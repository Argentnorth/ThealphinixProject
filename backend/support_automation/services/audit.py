"""Append-only audit event helpers."""

from __future__ import annotations

from typing import Any

from ..extensions import db
from ..models import AuditLog, Ticket, User


def record_audit(
    action: str,
    *,
    ticket: Ticket | None = None,
    actor: User | None = None,
    actor_type: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Stage an audit event in the current transaction.

    Callers commit their business change and its audit entry together, which prevents
    a ticket state mutation without a corresponding trail.
    """

    event = AuditLog(
        ticket_id=ticket.id if ticket else None,
        actor_user_id=actor.id if actor else None,
        actor_type=actor_type or ("user" if actor else "system"),
        action=action,
        old_value=old_value,
        new_value=new_value,
        details=details,
    )
    db.session.add(event)
    return event
