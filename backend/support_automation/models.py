"""Relational data model for tickets, mail, automation policy, and audit history."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


TICKET_STATUSES = {"open", "in_progress", "awaiting_customer_reply", "resolved", "closed"}
TICKET_PRIORITIES = {"low", "normal", "high", "urgent"}
USER_ROLES = {"agent", "admin"}


def utcnow() -> datetime:
    """Use naive UTC consistently for SQLite and PostgreSQL portability."""

    return datetime.utcnow()


def iso(value: datetime | None) -> str | None:
    return f"{value.replace(microsecond=0).isoformat()}Z" if value else None


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(160), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="agent")
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    assigned_tickets = db.relationship(
        "Ticket", back_populates="assignee", foreign_keys="Ticket.assignee_id", lazy="select"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "fullName": self.full_name,
            "role": self.role,
            "isActive": self.is_active,
            "lastLoginAt": iso(self.last_login_at),
            "createdAt": iso(self.created_at),
        }


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(160), nullable=True)
    company = db.Column(db.String(160), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    tickets = db.relationship("Ticket", back_populates="customer", lazy="select")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "fullName": self.full_name,
            "company": self.company,
            "createdAt": iso(self.created_at),
        }


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    automation_enabled = db.Column(db.Boolean, nullable=False, default=False)
    automation_confidence_threshold = db.Column(db.Float, nullable=False, default=0.85)
    default_sla_hours = db.Column(db.Integer, nullable=False, default=24)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    tickets = db.relationship("Ticket", back_populates="category", lazy="select")
    templates = db.relationship("Template", back_populates="category", lazy="select")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "automationEnabled": self.automation_enabled,
            "automationConfidenceThreshold": self.automation_confidence_threshold,
            "defaultSlaHours": self.default_sla_hours,
            "isActive": self.is_active,
        }


class Ticket(db.Model):
    __tablename__ = "tickets"
    __table_args__ = (
        db.Index("ix_tickets_queue", "status", "priority", "due_at"),
        db.Index("ix_tickets_assignee", "assignee_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(32), unique=True, nullable=False, index=True)
    subject = db.Column(db.String(500), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    status = db.Column(db.String(40), nullable=False, default="open", index=True)
    priority = db.Column(db.String(20), nullable=False, default="normal", index=True)
    category_confidence = db.Column(db.Float, nullable=True)
    origin = db.Column(db.String(40), nullable=False, default="email")
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    first_response_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    last_customer_message_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    customer = db.relationship("Customer", back_populates="tickets", lazy="joined")
    category = db.relationship("Category", back_populates="tickets", lazy="joined")
    assignee = db.relationship("User", back_populates="assigned_tickets", foreign_keys=[assignee_id], lazy="joined")
    messages = db.relationship(
        "Message", back_populates="ticket", cascade="all, delete-orphan", order_by="Message.created_at", lazy="select"
    )
    audit_events = db.relationship(
        "AuditLog", back_populates="ticket", cascade="all, delete-orphan", order_by="AuditLog.created_at.desc()", lazy="select"
    )

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_at
            and self.due_at < utcnow()
            and self.status not in {"resolved", "closed"}
        )

    def to_dict(self, include_messages: bool = False, include_audit: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "reference": self.reference,
            "subject": self.subject,
            "status": self.status,
            "priority": self.priority,
            "categoryConfidence": self.category_confidence,
            "origin": self.origin,
            "dueAt": iso(self.due_at),
            "firstResponseAt": iso(self.first_response_at),
            "resolvedAt": iso(self.resolved_at),
            "lastCustomerMessageAt": iso(self.last_customer_message_at),
            "createdAt": iso(self.created_at),
            "updatedAt": iso(self.updated_at),
            "isOverdue": self.is_overdue,
            "customer": self.customer.to_dict() if self.customer else None,
            "category": self.category.to_dict() if self.category else None,
            "assignee": self.assignee.to_dict() if self.assignee else None,
        }
        if include_messages:
            data["messages"] = [message.to_dict() for message in self.messages]
        if include_audit:
            data["audit"] = [event.to_dict() for event in self.audit_events]
        return data


class Message(db.Model):
    __tablename__ = "messages"
    __table_args__ = (
        db.Index("ix_messages_thread", "in_reply_to", "created_at"),
        db.Index("ix_messages_ticket_direction", "ticket_id", "direction", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    direction = db.Column(db.String(20), nullable=False)  # inbound or outbound
    sender = db.Column(db.String(255), nullable=False)
    recipients = db.Column(db.JSON, nullable=False, default=list)
    cc = db.Column(db.JSON, nullable=False, default=list)
    subject = db.Column(db.String(500), nullable=False)
    body_text = db.Column(db.Text, nullable=False, default="")
    body_html = db.Column(db.Text, nullable=True)
    rfc_message_id = db.Column(db.String(500), unique=True, nullable=False, index=True)
    in_reply_to = db.Column(db.String(500), nullable=True, index=True)
    references_header = db.Column(db.Text, nullable=True)
    raw_headers = db.Column(db.JSON, nullable=True)
    source = db.Column(db.String(40), nullable=False, default="email")
    mailbox_uid = db.Column(db.String(120), nullable=True, index=True)
    delivery_status = db.Column(db.String(40), nullable=False, default="received")
    automation_source = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    ticket = db.relationship("Ticket", back_populates="messages", lazy="joined")
    attachments = db.relationship(
        "Attachment", back_populates="message", cascade="all, delete-orphan", lazy="select"
    )
    delivery = db.relationship(
        "OutboundDelivery", back_populates="outbound_message", uselist=False, cascade="all, delete-orphan", lazy="select"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticketId": self.ticket_id,
            "direction": self.direction,
            "sender": self.sender,
            "recipients": self.recipients or [],
            "cc": self.cc or [],
            "subject": self.subject,
            "bodyText": self.body_text,
            "bodyHtml": self.body_html,
            "messageId": self.rfc_message_id,
            "inReplyTo": self.in_reply_to,
            "references": self.references_header,
            "source": self.source,
            "deliveryStatus": self.delivery_status,
            "automationSource": self.automation_source,
            "createdAt": iso(self.created_at),
            "attachments": [attachment.to_dict() for attachment in self.attachments],
        }


class Attachment(db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False, index=True)
    original_name = db.Column(db.String(500), nullable=False)
    stored_name = db.Column(db.String(500), nullable=False, unique=True)
    storage_path = db.Column(db.String(1000), nullable=False)
    content_type = db.Column(db.String(255), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=False)
    checksum = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    message = db.relationship("Message", back_populates="attachments", lazy="joined")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "originalName": self.original_name,
            "contentType": self.content_type,
            "sizeBytes": self.size_bytes,
            "checksum": self.checksum,
            "downloadUrl": f"/api/v1/attachments/{self.id}/download",
            "createdAt": iso(self.created_at),
        }


class Template(db.Model):
    __tablename__ = "templates"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    subject_template = db.Column(db.String(500), nullable=False)
    body_template = db.Column(db.Text, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True)
    is_approved = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    auto_send = db.Column(db.Boolean, nullable=False, default=False)
    resolves_ticket = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    category = db.relationship("Category", back_populates="templates", lazy="joined")
    versions = db.relationship(
        "TemplateVersion", back_populates="template", cascade="all, delete-orphan", order_by="TemplateVersion.version.desc()", lazy="select"
    )

    def to_dict(self, include_versions: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "subjectTemplate": self.subject_template,
            "bodyTemplate": self.body_template,
            "category": self.category.to_dict() if self.category else None,
            "isApproved": self.is_approved,
            "isActive": self.is_active,
            "autoSend": self.auto_send,
            "resolvesTicket": self.resolves_ticket,
            "version": self.version,
            "createdAt": iso(self.created_at),
            "updatedAt": iso(self.updated_at),
        }
        if include_versions:
            data["versions"] = [version.to_dict() for version in self.versions]
        return data


class TemplateVersion(db.Model):
    __tablename__ = "template_versions"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("templates.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    subject_template = db.Column(db.String(500), nullable=False)
    body_template = db.Column(db.Text, nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    change_note = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    template = db.relationship("Template", back_populates="versions", lazy="joined")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "subjectTemplate": self.subject_template,
            "bodyTemplate": self.body_template,
            "changeNote": self.change_note,
            "createdAt": iso(self.created_at),
        }


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    __table_args__ = (db.Index("ix_audit_ticket_created", "ticket_id", "created_at"),)

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=True, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    actor_type = db.Column(db.String(40), nullable=False, default="system")
    action = db.Column(db.String(120), nullable=False, index=True)
    old_value = db.Column(db.JSON, nullable=True)
    new_value = db.Column(db.JSON, nullable=True)
    details = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    ticket = db.relationship("Ticket", back_populates="audit_events", lazy="joined")
    actor = db.relationship("User", lazy="joined")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticketId": self.ticket_id,
            "actor": self.actor.to_dict() if self.actor else {"type": self.actor_type},
            "actorType": self.actor_type,
            "action": self.action,
            "oldValue": self.old_value,
            "newValue": self.new_value,
            "details": self.details,
            "createdAt": iso(self.created_at),
        }


class OutboundDelivery(db.Model):
    __tablename__ = "outbound_deliveries"
    __table_args__ = (db.Index("ix_outbox_queue", "status", "not_before"),)

    id = db.Column(db.Integer, primary_key=True)
    outbound_message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), unique=True, nullable=False)
    status = db.Column(db.String(40), nullable=False, default="queued", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    status_after_send = db.Column(db.String(40), nullable=True)
    not_before = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    queued_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)

    outbound_message = db.relationship("Message", back_populates="delivery", lazy="joined")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "messageId": self.outbound_message_id,
            "status": self.status,
            "attempts": self.attempts,
            "lastError": self.last_error,
            "queuedAt": iso(self.queued_at),
            "sentAt": iso(self.sent_at),
        }


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(64), unique=True, nullable=False, index=True)
    token_type = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class MailboxSyncState(db.Model):
    __tablename__ = "mailbox_sync_state"

    id = db.Column(db.Integer, primary_key=True)
    mailbox = db.Column(db.String(255), unique=True, nullable=False)
    last_poll_at = db.Column(db.DateTime, nullable=True)
    last_success_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mailbox": self.mailbox,
            "lastPollAt": iso(self.last_poll_at),
            "lastSuccessAt": iso(self.last_success_at),
            "lastError": self.last_error,
        }


class CompanyProfile(db.Model):
    """Single-deployment company context used to ground AI support automation."""

    __tablename__ = "company_profiles"
    __table_args__ = (
        db.CheckConstraint("id = 1", name="ck_company_profiles_singleton"),
    )
    # New profiles use a deterministic primary key so concurrent first writes cannot
    # create more than one active profile. ``current`` still reads legacy rows.
    SINGLETON_ID = 1

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(160), nullable=False)
    company_brief = db.Column(db.Text, nullable=False)
    tone = db.Column(db.String(80), nullable=False, default="clear and helpful")
    auto_reply_enabled = db.Column(db.Boolean, nullable=False, default=False)
    minimum_customer_confidence = db.Column(db.Float, nullable=False, default=0.85)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @classmethod
    def current(cls) -> "CompanyProfile | None":
        """Return the deterministic singleton, with a safe legacy-row fallback."""

        return db.session.get(cls, cls.SINGLETON_ID) or cls.query.order_by(cls.id.asc()).first()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "companyName": self.company_name,
            "companyBrief": self.company_brief,
            "tone": self.tone,
            "autoReplyEnabled": self.auto_reply_enabled,
            "minimumCustomerConfidence": self.minimum_customer_confidence,
            "version": self.version,
            "isComplete": bool(self.company_name.strip() and len(self.company_brief.strip()) >= 20),
            "createdAt": iso(self.created_at),
            "updatedAt": iso(self.updated_at),
        }


class InboundIntake(db.Model):
    """Auditable IMAP intake decision, including promotions that never become tickets."""

    __tablename__ = "inbound_intakes"
    __table_args__ = (
        db.Index("ix_inbound_intakes_disposition_created", "disposition", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    rfc_message_id = db.Column(db.String(500), unique=True, nullable=False, index=True)
    source = db.Column(db.String(40), nullable=False, default="imap")
    mailbox_uid = db.Column(db.String(120), nullable=True, index=True)
    sender = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    disposition = db.Column(db.String(40), nullable=False, default="needs_review", index=True)
    confidence = db.Column(db.Float, nullable=True)
    classification_source = db.Column(db.String(40), nullable=False, default="rules")
    model = db.Column(db.String(160), nullable=True)
    reason = db.Column(db.String(500), nullable=True)
    company_profile_version = db.Column(db.Integer, nullable=True)
    was_human_eligible = db.Column(db.Boolean, nullable=False, default=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    ticket = db.relationship("Ticket", lazy="joined", foreign_keys=[ticket_id])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "messageId": self.rfc_message_id,
            "source": self.source,
            "disposition": self.disposition,
            "confidence": self.confidence,
            "classificationSource": self.classification_source,
            "model": self.model,
            "reason": self.reason,
            "companyProfileVersion": self.company_profile_version,
            "ticketId": self.ticket_id,
            "createdAt": iso(self.created_at),
        }
