"""Policy-first routing for inbound IMAP messages before ticket automation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import CompanyProfile, InboundIntake, Message, Ticket
from .audit import record_audit
from .groq import GroqDecision, decide_inbound
from .ticketing import find_thread_ticket, process_inbound, queue_reply


@dataclass(frozen=True)
class IntakeResult:
    """A persisted intake decision used by IMAP before a message is marked seen."""

    disposition: str
    created: bool
    ticket: Ticket | None = None
    duplicate: bool = False


def _profile() -> CompanyProfile | None:
    return CompanyProfile.current()


def _complete(profile: CompanyProfile | None) -> bool:
    return bool(profile and profile.to_dict()["isComplete"])


def _receipt(
    parsed: dict[str, Any],
    *,
    source: str,
    mailbox_uid: str | None,
    disposition: str,
    confidence: float | None,
    classification_source: str,
    model: str | None,
    reason: str,
    profile: CompanyProfile | None,
    ticket: Ticket | None = None,
) -> InboundIntake:
    receipt = InboundIntake(
        rfc_message_id=str(parsed["message_id"]),
        source=source,
        mailbox_uid=mailbox_uid,
        sender=str(parsed.get("sender") or "unknown@example.invalid"),
        subject=str(parsed.get("subject") or "(no subject)"),
        disposition=disposition,
        confidence=confidence,
        classification_source=classification_source,
        model=model,
        reason=reason[:500],
        company_profile_version=profile.version if profile else None,
        was_human_eligible=bool(parsed.get("is_human_ack_eligible", False)),
        ticket_id=ticket.id if ticket else None,
    )
    db.session.add(receipt)
    return receipt


def _commit_decision(action: str, receipt: InboundIntake, ticket: Ticket | None = None) -> None:
    record_audit(
        action,
        ticket=ticket,
        actor_type="automation",
        details={
            "intakeId": receipt.id,
            "disposition": receipt.disposition,
            "confidence": receipt.confidence,
            "source": receipt.classification_source,
            "model": receipt.model,
            "companyProfileVersion": receipt.company_profile_version,
        },
    )
    db.session.commit()


def _review_ticket(
    parsed: dict[str, Any], *, source: str, mailbox_uid: str | None
) -> tuple[Ticket, bool]:
    ticket, _, created = process_inbound(
        parsed,
        source=source,
        mailbox_uid=mailbox_uid,
        allow_automation=False,
    )
    return ticket, created


def _threshold(profile: CompanyProfile) -> float:
    """Use the reviewed company policy; the global setting is its creation default."""

    return float(profile.minimum_customer_confidence)


def _queue_ai_reply(ticket: Ticket, decision: GroqDecision, profile: CompanyProfile) -> bool:
    if not profile.auto_reply_enabled or not decision.reply_body:
        return False
    # Import locally to keep intake policy independent from SMTP implementation.
    from .mail import request_outbox_delivery

    try:
        outbound = queue_reply(
            ticket,
            subject=decision.reply_subject or f"Re: {ticket.subject}",
            body=decision.reply_body,
            source="ai_automation",
            automated=True,
            status_after_send="awaiting_customer_reply",
            commit=False,
        )
        record_audit(
            "ai_reply_queued",
            ticket=ticket,
            actor_type="automation",
            details={
                "messageId": outbound.rfc_message_id,
                "model": decision.model,
                "confidence": decision.confidence,
                "companyProfileVersion": profile.version,
            },
        )
        # The reply, outbox row, and audit event must be durable before the
        # scheduler is asked to deliver it immediately.
        db.session.commit()
        request_outbox_delivery()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unable to queue AI reply for ticket %s", ticket.reference)
        return False


def _recovery_parsed_message(message: Message, receipt: InboundIntake) -> dict[str, Any]:
    """Rebuild the minimum trusted intake shape from a durable inbound message."""

    return {
        "message_id": message.rfc_message_id,
        "sender": message.sender,
        "recipients": list(message.recipients or []),
        "cc": list(message.cc or []),
        "subject": message.subject,
        "body_text": message.body_text,
        "is_human_ack_eligible": receipt.was_human_eligible,
    }


def _apply_recovery_decision(
    receipt: InboundIntake,
    decision: GroqDecision,
    *,
    disposition: str,
    profile: CompanyProfile,
    action: str,
) -> None:
    """Persist a fresh Groq decision without creating a second inbound message."""

    receipt.disposition = disposition
    receipt.confidence = decision.confidence
    receipt.classification_source = decision.source
    receipt.model = decision.model
    receipt.reason = decision.reason[:500]
    receipt.company_profile_version = profile.version
    record_audit(
        action,
        ticket=receipt.ticket,
        actor_type="automation",
        details={
            "intakeId": receipt.id,
            "disposition": receipt.disposition,
            "confidence": receipt.confidence,
            "source": receipt.classification_source,
            "model": receipt.model,
            "companyProfileVersion": receipt.company_profile_version,
        },
    )


def _skip_groq_recovery(receipt: InboundIntake, reason: str) -> None:
    """Keep a malformed or already-answered receipt in human review permanently."""

    receipt.classification_source = "groq-recovery-skipped"
    receipt.reason = reason[:500]
    record_audit(
        "inbound_groq_recovery_skipped",
        ticket=receipt.ticket,
        actor_type="automation",
        details={"intakeId": receipt.id},
    )
    db.session.commit()


def recover_groq_error_intakes(limit: int = 25) -> dict[str, int]:
    """Retry only provider-error receipts that cannot already have a reply.

    IMAP marks a safely persisted provider failure as seen so it cannot loop
    forever. This job gives that terminal review state a bounded recovery path
    once the provider is healthy again, while retaining human review for low
    confidence decisions and avoiding any ticket that already has an outbound
    message.
    """

    result = {
        "examined": 0,
        "replied": 0,
        "promotional": 0,
        "review": 0,
        "skipped": 0,
        "provider_unavailable": 0,
        "queue_failed": 0,
        "errors": 0,
    }
    profile = _profile()
    if profile is None or not _complete(profile) or not profile.auto_reply_enabled:
        return result

    receipts = (
        InboundIntake.query.filter(
            InboundIntake.disposition == "needs_review",
            InboundIntake.classification_source == "groq-error",
            InboundIntake.was_human_eligible.is_(True),
        )
        .order_by(InboundIntake.created_at.asc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    for receipt in receipts:
        result["examined"] += 1
        try:
            ticket = receipt.ticket
            inbound = Message.query.filter_by(
                rfc_message_id=receipt.rfc_message_id,
                direction="inbound",
                ticket_id=receipt.ticket_id,
            ).first()
            if ticket is None or inbound is None:
                _skip_groq_recovery(receipt, "The original inbound message or ticket is unavailable for safe AI recovery.")
                result["skipped"] += 1
                continue
            if Message.query.filter_by(ticket_id=ticket.id, direction="outbound").first():
                _skip_groq_recovery(receipt, "A reply already exists on this ticket; automated recovery was not duplicated.")
                result["skipped"] += 1
                continue

            decision = decide_inbound(profile, _recovery_parsed_message(inbound, receipt))
            if decision.error:
                # Leave the original groq-error receipt intact for the next bounded retry.
                result["provider_unavailable"] += 1
                continue

            if decision.disposition == "customer_request" and decision.confidence >= _threshold(profile):
                _apply_recovery_decision(
                    receipt,
                    decision,
                    disposition="customer_request",
                    profile=profile,
                    action="inbound_groq_recovered_customer",
                )
                if _queue_ai_reply(ticket, decision, profile):
                    result["replied"] += 1
                else:
                    result["queue_failed"] += 1
                continue

            if decision.disposition == "promotional" and decision.confidence >= _threshold(profile):
                _apply_recovery_decision(
                    receipt,
                    decision,
                    disposition="promotional",
                    profile=profile,
                    action="inbound_groq_recovered_promotional",
                )
                db.session.commit()
                result["promotional"] += 1
                continue

            _apply_recovery_decision(
                receipt,
                decision,
                disposition="needs_review",
                profile=profile,
                action="inbound_groq_recovered_review",
            )
            db.session.commit()
            result["review"] += 1
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Unable to recover Groq-error intake %s", receipt.id)
            result["errors"] += 1
    return result


def route_inbound(
    parsed: dict[str, Any], *, source: str = "imap", mailbox_uid: str | None = None
) -> IntakeResult:
    """Persist a promotion/customer/review decision before IMAP marks a UID seen.

    Existing threads remain tickets but never trigger another automated reply. New
    mail is fail-closed: unavailable AI, low confidence, and incomplete company
    setup become review tickets rather than promotions or automatic replies.
    """

    message_id = str(parsed["message_id"])
    existing_receipt = InboundIntake.query.filter_by(rfc_message_id=message_id).first()
    if existing_receipt:
        return IntakeResult(
            disposition=existing_receipt.disposition,
            created=False,
            ticket=existing_receipt.ticket,
            duplicate=True,
        )

    existing_message = Message.query.filter_by(rfc_message_id=message_id).first()
    if existing_message:
        receipt = _receipt(
            parsed,
            source=source,
            mailbox_uid=mailbox_uid,
            disposition="duplicate",
            confidence=1.0,
            classification_source="message-id",
            model=None,
            reason="A persisted ticket message already uses this Message-ID.",
            profile=_profile(),
            ticket=existing_message.ticket,
        )
        db.session.flush()
        _commit_decision("inbound_duplicate_recorded", receipt, existing_message.ticket)
        return IntakeResult("duplicate", False, existing_message.ticket, duplicate=True)

    threaded_ticket = find_thread_ticket(parsed)
    if threaded_ticket:
        ticket, created = _review_ticket(parsed, source=source, mailbox_uid=mailbox_uid)
        receipt = _receipt(
            parsed,
            source=source,
            mailbox_uid=mailbox_uid,
            disposition="threaded",
            confidence=1.0,
            classification_source="threading",
            model=None,
            reason="Message belongs to an existing customer conversation.",
            profile=_profile(),
            ticket=ticket,
        )
        db.session.flush()
        _commit_decision("inbound_thread_routed", receipt, ticket)
        return IntakeResult("threaded", created, ticket)

    if not bool(parsed.get("is_human_ack_eligible", False)):
        receipt = _receipt(
            parsed,
            source=source,
            mailbox_uid=mailbox_uid,
            disposition="suppressed",
            confidence=1.0,
            classification_source="header-policy",
            model=None,
            reason="Automated, bulk, malformed, or support-owned sender policy suppressed automation.",
            profile=_profile(),
        )
        db.session.flush()
        _commit_decision("inbound_suppressed", receipt)
        return IntakeResult("suppressed", False)

    profile = _profile()
    if not _complete(profile):
        ticket, created = _review_ticket(parsed, source=source, mailbox_uid=mailbox_uid)
        receipt = _receipt(
            parsed,
            source=source,
            mailbox_uid=mailbox_uid,
            disposition="needs_review",
            confidence=None,
            classification_source="profile-required",
            model=None,
            reason="Company profile setup is incomplete; a human must review this message.",
            profile=profile,
            ticket=ticket,
        )
        db.session.flush()
        _commit_decision("inbound_review_required", receipt, ticket)
        return IntakeResult("needs_review", created, ticket)

    decision = decide_inbound(profile, parsed)
    if decision.disposition == "promotional" and decision.confidence >= _threshold(profile):
        receipt = _receipt(
            parsed,
            source=source,
            mailbox_uid=mailbox_uid,
            disposition="promotional",
            confidence=decision.confidence,
            classification_source=decision.source,
            model=decision.model,
            reason=decision.reason,
            profile=profile,
        )
        db.session.flush()
        _commit_decision("inbound_promotion_suppressed", receipt)
        return IntakeResult("promotional", False)

    if decision.disposition != "customer_request" or decision.confidence < _threshold(profile):
        ticket, created = _review_ticket(parsed, source=source, mailbox_uid=mailbox_uid)
        receipt = _receipt(
            parsed,
            source=source,
            mailbox_uid=mailbox_uid,
            disposition="needs_review",
            confidence=decision.confidence,
            classification_source=decision.source,
            model=decision.model,
            reason=decision.reason,
            profile=profile,
            ticket=ticket,
        )
        db.session.flush()
        _commit_decision("inbound_ai_review_required", receipt, ticket)
        return IntakeResult("needs_review", created, ticket)

    ticket, created = _review_ticket(parsed, source=source, mailbox_uid=mailbox_uid)
    receipt = _receipt(
        parsed,
        source=source,
        mailbox_uid=mailbox_uid,
        disposition="customer_request",
        confidence=decision.confidence,
        classification_source=decision.source,
        model=decision.model,
        reason=decision.reason,
        profile=profile,
        ticket=ticket,
    )
    db.session.flush()
    _commit_decision("inbound_customer_request_routed", receipt, ticket)
    _queue_ai_reply(ticket, decision, profile)
    return IntakeResult("customer_request", created, ticket)
