"""Ticket lifecycle, email threading, attachment persistence, and outbox staging."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from flask import current_app
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import Attachment, Category, Customer, Message, OutboundDelivery, Ticket, User, utcnow
from .audit import record_audit
from .response_builder import acknowledgement_template, render_template, select_auto_template
from .triage import triage

MESSAGE_ID_PATTERN = re.compile(r"<[^<>\s]+>")
TICKET_REFERENCE_PATTERN = re.compile(r"\b(SUP-[A-Z0-9-]+)\b", re.IGNORECASE)


def _header_message_ids(value: str | None) -> list[str]:
    if not value:
        return []
    found = MESSAGE_ID_PATTERN.findall(value)
    return found or [value.strip()]


def _reference() -> str:
    return f"SUP-{utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def _message_id() -> str:
    domain = current_app.config["MAIL_FROM"].partition("@")[2] or "support.local"
    return f"<support-{uuid.uuid4().hex}@{domain}>"


def _find_thread_ticket(parsed: dict[str, Any]) -> Ticket | None:
    """Resolve a conversation using RFC threading headers before weaker subject fallback."""

    candidates = _header_message_ids(parsed.get("in_reply_to")) + _header_message_ids(parsed.get("references"))
    for message_id in reversed(candidates):
        previous = Message.query.filter_by(rfc_message_id=message_id).first()
        if previous:
            return previous.ticket

    subject = str(parsed.get("subject", ""))
    reference_match = TICKET_REFERENCE_PATTERN.search(subject)
    if reference_match:
        return Ticket.query.filter_by(reference=reference_match.group(1).upper()).first()
    return None


def find_thread_ticket(parsed: dict[str, Any]) -> Ticket | None:
    """Expose RFC conversation lookup to the pre-ticket intake router."""

    return _find_thread_ticket(parsed)


def _find_or_create_customer(email: str, full_name: str | None = None) -> Customer:
    normalized_email = email.strip().lower()
    customer = Customer.query.filter_by(email=normalized_email).first()
    if customer is None:
        customer = Customer(email=normalized_email, full_name=(full_name or "").strip() or None)
        db.session.add(customer)
        db.session.flush()
    elif full_name and not customer.full_name:
        customer.full_name = full_name.strip()
    return customer


def _persist_attachments(message: Message, attachments: list[dict[str, Any]]) -> int:
    storage_root = Path(current_app.config["ATTACHMENT_DIR"])
    ticket_directory = storage_root / message.ticket.reference
    ticket_directory.mkdir(parents=True, exist_ok=True)
    saved = 0
    limit = current_app.config["MAX_CONTENT_LENGTH"]

    for item in attachments:
        data = item.get("data", b"")
        if not isinstance(data, bytes) or len(data) > limit:
            current_app.logger.warning("Skipping oversized or invalid attachment on ticket %s", message.ticket.reference)
            continue
        safe_name = secure_filename(str(item.get("filename") or "attachment.bin")) or "attachment.bin"
        stored_name = f"{uuid.uuid4().hex}_{safe_name}"
        destination = ticket_directory / stored_name
        destination.write_bytes(data)
        db.session.add(
            Attachment(
                message_id=message.id,
                original_name=str(item.get("filename") or safe_name),
                stored_name=stored_name,
                storage_path=str(destination.relative_to(storage_root)),
                content_type=str(item.get("content_type") or "application/octet-stream"),
                size_bytes=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
            )
        )
        saved += 1
    return saved


def _create_ticket(parsed: dict[str, Any], source: str) -> Ticket:
    active_categories = Category.query.filter_by(is_active=True).all()
    outcome = triage(str(parsed["subject"]), str(parsed["body_text"]), active_categories)
    category = outcome["category"]
    customer = _find_or_create_customer(str(parsed["sender"]), str(parsed.get("sender_name") or ""))
    due_at = utcnow() + timedelta(hours=category.default_sla_hours) if category else None
    ticket = Ticket(
        reference=_reference(),
        subject=str(parsed["subject"]),
        customer_id=customer.id,
        category_id=category.id if category else None,
        priority=str(outcome["priority"]),
        category_confidence=float(outcome["confidence"]),
        origin=source,
        due_at=due_at,
        last_customer_message_at=utcnow(),
    )
    db.session.add(ticket)
    db.session.flush()
    record_audit(
        "ticket_created",
        ticket=ticket,
        actor_type="automation",
        new_value={
            "reference": ticket.reference,
            "category": category.slug if category else None,
            "priority": ticket.priority,
            "confidence": ticket.category_confidence,
            "classificationSource": outcome["source"],
        },
    )
    return ticket


def process_inbound(
    parsed: dict[str, Any],
    *,
    source: str = "imap",
    mailbox_uid: str | None = None,
    allow_automation: bool = True,
) -> tuple[Ticket, Message, bool]:
    """Commit an inbound message once and safely enqueue any permitted response.

    The caller must only mark the IMAP message as seen after this function returns.
    RFC Message-ID uniqueness makes IMAP retry cycles idempotent.
    """

    existing = Message.query.filter_by(rfc_message_id=str(parsed["message_id"])).first()
    if existing:
        return existing.ticket, existing, False

    ticket = _find_thread_ticket(parsed)
    new_ticket = ticket is None
    if new_ticket:
        ticket = _create_ticket(parsed, source)
    else:
        assert ticket is not None
        old_status = ticket.status
        if ticket.status in {"resolved", "closed", "awaiting_customer_reply"}:
            ticket.status = "open"
            ticket.resolved_at = None
            record_audit(
                "ticket_reopened",
                ticket=ticket,
                actor_type="automation",
                old_value={"status": old_status},
                new_value={"status": "open"},
            )

    message = Message(
        ticket_id=ticket.id,
        direction="inbound",
        sender=str(parsed["sender"]),
        recipients=list(parsed.get("recipients") or []),
        cc=list(parsed.get("cc") or []),
        subject=str(parsed["subject"]),
        body_text=str(parsed.get("body_text") or ""),
        body_html=parsed.get("body_html"),
        rfc_message_id=str(parsed["message_id"]),
        in_reply_to=parsed.get("in_reply_to"),
        references_header=parsed.get("references"),
        raw_headers=parsed.get("headers"),
        source=source,
        mailbox_uid=mailbox_uid,
        delivery_status="received",
    )
    db.session.add(message)
    db.session.flush()
    attachment_count = _persist_attachments(message, list(parsed.get("attachments") or []))
    ticket.last_customer_message_at = message.created_at
    record_audit(
        "message_received",
        ticket=ticket,
        actor_type="automation",
        details={"messageId": message.rfc_message_id, "attachments": attachment_count, "source": source},
    )
    db.session.commit()

    # Keep the durable inbound commit independent of nonessential automated responses.
    # Only parser-confirmed customer-eligible mail reaches the auto-reply policy.
    if allow_automation and bool(parsed.get("is_human_ack_eligible", False)):
        queue_automatic_responses(ticket, message, new_ticket)
    return ticket, message, new_ticket


def queue_reply(
    ticket: Ticket,
    *,
    subject: str,
    body: str,
    actor: User | None = None,
    source: str = "agent",
    template_id: int | None = None,
    automated: bool = False,
    status_after_send: str | None = "awaiting_customer_reply",
    commit: bool = True,
) -> Message:
    """Create an outbound message plus durable outbox row; no SMTP call happens here."""

    if not body.strip():
        raise ValueError("A reply body is required.")
    latest_inbound = (
        Message.query.filter_by(ticket_id=ticket.id, direction="inbound")
        .order_by(Message.created_at.desc())
        .first()
    )
    in_reply_to = latest_inbound.rfc_message_id if latest_inbound else None
    previous_refs = latest_inbound.references_header if latest_inbound else None
    references = " ".join(item for item in [previous_refs, in_reply_to] if item) or None
    message = Message(
        ticket_id=ticket.id,
        direction="outbound",
        sender=current_app.config["MAIL_FROM"],
        recipients=[ticket.customer.email],
        cc=[],
        subject=subject.strip() or f"Re: {ticket.subject}",
        body_text=body.strip(),
        rfc_message_id=_message_id(),
        in_reply_to=in_reply_to,
        references_header=references,
        source=source,
        delivery_status="queued",
        automation_source="template" if template_id else ("automation" if automated else None),
    )
    db.session.add(message)
    db.session.flush()
    db.session.add(
        OutboundDelivery(
            outbound_message_id=message.id,
            status="queued",
            status_after_send=status_after_send,
        )
    )
    record_audit(
        "reply_queued",
        ticket=ticket,
        actor=actor,
        actor_type="automation" if automated else None,
        details={"messageId": message.rfc_message_id, "templateId": template_id, "source": source},
    )
    if commit:
        db.session.commit()
    return message


def queue_automatic_responses(ticket: Ticket, inbound: Message, new_ticket: bool) -> list[Message]:
    """Queue only approved policy responses and leave low-confidence mail for agents."""

    queued: list[Message] = []
    try:
        if new_ticket and current_app.config["AUTO_ACKNOWLEDGEMENT"]:
            acknowledgement = acknowledgement_template()
            if acknowledgement:
                rendered = render_template(acknowledgement, ticket)
                queued.append(
                    queue_reply(
                        ticket,
                        subject=rendered["subject"],
                        body=rendered["body"],
                        source="acknowledgement",
                        template_id=acknowledgement.id,
                        automated=True,
                        status_after_send=None,
                        commit=False,
                    )
                )

        # Automatic answers happen once for newly-created conversations and only from an approved template.
        if (
            new_ticket
            and ticket.category
            and ticket.category.automation_enabled
            and (ticket.category_confidence or 0) >= ticket.category.automation_confidence_threshold
        ):
            template = select_auto_template(ticket)
            if template:
                rendered = render_template(template, ticket)
                queued.append(
                    queue_reply(
                        ticket,
                        subject=rendered["subject"],
                        body=rendered["body"],
                        source="automation",
                        template_id=template.id,
                        automated=True,
                        status_after_send="resolved" if template.resolves_ticket else "awaiting_customer_reply",
                        commit=False,
                    )
                )
        if queued:
            db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Automatic response queueing failed for ticket %s", ticket.reference)
    return queued


def create_manual_ticket(payload: dict[str, Any], actor: User) -> tuple[Ticket, Message, bool]:
    """Create a dashboard-originated ticket without sending an accidental acknowledgement."""

    email = str(payload.get("customerEmail", "")).strip().lower()
    subject = str(payload.get("subject", "")).strip()
    body = str(payload.get("body", "")).strip()
    if not email or "@" not in email:
        raise ValueError("A valid customerEmail is required.")
    if not subject or not body:
        raise ValueError("subject and body are required.")
    parsed = {
        "sender": email,
        "sender_name": str(payload.get("customerName", "")),
        "recipients": [current_app.config["MAIL_FROM"]],
        "cc": [],
        "subject": subject,
        "body_text": body,
        "body_html": None,
        "message_id": f"<dashboard-{uuid.uuid4().hex}@support.local>",
        "in_reply_to": None,
        "references": None,
        "headers": {"X-Source": "dashboard"},
        "attachments": [],
        "is_automated": False,
    }
    ticket, message, created = process_inbound(parsed, source="dashboard", allow_automation=False)
    record_audit("ticket_created_by_agent", ticket=ticket, actor=actor, details={"messageId": message.rfc_message_id})
    db.session.commit()
    return ticket, message, created


def apply_ticket_change(ticket: Ticket, changes: dict[str, Any], actor: User) -> Ticket:
    """Apply validated mutable ticket fields and record their before/after state."""

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if "priority" in changes:
        priority = str(changes["priority"]).lower()
        if priority not in {"low", "normal", "high", "urgent"}:
            raise ValueError("Invalid priority.")
        before["priority"], ticket.priority = ticket.priority, priority
        after["priority"] = priority
    if "status" in changes:
        status = str(changes["status"]).lower()
        if status not in {"open", "in_progress", "awaiting_customer_reply", "resolved", "closed"}:
            raise ValueError("Invalid status.")
        before["status"], ticket.status = ticket.status, status
        after["status"] = status
        ticket.resolved_at = utcnow() if status in {"resolved", "closed"} else None
    if "categoryId" in changes:
        category = db.session.get(Category, int(changes["categoryId"])) if changes["categoryId"] else None
        if changes["categoryId"] and category is None:
            raise ValueError("Category not found.")
        before["categoryId"], ticket.category_id = ticket.category_id, category.id if category else None
        after["categoryId"] = ticket.category_id
        if category and ticket.due_at is None:
            ticket.due_at = utcnow() + timedelta(hours=category.default_sla_hours)
    if "assigneeId" in changes:
        assignee = db.session.get(User, int(changes["assigneeId"])) if changes["assigneeId"] else None
        if changes["assigneeId"] and (assignee is None or not assignee.is_active):
            raise ValueError("Assignee not found or inactive.")
        before["assigneeId"], ticket.assignee_id = ticket.assignee_id, assignee.id if assignee else None
        after["assigneeId"] = ticket.assignee_id
    if "subject" in changes:
        subject = str(changes["subject"]).strip()
        if not subject:
            raise ValueError("Subject cannot be empty.")
        before["subject"], ticket.subject = ticket.subject, subject
        after["subject"] = subject
    if after:
        record_audit("ticket_updated", ticket=ticket, actor=actor, old_value=before, new_value=after)
        db.session.commit()
    return ticket
