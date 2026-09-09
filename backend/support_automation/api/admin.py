"""Administrator-only user, operations, and audit endpoints."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ..extensions import db
from ..models import AuditLog, CompanyProfile, MailboxSyncState, User, USER_ROLES, utcnow
from ..security import current_user, roles_required
from ..services.audit import record_audit
from ..services.ingestion import poll_mailbox
from ..services.mail import deliver_pending, escalate_overdue_tickets

admin_bp = Blueprint("admin", __name__, url_prefix="/api/v1/admin")


def _page_values() -> tuple[int, int]:
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    try:
        per_page = min(100, max(1, int(request.args.get("perPage", "30"))))
    except ValueError:
        per_page = 30
    return page, per_page


@admin_bp.get("/users")
@roles_required("admin")
def list_users():
    return jsonify(items=[user.to_dict() for user in User.query.order_by(User.full_name.asc()).all()])


@admin_bp.post("/users")
@roles_required("admin")
def create_user():
    payload: dict[str, Any] = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    full_name = str(payload.get("fullName", "")).strip()
    password = str(payload.get("password", ""))
    role = str(payload.get("role", "agent")).lower()
    if not email or "@" not in email or not full_name or len(password) < 10:
        return jsonify(error={"code": "validation_error", "message": "email, fullName, and a 10+ character password are required."}), 400
    if role not in USER_ROLES:
        return jsonify(error={"code": "validation_error", "message": "Role must be admin or agent."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify(error={"code": "conflict", "message": "A user already uses this email."}), 409
    user = User(email=email, full_name=full_name, role=role, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    record_audit("user_created", actor=current_user(), details={"userId": user.id, "email": user.email, "role": user.role})
    db.session.commit()
    return jsonify(user=user.to_dict()), 201


@admin_bp.patch("/users/<int:user_id>")
@roles_required("admin")
def update_user(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify(error={"code": "not_found", "message": "User not found."}), 404
    payload: dict[str, Any] = request.get_json(silent=True) or {}
    if "fullName" in payload:
        name = str(payload["fullName"]).strip()
        if not name:
            return jsonify(error={"code": "validation_error", "message": "fullName cannot be empty."}), 400
        user.full_name = name
    if "role" in payload:
        role = str(payload["role"]).lower()
        if role not in USER_ROLES:
            return jsonify(error={"code": "validation_error", "message": "Role must be admin or agent."}), 400
        user.role = role
    if "isActive" in payload:
        active = bool(payload["isActive"])
        if user.id == current_user().id and not active:
            return jsonify(error={"code": "validation_error", "message": "You cannot deactivate your own account."}), 400
        user.is_active = active
    if payload.get("password"):
        password = str(payload["password"])
        if len(password) < 10:
            return jsonify(error={"code": "validation_error", "message": "Password must have at least 10 characters."}), 400
        user.set_password(password)
    record_audit("user_updated", actor=current_user(), details={"userId": user.id})
    db.session.commit()
    return jsonify(user=user.to_dict())


@admin_bp.get("/audit")
@roles_required("admin")
def audit_log():
    query = AuditLog.query
    try:
        ticket_id = int(request.args["ticketId"]) if request.args.get("ticketId") else None
    except ValueError:
        ticket_id = None
    if ticket_id:
        query = query.filter(AuditLog.ticket_id == ticket_id)
    action = request.args.get("action")
    if action:
        query = query.filter(AuditLog.action == action)
    page, per_page = _page_values()
    result = query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(
        items=[item.to_dict() for item in result.items],
        pagination={"page": result.page, "perPage": result.per_page, "total": result.total, "pages": result.pages},
    )


@admin_bp.get("/company-profile")
@roles_required("admin")
def company_profile():
    from ..services.groq import provider_status

    profile = CompanyProfile.current()
    return jsonify(
        profile=profile.to_dict() if profile else {
            "id": None,
            "companyName": "",
            "companyBrief": "",
            "tone": "clear and helpful",
            "autoReplyEnabled": False,
            "minimumCustomerConfidence": current_app.config["AI_CUSTOMER_CONFIDENCE_THRESHOLD"],
            "version": 0,
            "isComplete": False,
        },
        ai=provider_status(),
    )


@admin_bp.put("/company-profile")
@roles_required("admin")
def update_company_profile():
    from sqlalchemy.exc import IntegrityError

    from ..services.groq import provider_status

    payload: dict[str, Any] = request.get_json(silent=True) or {}
    company_name = str(payload.get("companyName", "")).strip()
    company_brief = str(payload.get("companyBrief", "")).strip()
    tone = str(payload.get("tone", "clear and helpful")).strip() or "clear and helpful"
    if not company_name or len(company_name) > 160:
        return jsonify(error={"code": "validation_error", "message": "companyName is required and must be 160 characters or fewer."}), 400
    if len(company_brief) < 20 or len(company_brief) > 4_000:
        return jsonify(error={"code": "validation_error", "message": "companyBrief must contain 20 to 4,000 characters."}), 400
    if len(tone) > 80:
        return jsonify(error={"code": "validation_error", "message": "tone must be 80 characters or fewer."}), 400
    raw_confidence = payload.get("minimumCustomerConfidence", current_app.config["AI_CUSTOMER_CONFIDENCE_THRESHOLD"])
    if isinstance(raw_confidence, bool):
        return jsonify(error={"code": "validation_error", "message": "minimumCustomerConfidence must be a number."}), 400
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        return jsonify(error={"code": "validation_error", "message": "minimumCustomerConfidence must be a number."}), 400
    if not 0.5 <= confidence <= 1:
        return jsonify(error={"code": "validation_error", "message": "minimumCustomerConfidence must be between 0.5 and 1."}), 400
    auto_reply_enabled = payload.get("autoReplyEnabled")
    if not isinstance(auto_reply_enabled, bool):
        return jsonify(error={"code": "validation_error", "message": "autoReplyEnabled must be a JSON boolean."}), 400
    ai = provider_status()
    if auto_reply_enabled and not ai["configurationReady"]:
        return jsonify(error={"code": "ai_not_ready", "message": "Enable Groq and configure a fresh server-side API key before enabling automatic AI replies."}), 409

    profile = CompanyProfile.current()
    actor_id = current_user().id
    created = False
    if profile is None:
        candidate = CompanyProfile(
            id=CompanyProfile.SINGLETON_ID,
            company_name=company_name,
            company_brief=company_brief,
            tone=tone,
            auto_reply_enabled=auto_reply_enabled,
            minimum_customer_confidence=confidence,
            version=1,
            created_by_id=actor_id,
            updated_by_id=actor_id,
        )
        db.session.add(candidate)
        try:
            db.session.flush()
        except IntegrityError:
            # The primary-key singleton prevents a second first-write from creating
            # another profile. Reload the winner and apply this update to it.
            db.session.rollback()
            profile = CompanyProfile.current()
            if profile is None:
                return jsonify(error={"code": "conflict", "message": "Company profile setup changed concurrently; reload and retry."}), 409
        else:
            profile = candidate
            created = True
    if not created:
        profile.company_name = company_name
        profile.company_brief = company_brief
        profile.tone = tone
        profile.auto_reply_enabled = auto_reply_enabled
        profile.minimum_customer_confidence = confidence
        profile.updated_by_id = actor_id
        profile.version += 1
    record_audit(
        "company_profile_created" if created else "company_profile_updated",
        actor=current_user(),
        details={
            "profileId": profile.id,
            "version": profile.version,
            "autoReplyEnabled": profile.auto_reply_enabled,
            "minimumCustomerConfidence": profile.minimum_customer_confidence,
        },
    )
    db.session.commit()
    return jsonify(profile=profile.to_dict(), ai=ai), 201 if created else 200


@admin_bp.get("/operations")
@roles_required("admin")
def operations_status():
    from ..services.groq import provider_status

    mailbox = MailboxSyncState.query.order_by(MailboxSyncState.updated_at.desc()).first()
    scheduler = current_app.extensions.get("support_scheduler")
    return jsonify(
        scheduler={"enabled": bool(scheduler), "running": bool(scheduler and scheduler.running)},
        mailbox=mailbox.to_dict() if mailbox else None,
        mail={
            "transport": current_app.config["MAIL_TRANSPORT"],
            "fromAddress": current_app.config["MAIL_FROM"],
            "imapEnabled": current_app.config["IMAP_ENABLED"],
        },
        ai=provider_status(),
    )


@admin_bp.post("/operations/poll-mailbox")
@roles_required("admin")
def run_mailbox_poll():
    result = poll_mailbox()
    record_audit("mailbox_poll_triggered", actor=current_user(), details=result)
    db.session.commit()
    return jsonify(result), 200 if result["status"] == "ok" else 409


@admin_bp.post("/operations/deliver-outbox")
@roles_required("admin")
def run_outbox_delivery():
    result = deliver_pending()
    record_audit("outbox_delivery_triggered", actor=current_user(), details=result)
    db.session.commit()
    return jsonify(result)


@admin_bp.post("/operations/check-sla")
@roles_required("admin")
def run_sla_check():
    result = escalate_overdue_tickets()
    record_audit("sla_check_triggered", actor=current_user(), details=result)
    db.session.commit()
    return jsonify(result)


@admin_bp.post("/operations/purge-revocations")
@roles_required("admin")
def purge_expired_revocations():
    from ..models import RevokedToken

    removed = RevokedToken.query.filter(RevokedToken.expires_at < utcnow() - timedelta(minutes=1)).delete()
    record_audit("expired_revocations_purged", actor=current_user(), details={"removed": removed})
    db.session.commit()
    return jsonify(removed=removed)
