"""Durable outbox delivery through console or SMTP STARTTLS transports."""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import AuditLog, OutboundDelivery, Ticket, utcnow
from .audit import record_audit


def request_outbox_delivery() -> None:
    """Wake the durable outbox job after a reply has committed successfully.

    This is only a scheduler hint: if no local scheduler is running, the queued
    delivery remains durable and the normal interval/external worker can claim it.
    SMTP is still performed exclusively by ``deliver_pending``.
    """

    scheduler = current_app.extensions.get("support_scheduler")
    if not scheduler or not scheduler.running:
        return
    try:
        scheduler.modify_job("outbox-delivery", next_run_time=datetime.now(timezone.utc))
        scheduler.wakeup()
    except Exception:
        current_app.logger.warning("Unable to wake the durable outbox delivery job", exc_info=True)


def _email_from_delivery(delivery: OutboundDelivery) -> EmailMessage:
    message = delivery.outbound_message
    email = EmailMessage()
    email["From"] = formataddr((current_app.config["MAIL_FROM_NAME"], current_app.config["MAIL_FROM"]))
    email["To"] = ", ".join(message.recipients or [])
    if message.cc:
        email["Cc"] = ", ".join(message.cc)
    email["Subject"] = message.subject
    email["Message-ID"] = message.rfc_message_id
    if message.in_reply_to:
        email["In-Reply-To"] = message.in_reply_to
    if message.references_header:
        email["References"] = message.references_header
    if message.source in {"acknowledgement", "automation", "ai_automation"}:
        email["Auto-Submitted"] = "auto-replied"
    email.set_content(message.body_text)
    return email


def _send(delivery: OutboundDelivery) -> None:
    transport = current_app.config["MAIL_TRANSPORT"]
    message = delivery.outbound_message
    if transport == "console":
        current_app.logger.info(
            "Console mail delivered: ticket=%s to=%s subject=%r",
            message.ticket.reference,
            ",".join(message.recipients or []),
            message.subject,
        )
        return
    if transport != "smtp":
        raise RuntimeError("MAIL_TRANSPORT must be configured as 'smtp' or 'console'.")
    if not current_app.config["SMTP_HOST"]:
        raise RuntimeError("SMTP_HOST is required when MAIL_TRANSPORT=smtp.")

    email = _email_from_delivery(delivery)
    recipients = list(message.recipients or []) + list(message.cc or [])
    if not recipients:
        raise RuntimeError("Outbound message has no recipient.")
    with smtplib.SMTP(
        current_app.config["SMTP_HOST"],
        current_app.config["SMTP_PORT"],
        timeout=current_app.config["SMTP_TIMEOUT_SECONDS"],
    ) as smtp:
        smtp.ehlo()
        if current_app.config["SMTP_USE_STARTTLS"]:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if current_app.config["SMTP_USERNAME"]:
            smtp.login(current_app.config["SMTP_USERNAME"], current_app.config["SMTP_PASSWORD"])
        smtp.send_message(email, from_addr=current_app.config["MAIL_FROM"], to_addrs=recipients)


def _mark_sent(delivery: OutboundDelivery) -> None:
    message = delivery.outbound_message
    ticket = message.ticket
    now = utcnow()
    delivery.status = "sent"
    delivery.sent_at = now
    delivery.last_error = None
    message.delivery_status = "sent"
    if ticket.first_response_at is None:
        ticket.first_response_at = now
    if delivery.status_after_send:
        old_status = ticket.status
        ticket.status = delivery.status_after_send
        if ticket.status in {"resolved", "closed"}:
            ticket.resolved_at = now
        record_audit(
            "ticket_status_changed_after_delivery",
            ticket=ticket,
            actor_type="automation",
            old_value={"status": old_status},
            new_value={"status": ticket.status},
            details={"deliveryId": delivery.id},
        )
    record_audit(
        "reply_sent",
        ticket=ticket,
        actor_type="automation",
        details={"deliveryId": delivery.id, "messageId": message.rfc_message_id, "transport": current_app.config["MAIL_TRANSPORT"]},
    )
    db.session.commit()


def _mark_failed(delivery: OutboundDelivery, error: Exception) -> None:
    delivery.last_error = str(error)[:2000]
    message = delivery.outbound_message
    max_attempts = current_app.config["OUTBOX_MAX_ATTEMPTS"]
    if delivery.attempts >= max_attempts:
        delivery.status = "failed"
        message.delivery_status = "failed"
    else:
        delivery.status = "queued"
        # Exponential backoff caps at 30 minutes while allowing transient SMTP recovery.
        delivery.not_before = utcnow() + timedelta(seconds=min(1800, 30 * (2 ** max(0, delivery.attempts - 1))))
        message.delivery_status = "retrying"
    record_audit(
        "reply_delivery_failed",
        ticket=message.ticket,
        actor_type="automation",
        details={"deliveryId": delivery.id, "attempts": delivery.attempts, "error": delivery.last_error},
    )
    db.session.commit()


def deliver_pending(limit: int = 25) -> dict[str, int]:
    """Send due outbox entries. Safe to call from the scheduler or CLI."""

    due = (
        OutboundDelivery.query.filter(
            OutboundDelivery.status == "queued",
            OutboundDelivery.not_before <= utcnow(),
        )
        .order_by(OutboundDelivery.queued_at.asc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    result = {"processed": 0, "sent": 0, "failed": 0}
    for delivery in due:
        # Claim first so another worker sees this record as unavailable before SMTP starts.
        delivery.status = "sending"
        delivery.attempts += 1
        db.session.commit()
        result["processed"] += 1
        try:
            _send(delivery)
            _mark_sent(delivery)
            result["sent"] += 1
        except Exception as error:
            current_app.logger.exception("Outbox delivery %s failed", delivery.id)
            _mark_failed(delivery, error)
            result["failed"] += 1
    return result


def escalate_overdue_tickets() -> dict[str, int]:
    """Escalate only once per ticket after its SLA deadline expires."""

    overdue = Ticket.query.filter(
        Ticket.due_at.isnot(None),
        Ticket.due_at < utcnow(),
        Ticket.status.notin_(["resolved", "closed"]),
    ).all()
    escalated = 0
    for ticket in overdue:
        existing = AuditLog.query.filter_by(ticket_id=ticket.id, action="sla_breached").first()
        if existing:
            continue
        previous_priority = ticket.priority
        if ticket.priority != "urgent":
            ticket.priority = "urgent"
        record_audit(
            "sla_breached",
            ticket=ticket,
            actor_type="automation",
            old_value={"priority": previous_priority},
            new_value={"priority": ticket.priority},
            details={"dueAt": ticket.due_at.isoformat() if ticket.due_at else None},
        )
        escalated += 1
    if escalated:
        db.session.commit()
    return {"overdue": len(overdue), "escalated": escalated}
