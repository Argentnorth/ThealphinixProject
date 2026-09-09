"""Reference policy and intentionally explicit local-demo data seeding."""

from __future__ import annotations

from datetime import timedelta

from ..extensions import db
from ..models import AuditLog, Category, Customer, Message, Template, TemplateVersion, Ticket, User, utcnow

CATEGORY_SEED = (
    {"slug": "order-status", "name": "Order status", "description": "Shipment, delivery, and tracking questions.", "automation": True, "threshold": 0.9, "sla": 24},
    {"slug": "general-inquiry", "name": "General inquiry", "description": "Low-risk general service questions.", "automation": True, "threshold": 0.92, "sla": 48},
    {"slug": "billing", "name": "Billing and payments", "description": "Invoices, payment failures, and charges.", "automation": False, "threshold": 0.95, "sla": 12},
    {"slug": "account-access", "name": "Account access", "description": "Password, sign-in, and account security requests.", "automation": False, "threshold": 0.98, "sla": 4},
    {"slug": "refund", "name": "Refund and returns", "description": "Refund, cancellation, and return requests.", "automation": False, "threshold": 0.97, "sla": 12},
    {"slug": "technical-issue", "name": "Technical issue", "description": "Product defects, errors, and failures.", "automation": False, "threshold": 0.9, "sla": 12},
    {"slug": "complaint", "name": "Complaint", "description": "Service dissatisfaction and escalation requests.", "automation": False, "threshold": 0.98, "sla": 8},
)

TEMPLATE_SEED = (
    {
        "slug": "ticket-acknowledgement",
        "name": "New ticket acknowledgement",
        "category": None,
        "subject": "We received your request ({{ticket_reference}})",
        "body": "Hello {{customer_name}},\n\nThank you for contacting us. Your request has been logged as ticket {{ticket_reference}}. Our team will review it and respond as soon as possible.\n\nKind regards,\nCustomer Support",
        "auto_send": False,
        "resolves": False,
    },
    {
        "slug": "order-status-update",
        "name": "Order status response",
        "category": "order-status",
        "subject": "Update on your request ({{ticket_reference}})",
        "body": "Hello {{customer_name}},\n\nThank you for your order-status question. We have received ticket {{ticket_reference}} and are checking the latest delivery information. We will update you as soon as we have confirmed details.\n\nKind regards,\nCustomer Support",
        "auto_send": True,
        "resolves": False,
    },
    {
        "slug": "general-inquiry-response",
        "name": "General inquiry response",
        "category": "general-inquiry",
        "subject": "Re: {{ticket_subject}} ({{ticket_reference}})",
        "body": "Hello {{customer_name}},\n\nThank you for getting in touch. We have logged your question as {{ticket_reference}} and will provide the information you need shortly.\n\nKind regards,\nCustomer Support",
        "auto_send": True,
        "resolves": False,
    },
)


def seed_reference_data() -> dict[str, int]:
    """Idempotently install reviewable categories and approved template versions."""

    categories_added = 0
    templates_added = 0
    for item in CATEGORY_SEED:
        category = Category.query.filter_by(slug=item["slug"]).first()
        if not category:
            category = Category(
                slug=item["slug"],
                name=item["name"],
                description=item["description"],
                automation_enabled=item["automation"],
                automation_confidence_threshold=item["threshold"],
                default_sla_hours=item["sla"],
            )
            db.session.add(category)
            categories_added += 1
    db.session.flush()

    for item in TEMPLATE_SEED:
        if Template.query.filter_by(slug=item["slug"]).first():
            continue
        category = Category.query.filter_by(slug=item["category"]).first() if item["category"] else None
        template = Template(
            slug=item["slug"],
            name=item["name"],
            category_id=category.id if category else None,
            subject_template=item["subject"],
            body_template=item["body"],
            is_approved=True,
            is_active=True,
            auto_send=item["auto_send"],
            resolves_ticket=item["resolves"],
            version=1,
        )
        db.session.add(template)
        db.session.flush()
        db.session.add(
            TemplateVersion(
                template_id=template.id,
                version=1,
                subject_template=template.subject_template,
                body_template=template.body_template,
                change_note="Initial approved policy template",
            )
        )
        templates_added += 1
    db.session.commit()
    return {"categories": categories_added, "templates": templates_added}


def seed_demo_data() -> dict[str, str]:
    """Create isolated local-development users and a small realistic queue once."""

    seed_reference_data()
    admin = User.query.filter_by(email="admin@support.local").first()
    if not admin:
        admin = User(email="admin@support.local", full_name="System Administrator", role="admin")
        admin.set_password("Admin@12345")
        db.session.add(admin)
    agent = User.query.filter_by(email="agent@support.local").first()
    if not agent:
        agent = User(email="agent@support.local", full_name="Avery Support", role="agent")
        agent.set_password("Agent@12345")
        db.session.add(agent)
    db.session.flush()

    if not Ticket.query.first():
        now = utcnow()
        order_category = Category.query.filter_by(slug="order-status").one()
        refund_category = Category.query.filter_by(slug="refund").one()
        technical_category = Category.query.filter_by(slug="technical-issue").one()
        examples = (
            ("Maya Patel", "maya@example.test", "Where is my order #1204?", order_category, "normal", "Hi, could you tell me when order #1204 is expected to arrive?", "awaiting_customer_reply", agent, 0.94),
            ("Noah Williams", "noah@example.test", "Refund requested for duplicate charge", refund_category, "high", "I was charged twice for my subscription and need a refund as soon as possible.", "open", None, 0.91),
            ("Aria Chen", "aria@example.test", "Application shows an error on checkout", technical_category, "urgent", "The checkout page crashes with an error and I cannot complete payment.", "in_progress", agent, 0.87),
        )
        for index, (name, email, subject, category, priority, body, status, assignee, confidence) in enumerate(examples, start=1):
            customer = Customer(email=email, full_name=name)
            db.session.add(customer)
            db.session.flush()
            ticket = Ticket(
                reference=f"SUP-DEMO-{index:04d}",
                subject=subject,
                customer_id=customer.id,
                category_id=category.id,
                assignee_id=assignee.id if assignee else None,
                priority=priority,
                status=status,
                category_confidence=confidence,
                origin="demo",
                due_at=now + timedelta(hours=category.default_sla_hours),
                last_customer_message_at=now - timedelta(minutes=index * 15),
            )
            db.session.add(ticket)
            db.session.flush()
            inbound = Message(
                ticket_id=ticket.id,
                direction="inbound",
                sender=email,
                recipients=["support@example.com"],
                cc=[],
                subject=subject,
                body_text=body,
                rfc_message_id=f"<demo-inbound-{index}@support.local>",
                delivery_status="received",
                source="demo",
                created_at=now - timedelta(minutes=index * 15),
            )
            db.session.add(inbound)
            db.session.add(AuditLog(ticket_id=ticket.id, actor_type="system", action="ticket_created", details={"source": "demo"}))
        db.session.commit()
    return {"admin": "admin@support.local / Admin@12345", "agent": "agent@support.local / Agent@12345"}
