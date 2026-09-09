"""Approved-template rendering and optional AI-assisted draft generation."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from flask import current_app

from ..extensions import db
from ..models import Template, Ticket

TOKEN_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def template_context(ticket: Ticket) -> dict[str, str]:
    return {
        "ticket_reference": ticket.reference,
        "ticket_subject": ticket.subject,
        "customer_name": ticket.customer.full_name or ticket.customer.email,
        "customer_email": ticket.customer.email,
        "category": ticket.category.name if ticket.category else "General inquiry",
        "priority": ticket.priority,
        "support_email": current_app.config["MAIL_FROM"],
    }


def render(value: str, context: dict[str, str]) -> str:
    """Render only whitelisted ``{{variable}}`` tokens; unknown tokens stay visible."""

    return TOKEN_PATTERN.sub(lambda match: context.get(match.group(1), match.group(0)), value)


def render_template(template: Template, ticket: Ticket) -> dict[str, str]:
    context = template_context(ticket)
    return {
        "subject": render(template.subject_template, context),
        "body": render(template.body_template, context),
        "templateId": str(template.id),
        "templateVersion": str(template.version),
    }


def get_template(template_id: int | None) -> Template | None:
    return db.session.get(Template, template_id) if template_id else None


def select_auto_template(ticket: Ticket) -> Template | None:
    if not ticket.category:
        return None
    return (
        Template.query.filter_by(
            category_id=ticket.category_id,
            is_active=True,
            is_approved=True,
            auto_send=True,
        )
        .order_by(Template.updated_at.desc())
        .first()
    )


def acknowledgement_template() -> Template | None:
    return Template.query.filter_by(
        slug="ticket-acknowledgement", is_active=True, is_approved=True
    ).first()


def _knowledge_based_draft(ticket: Ticket, extra_instruction: str = "") -> str:
    customer = ticket.customer.full_name or "there"
    category = ticket.category.slug if ticket.category else "general-inquiry"
    guidance = {
        "order-status": "We are checking the latest order and carrier information and will update you shortly.",
        "billing": "We are reviewing the billing details against your account and will clarify the charge shortly.",
        "account-access": "For your security, we are reviewing the account-access issue and will guide you through the next safe step.",
        "refund": "We are reviewing the request against our return and refund policy and will confirm the available options shortly.",
        "technical-issue": "We are investigating the reported issue and will share troubleshooting steps or an update as soon as possible.",
        "complaint": "I am sorry that your experience has fallen short. We are reviewing the details so we can make this right.",
    }.get(category, "Thank you for contacting us. We are reviewing your request and will follow up shortly.")
    instruction = f"\n\nAdditional agent note: {extra_instruction.strip()}" if extra_instruction.strip() else ""
    return (
        f"Hello {customer},\n\n"
        f"Thank you for contacting us about ticket {ticket.reference}. {guidance}"
        f"{instruction}\n\n"
        "Kind regards,\nCustomer Support"
    )


def _llm_draft(ticket: Ticket, extra_instruction: str) -> str | None:
    """Use a configured, organization-approved endpoint only when explicitly enabled."""

    endpoint = current_app.config.get("LLM_ENDPOINT", "").strip()
    if not endpoint:
        return None
    payload = {
        "ticket": {
            "reference": ticket.reference,
            "subject": ticket.subject,
            "category": ticket.category.name if ticket.category else "General inquiry",
            "priority": ticket.priority,
            "customerMessage": ticket.messages[-1].body_text if ticket.messages else "",
        },
        "instruction": extra_instruction,
        "constraints": [
            "Draft only; do not claim an unverified action was completed.",
            "Do not include sensitive data.",
            "Use a concise professional support tone.",
        ],
    }
    headers = {"Content-Type": "application/json"}
    if current_app.config.get("LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {current_app.config['LLM_API_KEY']}"
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=current_app.config["LLM_TIMEOUT_SECONDS"]) as response:  # nosec B310 - admin-configured endpoint
            result: Any = json.loads(response.read().decode("utf-8"))
        if isinstance(result, dict):
            for key in ("draft", "text", "content"):
                if isinstance(result.get(key), str) and result[key].strip():
                    return result[key].strip()
    except (URLError, TimeoutError, ValueError, OSError):
        current_app.logger.warning("Configured LLM draft request failed; using local draft")
    return None


def generate_draft(ticket: Ticket, extra_instruction: str = "") -> dict[str, str]:
    """Generate a human-review-only draft with a deterministic privacy-safe fallback."""

    draft = _llm_draft(ticket, extra_instruction) or _knowledge_based_draft(ticket, extra_instruction)
    return {
        "subject": f"Re: {ticket.subject}",
        "body": draft,
        "source": "llm" if current_app.config.get("LLM_ENDPOINT") else "knowledge-base",
        "requiresApproval": "true",
    }
