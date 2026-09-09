"""Explainable category and priority prediction with an optional sklearn model."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

from flask import current_app

from ..models import Category

# These deterministic rules are the safe baseline when no trained local model exists.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "billing": ("invoice", "billing", "charged", "charge", "payment", "card", "receipt", "price"),
    "order-status": ("order", "shipment", "shipping", "tracking", "delivery", "where is", "arrive"),
    "account-access": ("password", "login", "sign in", "account locked", "reset", "verification code"),
    "refund": ("refund", "return", "cancel order", "cancel my", "money back", "reimbursement"),
    "technical-issue": ("error", "bug", "broken", "crash", "not working", "failed", "issue"),
    "complaint": ("complaint", "unhappy", "disappointed", "terrible", "poor service", "angry"),
    "general-inquiry": ("information", "brochure", "hours", "location", "available", "question", "help"),
}

URGENT_TERMS = (
    "urgent", "immediately", "asap", "fraud", "unauthorized", "security breach",
    "data loss", "locked out", "cannot access", "can't access", "payment failed",
)
HIGH_TERMS = (
    "refund", "cancel", "charged", "chargeback", "complaint", "not working", "error",
    "overcharged", "failed payment", "deadline",
)


@lru_cache(maxsize=2)
def _load_model(path_text: str):
    """Load only a local model explicitly configured by the deployer."""

    if not path_text or not Path(path_text).is_file():
        return None
    try:
        import joblib  # Provided by the optional requirements-ml.txt dependency.

        return joblib.load(path_text)
    except Exception:  # A malformed optional model must never stop mail ingestion.
        current_app.logger.exception("Unable to load configured classifier model")
        return None


def _choose_category(categories: Iterable[Category], slug: str | None) -> Category | None:
    items = list(categories)
    if not items:
        return None
    if slug:
        normalized = slug.strip().lower().replace("_", "-").replace(" ", "-")
        for category in items:
            if category.slug == normalized:
                return category
    return next((category for category in items if category.slug == "general-inquiry"), items[0])


def _predict_with_model(text: str, categories: list[Category]) -> tuple[Category | None, float] | None:
    model = _load_model(current_app.config.get("CLASSIFIER_MODEL_PATH", ""))
    if model is None:
        return None
    try:
        probabilities = model.predict_proba([text])[0]
        classes = list(model.classes_)
        best_index = max(range(len(probabilities)), key=lambda index: float(probabilities[index]))
        category = _choose_category(categories, str(classes[best_index]))
        if category:
            return category, round(float(probabilities[best_index]), 4)
    except Exception:
        current_app.logger.exception("Configured classifier failed; using deterministic fallback")
    return None


def classify_category(subject: str, body: str, categories: list[Category]) -> tuple[Category | None, float, str]:
    """Return category, confidence, and source without ever blocking a ticket."""

    text = f"{subject}\n{body}".lower()
    model_result = _predict_with_model(text, categories)
    if model_result:
        category, confidence = model_result
        return category, confidence, "sklearn"

    scores: dict[str, int] = {
        slug: sum(1 for word in words if word in text)
        for slug, words in CATEGORY_KEYWORDS.items()
    }
    winning_slug, score = max(scores.items(), key=lambda item: item[1])
    category = _choose_category(categories, winning_slug if score else "general-inquiry")
    confidence = min(0.97, 0.46 + score * 0.13) if score else 0.45
    return category, round(confidence, 4), "rules"


def detect_priority(subject: str, body: str, category: Category | None = None) -> str:
    """Classify urgency conservatively; high-impact terms take precedence."""

    text = f"{subject}\n{body}".lower()
    if any(term in text for term in URGENT_TERMS):
        return "urgent"
    if any(term in text for term in HIGH_TERMS):
        return "high"
    if category and category.slug in {"refund", "complaint"}:
        return "high"
    if len(text.strip()) < 30 and category and category.slug == "general-inquiry":
        return "low"
    return "normal"


def triage(subject: str, body: str, categories: list[Category]) -> dict[str, object]:
    category, confidence, source = classify_category(subject, body, categories)
    return {
        "category": category,
        "confidence": confidence,
        "priority": detect_priority(subject, body, category),
        "source": source,
    }
