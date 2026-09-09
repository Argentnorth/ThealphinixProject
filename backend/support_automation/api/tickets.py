"""Ticket queue, conversation, reply, and attachment endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy import or_

from ..extensions import db
from ..models import Attachment, Category, Customer, Message, Template, Ticket, User
from ..security import current_user, roles_required
from ..services.audit import record_audit
from ..services.response_builder import generate_draft, get_template as find_template, render_template
from ..services.ticketing import apply_ticket_change, create_manual_ticket, queue_reply

tickets_bp = Blueprint("tickets", __name__, url_prefix="/api/v1/tickets")
attachments_bp = Blueprint("attachments", __name__, url_prefix="/api/v1/attachments")


VALID_STATUSES = {"open", "in_progress", "awaiting_customer_reply", "resolved", "closed"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


def _ticket_or_404(ticket_id: int) -> Ticket | None:
    return db.session.get(Ticket, ticket_id)


def _can_edit(ticket: Ticket) -> bool:
    user = current_user()
    return user.role == "admin" or ticket.assignee_id in {None, user.id}


def _forbidden_assignment_response():
    return jsonify(error={"code": "forbidden", "message": "Ticket is assigned to another agent."}), 403


def _optional_int(value: str | None) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _page_size() -> tuple[int, int]:
    page = max(1, _optional_int(request.args.get("page")) or 1)
    per_page = min(100, max(1, _optional_int(request.args.get("perPage")) or 25))
    return page, per_page


@tickets_bp.get("")
@roles_required("admin", "agent")
def list_tickets():
    query = Ticket.query
    statuses = [value.strip().lower() for value in request.args.get("status", "").split(",") if value.strip()]
    priorities = [value.strip().lower() for value in request.args.get("priority", "").split(",") if value.strip()]
    if statuses:
        query = query.filter(Ticket.status.in_([status for status in statuses if status in VALID_STATUSES]))
    if priorities:
        query = query.filter(Ticket.priority.in_([priority for priority in priorities if priority in VALID_PRIORITIES]))
    category_id = _optional_int(request.args.get("categoryId"))
    if category_id:
        query = query.filter(Ticket.category_id == category_id)
    assignee = request.args.get("assignee")
    if assignee == "me":
        query = query.filter(Ticket.assignee_id == current_user().id)
    elif assignee == "unassigned":
        query = query.filter(Ticket.assignee_id.is_(None))
    elif _optional_int(assignee):
        query = query.filter(Ticket.assignee_id == _optional_int(assignee))
    if request.args.get("sla") == "breached":
        from ..models import utcnow

        query = query.filter(Ticket.due_at.isnot(None), Ticket.due_at < utcnow(), Ticket.status.notin_(["resolved", "closed"]))
    search = request.args.get("search", "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.outerjoin(Customer).filter(
            or_(
                Ticket.reference.ilike(pattern),
                Ticket.subject.ilike(pattern),
                Customer.email.ilike(pattern),
                Customer.full_name.ilike(pattern),
            )
        )

    sort = request.args.get("sort", "newest")
    if sort == "oldest":
        query = query.order_by(Ticket.created_at.asc())
    elif sort == "due":
        query = query.order_by(Ticket.due_at.asc().nullslast(), Ticket.created_at.asc())
    elif sort == "priority":
        # Database-independent priority order with deterministic latest-ticket tiebreaking.
        from sqlalchemy import case

        query = query.order_by(
            case({"urgent": 0, "high": 1, "normal": 2, "low": 3}, value=Ticket.priority, else_=4),
            Ticket.created_at.desc(),
        )
    else:
        query = query.order_by(Ticket.updated_at.desc())

    page, per_page = _page_size()
    result = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(
        items=[ticket.to_dict() for ticket in result.items],
        pagination={"page": result.page, "perPage": result.per_page, "total": result.total, "pages": result.pages},
    )


@tickets_bp.post("")
@roles_required("admin", "agent")
def create_ticket():
    try:
        ticket, message, _ = create_manual_ticket(request.get_json(silent=True) or {}, current_user())
    except ValueError as error:
        return jsonify(error={"code": "validation_error", "message": str(error)}), 400
    return jsonify(ticket=ticket.to_dict(include_messages=True), message=message.to_dict()), 201


@tickets_bp.get("/<int:ticket_id>")
@roles_required("admin", "agent")
def get_ticket(ticket_id: int):
    ticket = _ticket_or_404(ticket_id)
    if not ticket:
        return jsonify(error={"code": "not_found", "message": "Ticket not found."}), 404
    return jsonify(ticket=ticket.to_dict(include_messages=True, include_audit=current_user().role == "admin"))


@tickets_bp.patch("/<int:ticket_id>")
@roles_required("admin", "agent")
def update_ticket(ticket_id: int):
    ticket = _ticket_or_404(ticket_id)
    if not ticket:
        return jsonify(error={"code": "not_found", "message": "Ticket not found."}), 404
    if not _can_edit(ticket):
        return _forbidden_assignment_response()
    try:
        updated = apply_ticket_change(ticket, request.get_json(silent=True) or {}, current_user())
    except ValueError as error:
        return jsonify(error={"code": "validation_error", "message": str(error)}), 400
    return jsonify(ticket=updated.to_dict())


@tickets_bp.post("/<int:ticket_id>/assignment")
@roles_required("admin", "agent")
def assign_ticket(ticket_id: int):
    ticket = _ticket_or_404(ticket_id)
    if not ticket:
        return jsonify(error={"code": "not_found", "message": "Ticket not found."}), 404
    payload = request.get_json(silent=True) or {}
    target_id = _optional_int(str(payload.get("assigneeId", "")))
    actor = current_user()
    if actor.role != "admin" and target_id != actor.id:
        return jsonify(error={"code": "forbidden", "message": "Agents may assign tickets only to themselves."}), 403
    target = db.session.get(User, target_id) if target_id else None
    if target_id and (target is None or not target.is_active):
        return jsonify(error={"code": "validation_error", "message": "Assignee must be an active user."}), 400
    old_id = ticket.assignee_id
    ticket.assignee_id = target.id if target else None
    record_audit(
        "ticket_assignment_changed",
        ticket=ticket,
        actor=actor,
        old_value={"assigneeId": old_id},
        new_value={"assigneeId": ticket.assignee_id},
    )
    db.session.commit()
    return jsonify(ticket=ticket.to_dict())


@tickets_bp.post("/<int:ticket_id>/reply")
@roles_required("admin", "agent")
def reply_to_ticket(ticket_id: int):
    ticket = _ticket_or_404(ticket_id)
    if not ticket:
        return jsonify(error={"code": "not_found", "message": "Ticket not found."}), 404
    if not _can_edit(ticket):
        return _forbidden_assignment_response()
    payload: dict[str, Any] = request.get_json(silent=True) or {}
    template_id = _optional_int(str(payload.get("templateId", "")))
    template: Template | None = find_template(template_id)
    if template_id and (not template or not template.is_active or not template.is_approved):
        return jsonify(error={"code": "validation_error", "message": "Selected template is unavailable."}), 400
    rendered = render_template(template, ticket) if template else None
    subject = str(payload.get("subject") or (rendered["subject"] if rendered else f"Re: {ticket.subject}"))
    body = str(payload.get("body") or (rendered["body"] if rendered else ""))
    status_after_send = str(payload.get("statusAfterSend", "awaiting_customer_reply")).lower()
    if status_after_send not in VALID_STATUSES | {""}:
        return jsonify(error={"code": "validation_error", "message": "Invalid statusAfterSend."}), 400
    try:
        outbound = queue_reply(
            ticket,
            subject=subject,
            body=body,
            actor=current_user(),
            source="template" if template else "agent",
            template_id=template.id if template else None,
            status_after_send=status_after_send or None,
        )
    except ValueError as error:
        return jsonify(error={"code": "validation_error", "message": str(error)}), 400
    return jsonify(message=outbound.to_dict(), delivery=outbound.delivery.to_dict() if outbound.delivery else None), 202


@tickets_bp.post("/<int:ticket_id>/draft")
@roles_required("admin", "agent")
def draft_reply(ticket_id: int):
    ticket = _ticket_or_404(ticket_id)
    if not ticket:
        return jsonify(error={"code": "not_found", "message": "Ticket not found."}), 404
    if not _can_edit(ticket):
        return _forbidden_assignment_response()
    payload = request.get_json(silent=True) or {}
    instruction = str(payload.get("instruction", ""))[:1000]
    draft = generate_draft(ticket, instruction)
    record_audit("reply_draft_generated", ticket=ticket, actor=current_user(), details={"source": draft["source"]})
    db.session.commit()
    return jsonify(draft=draft)


@tickets_bp.get("/<int:ticket_id>/messages")
@roles_required("admin", "agent")
def list_messages(ticket_id: int):
    ticket = _ticket_or_404(ticket_id)
    if not ticket:
        return jsonify(error={"code": "not_found", "message": "Ticket not found."}), 404
    return jsonify(items=[message.to_dict() for message in ticket.messages])


@attachments_bp.get("/<int:attachment_id>/download")
@roles_required("admin", "agent")
def download_attachment(attachment_id: int):
    attachment = db.session.get(Attachment, attachment_id)
    if not attachment:
        return jsonify(error={"code": "not_found", "message": "Attachment not found."}), 404
    storage_root = Path(current_app.config["ATTACHMENT_DIR"]).resolve()
    path = (storage_root / attachment.storage_path).resolve()
    if storage_root not in path.parents or not path.is_file():
        current_app.logger.warning("Attachment storage file is unavailable: %s", attachment.id)
        return jsonify(error={"code": "not_found", "message": "Attachment file is unavailable."}), 404
    return send_file(path, as_attachment=True, download_name=attachment.original_name, mimetype=attachment.content_type)
