"""Environment-backed application configuration."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Loading is intentionally non-overriding: deployment environment always wins over .env.
load_dotenv(PROJECT_ROOT / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class Config:
    """Configuration deliberately keeps mail credentials in environment variables."""

    PROJECT_ROOT = PROJECT_ROOT
    INSTANCE_DIR = PROJECT_ROOT / "instance"
    _attachment_dir = Path(os.getenv("ATTACHMENT_DIR", "storage/attachments"))
    ATTACHMENT_DIR = _attachment_dir if _attachment_dir.is_absolute() else PROJECT_ROOT / _attachment_dir

    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-development-secret")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-development-jwt-secret")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "60")))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "14")))

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'support_automation.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))

    ENVIRONMENT = os.getenv("APP_ENV", "development").strip().lower()
    CORS_ORIGINS = env_list("CORS_ORIGINS", "http://localhost:5173")
    # A fresh local checkout becomes runnable without a manual migration step.
    # Production must opt in explicitly through APP_ENV=production or AUTO_CREATE_SCHEMA.
    AUTO_CREATE_SCHEMA = env_bool("AUTO_CREATE_SCHEMA", ENVIRONMENT != "production")
    SCHEDULER_ENABLED = env_bool("SCHEDULER_ENABLED", False)
    # Bound scheduled automation tightly enough for responsive support while
    # preventing accidental sub-second IMAP/SMTP hammering from environment typos.
    POLL_SECONDS = min(10, max(5, int(os.getenv("POLL_SECONDS", "5"))))
    OUTBOX_SECONDS = min(5, max(1, int(os.getenv("OUTBOX_SECONDS", "1"))))
    SLA_CHECK_SECONDS = max(60, int(os.getenv("SLA_CHECK_SECONDS", "900")))
    # Retry only receipts that were safely held for review during a transient Groq outage.
    # This is intentionally slower than fresh IMAP polling to avoid provider hammering.
    GROQ_RECOVERY_SECONDS = max(30, int(os.getenv("GROQ_RECOVERY_SECONDS", "60")))

    # Inbound IMAP. IMAP is disabled until a host and mailbox are configured.
    IMAP_ENABLED = env_bool("IMAP_ENABLED", False)
    IMAP_HOST = os.getenv("IMAP_HOST", "")
    IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
    IMAP_USERNAME = os.getenv("IMAP_USERNAME", "")
    IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")
    IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")

    # SMTP defaults to a console transport so a local demo never emits real email.
    MAIL_TRANSPORT = os.getenv("MAIL_TRANSPORT", "console").strip().lower()
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_USE_STARTTLS = env_bool("SMTP_USE_STARTTLS", True)
    MAIL_FROM = os.getenv("MAIL_FROM", "support@example.com")
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "SupportPilot")
    SMTP_TIMEOUT_SECONDS = int(os.getenv("SMTP_TIMEOUT_SECONDS", "20"))
    OUTBOX_MAX_ATTEMPTS = max(1, int(os.getenv("OUTBOX_MAX_ATTEMPTS", "5")))

    # Replies from these support-owned addresses are never auto-acknowledged.
    AUTO_ACK_SUPPORT_ADDRESSES = env_list("AUTO_ACK_SUPPORT_ADDRESSES", MAIL_FROM)
    AUTO_ACKNOWLEDGEMENT = env_bool("AUTO_ACKNOWLEDGEMENT", True)
    CLASSIFIER_MODEL_PATH = os.getenv("CLASSIFIER_MODEL_PATH", "")

    # Groq is opt-in and server-only. Never expose this key through the frontend.
    GROQ_ENABLED = env_bool("GROQ_ENABLED", False)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    GROQ_TIMEOUT_SECONDS = max(5, int(os.getenv("GROQ_TIMEOUT_SECONDS", "20")))
    AI_CUSTOMER_CONFIDENCE_THRESHOLD = min(
        1.0, max(0.5, float(os.getenv("AI_CUSTOMER_CONFIDENCE_THRESHOLD", "0.85")))
    )

    # Legacy review-only draft integration. It remains separate from Groq automation.
    LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "15"))

    # Enables no-op / expected failures in production configuration to be easier to spot.
    DEBUG = env_bool("FLASK_DEBUG", False)
