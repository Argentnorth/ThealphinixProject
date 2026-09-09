"""Background jobs for mailbox polling, durable delivery, and SLA escalation."""

from __future__ import annotations

import atexit
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from .services.ingestion import poll_mailbox
from .services.intake import recover_groq_error_intakes
from .services.mail import deliver_pending, escalate_overdue_tickets


def _job(app: Flask, operation: Callable[[], object]) -> Callable[[], None]:
    def run() -> None:
        with app.app_context():
            try:
                operation()
            except Exception:
                app.logger.exception("Background job failed")

    return run


def start_scheduler(app: Flask) -> BackgroundScheduler:
    """Start one in-process scheduler; production can run this in a worker process."""

    existing = app.extensions.get("support_scheduler")
    if existing:
        return existing
    scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    run_now = datetime.now(timezone.utc)
    scheduler.add_job(
        _job(app, poll_mailbox),
        trigger="interval",
        seconds=app.config["POLL_SECONDS"],
        id="imap-poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=run_now,
    )
    scheduler.add_job(
        _job(app, deliver_pending),
        trigger="interval",
        seconds=app.config["OUTBOX_SECONDS"],
        id="outbox-delivery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=run_now,
    )
    scheduler.add_job(
        _job(app, recover_groq_error_intakes),
        trigger="interval",
        seconds=app.config["GROQ_RECOVERY_SECONDS"],
        id="groq-error-recovery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=run_now,
    )
    scheduler.add_job(
        _job(app, escalate_overdue_tickets),
        trigger="interval",
        seconds=app.config["SLA_CHECK_SECONDS"],
        id="sla-escalation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.extensions["support_scheduler"] = scheduler
    atexit.register(lambda: scheduler.shutdown(wait=False) if scheduler.running else None)
    return scheduler


def stop_scheduler(app: Flask) -> None:
    scheduler = app.extensions.pop("support_scheduler", None)
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
