"""Approved response template and category policy endpoints."""

from __future__ import annotations

import re
from typing import Any

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..models import Category, Template, TemplateVersion
from ..security import current_user, roles_required
from ..services.audit import record_audit

templates_bp = Blueprint("templates", __name__, url_prefix="/api/v1")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _bool(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _category(category_id: Any) -> Category | None:
    if category_id in {None, "", 0}:
        return None
    try:
        return db.session.get(Category, int(category_id))
    except (TypeError, ValueError):
        return None


def _template_payload(payload: dict[str, Any], *, existing: Template | None = None) -> tuple[dict[str, Any], str | None]:
    slug = str(payload.get("slug", existing.slug if existing else "")).strip().lower()
    name = str(payload.get("name", existing.name if existing else "")).strip()
    subject = str(payload.get("subjectTemplate", existing.subject_template if existing else "")).strip()
    body = str(payload.get("bodyTemplate", existing.body_template if existing else "")).strip()
    category = _category(payload.get("categoryId", existing.category_id if existing else None))
    category_requested = payload.get("categoryId", existing.category_id if existing else None)
    if not SLUG.fullmatch(slug):
        return {}, "slug must use lowercase letters, digits, and hyphens."
    if not name or not subject or not body:
        return {}, "name, subjectTemplate, and bodyTemplate are required."
    if category_requested not in {None, "", 0} and category is None:
        return {}, "Selected category does not exist."
    values = {
        "slug": slug,
        "name": name,
        "subject_template": subject,
        "body_template": body,
        "category_id": category.id if category else None,
        "is_active": _bool(payload, "isActive", existing.is_active if existing else True),
        "auto_send": _bool(payload, "autoSend", existing.auto_send if existing else False),
        "resolves_ticket": _bool(payload, "resolvesTicket", existing.resolves_ticket if existing else False),
    }
    if values["auto_send"] and (category is None or not category.automation_enabled):
        return {}, "autoSend requires a category with automation enabled."
    return values, None


@templates_bp.get("/templates")
@roles_required("admin", "agent")
def list_templates():
    query = Template.query
    if current_user().role != "admin":
        query = query.filter_by(is_active=True, is_approved=True)
    return jsonify(items=[template.to_dict() for template in query.order_by(Template.name.asc()).all()])


@templates_bp.get("/templates/<int:template_id>")
@roles_required("admin", "agent")
def get_template(template_id: int):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify(error={"code": "not_found", "message": "Template not found."}), 404
    if current_user().role != "admin" and (not template.is_active or not template.is_approved):
        return jsonify(error={"code": "forbidden", "message": "Template is not available."}), 403
    return jsonify(template=template.to_dict(include_versions=current_user().role == "admin"))


@templates_bp.post("/templates")
@roles_required("admin")
def create_template():
    payload = request.get_json(silent=True) or {}
    values, error = _template_payload(payload)
    if error:
        return jsonify(error={"code": "validation_error", "message": error}), 400
    if Template.query.filter_by(slug=values["slug"]).first():
        return jsonify(error={"code": "conflict", "message": "Template slug already exists."}), 409
    actor = current_user()
    template = Template(**values, is_approved=False, version=1, created_by_id=actor.id, updated_by_id=actor.id)
    db.session.add(template)
    db.session.flush()
    db.session.add(
        TemplateVersion(
            template_id=template.id,
            version=1,
            subject_template=template.subject_template,
            body_template=template.body_template,
            changed_by_id=actor.id,
            change_note="Initial draft",
        )
    )
    record_audit("template_created", actor=actor, details={"templateId": template.id, "slug": template.slug})
    db.session.commit()
    return jsonify(template=template.to_dict(include_versions=True)), 201


@templates_bp.patch("/templates/<int:template_id>")
@roles_required("admin")
def update_template(template_id: int):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify(error={"code": "not_found", "message": "Template not found."}), 404
    payload = request.get_json(silent=True) or {}
    values, error = _template_payload(payload, existing=template)
    if error:
        return jsonify(error={"code": "validation_error", "message": error}), 400
    duplicate = Template.query.filter(Template.slug == values["slug"], Template.id != template.id).first()
    if duplicate:
        return jsonify(error={"code": "conflict", "message": "Template slug already exists."}), 409
    changed = any(getattr(template, field) != value for field, value in values.items())
    if changed:
        template.version += 1
        for field, value in values.items():
            setattr(template, field, value)
        template.updated_by_id = current_user().id
        template.is_approved = False
        db.session.add(
            TemplateVersion(
                template_id=template.id,
                version=template.version,
                subject_template=template.subject_template,
                body_template=template.body_template,
                changed_by_id=current_user().id,
                change_note=str(payload.get("changeNote", "Updated; approval required."))[:500],
            )
        )
        record_audit("template_updated", actor=current_user(), details={"templateId": template.id, "version": template.version})
        db.session.commit()
    return jsonify(template=template.to_dict(include_versions=True))


@templates_bp.post("/templates/<int:template_id>/approval")
@roles_required("admin")
def set_template_approval(template_id: int):
    template = db.session.get(Template, template_id)
    if not template:
        return jsonify(error={"code": "not_found", "message": "Template not found."}), 404
    payload = request.get_json(silent=True) or {}
    approved = _bool(payload, "approved", True)
    if approved and template.auto_send and (not template.category or not template.category.automation_enabled):
        return jsonify(error={"code": "validation_error", "message": "Auto-send template requires an approved automation category."}), 400
    template.is_approved = approved
    template.updated_by_id = current_user().id
    record_audit("template_approval_changed", actor=current_user(), details={"templateId": template.id, "approved": approved})
    db.session.commit()
    return jsonify(template=template.to_dict(include_versions=True))


@templates_bp.get("/categories")
@roles_required("admin", "agent")
def list_categories():
    return jsonify(items=[category.to_dict() for category in Category.query.order_by(Category.name.asc()).all()])


@templates_bp.post("/categories")
@roles_required("admin")
def create_category():
    payload = request.get_json(silent=True) or {}
    slug = str(payload.get("slug", "")).strip().lower()
    name = str(payload.get("name", "")).strip()
    if not SLUG.fullmatch(slug) or not name:
        return jsonify(error={"code": "validation_error", "message": "A valid slug and name are required."}), 400
    if Category.query.filter_by(slug=slug).first():
        return jsonify(error={"code": "conflict", "message": "Category slug already exists."}), 409
    try:
        threshold = float(payload.get("automationConfidenceThreshold", 0.9))
        sla_hours = int(payload.get("defaultSlaHours", 24))
    except (TypeError, ValueError):
        return jsonify(error={"code": "validation_error", "message": "Threshold and SLA must be numeric."}), 400
    if not 0 <= threshold <= 1 or sla_hours < 1:
        return jsonify(error={"code": "validation_error", "message": "Threshold must be 0-1 and SLA at least one hour."}), 400
    category = Category(
        slug=slug,
        name=name,
        description=str(payload.get("description", "")).strip() or None,
        automation_enabled=_bool(payload, "automationEnabled"),
        automation_confidence_threshold=threshold,
        default_sla_hours=sla_hours,
        is_active=_bool(payload, "isActive", True),
    )
    db.session.add(category)
    db.session.flush()
    record_audit("category_created", actor=current_user(), details={"categoryId": category.id, "slug": category.slug})
    db.session.commit()
    return jsonify(category=category.to_dict()), 201


@templates_bp.patch("/categories/<int:category_id>")
@roles_required("admin")
def update_category(category_id: int):
    category = db.session.get(Category, category_id)
    if not category:
        return jsonify(error={"code": "not_found", "message": "Category not found."}), 404
    payload = request.get_json(silent=True) or {}
    for key, field in (("name", "name"), ("description", "description"), ("isActive", "is_active"), ("automationEnabled", "automation_enabled")):
        if key in payload:
            value = _bool(payload, key) if key in {"isActive", "automationEnabled"} else str(payload[key]).strip()
            setattr(category, field, value)
    if "automationConfidenceThreshold" in payload:
        try:
            threshold = float(payload["automationConfidenceThreshold"])
        except (TypeError, ValueError):
            threshold = -1
        if not 0 <= threshold <= 1:
            return jsonify(error={"code": "validation_error", "message": "Threshold must be from 0 to 1."}), 400
        category.automation_confidence_threshold = threshold
    if "defaultSlaHours" in payload:
        try:
            sla = int(payload["defaultSlaHours"])
        except (TypeError, ValueError):
            sla = 0
        if sla < 1:
            return jsonify(error={"code": "validation_error", "message": "SLA must be at least one hour."}), 400
        category.default_sla_hours = sla
    record_audit("category_updated", actor=current_user(), details={"categoryId": category.id})
    db.session.commit()
    return jsonify(category=category.to_dict())
