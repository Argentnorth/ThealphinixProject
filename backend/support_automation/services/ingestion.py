"""IMAP polling adapter with commit-before-seen delivery semantics."""

from __future__ import annotations

import imaplib
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import MailboxSyncState, utcnow
from .intake import route_inbound
from .mime_parser import parse_rfc822


def _state() -> MailboxSyncState:
    mailbox = f"{current_app.config['IMAP_HOST']}:{current_app.config['IMAP_FOLDER']}"
    state = MailboxSyncState.query.filter_by(mailbox=mailbox).first()
    if not state:
        state = MailboxSyncState(mailbox=mailbox)
        db.session.add(state)
        db.session.commit()
    return state


def _raw_message(fetch_data: list[Any]) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return None


def poll_mailbox(limit: int = 50) -> dict[str, Any]:
    """Fetch unseen messages and mark each seen after its intake decision persists."""

    if not current_app.config["IMAP_ENABLED"]:
        return {"status": "disabled", "processed": 0, "created": 0, "duplicates": 0, "errors": []}
    if not all((current_app.config["IMAP_HOST"], current_app.config["IMAP_USERNAME"], current_app.config["IMAP_PASSWORD"])):
        return {"status": "misconfigured", "processed": 0, "created": 0, "duplicates": 0, "errors": ["IMAP credentials are incomplete."]}

    state = _state()
    state.last_poll_at = utcnow()
    db.session.commit()
    client: imaplib.IMAP4_SSL | None = None
    result: dict[str, Any] = {
        "status": "ok", "processed": 0, "created": 0, "duplicates": 0,
        "promotional": 0, "review": 0, "suppressed": 0, "errors": [],
    }
    try:
        client = imaplib.IMAP4_SSL(current_app.config["IMAP_HOST"], current_app.config["IMAP_PORT"])
        client.login(current_app.config["IMAP_USERNAME"], current_app.config["IMAP_PASSWORD"])
        status, _ = client.select(current_app.config["IMAP_FOLDER"], readonly=False)
        if status != "OK":
            raise RuntimeError("Unable to select configured IMAP folder.")

        status, data = client.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("Unable to search unseen IMAP messages.")
        uids = (data[0] or b"").split()[: max(1, min(limit, 200))]
        for uid in uids:
            uid_text = uid.decode("ascii", errors="replace")
            try:
                status, fetched = client.uid("fetch", uid, "(RFC822)")
                raw = _raw_message(fetched)
                if status != "OK" or raw is None:
                    raise RuntimeError("Unable to fetch raw RFC822 message.")
                parsed = parse_rfc822(
                    raw,
                    support_addresses=current_app.config["AUTO_ACK_SUPPORT_ADDRESSES"],
                )
                outcome = route_inbound(parsed, source="imap", mailbox_uid=uid_text)
                # The intake decision or customer ticket is durable before this IMAP mutation.
                status, _ = client.uid("store", uid, "+FLAGS", "\\Seen")
                if status != "OK":
                    raise RuntimeError("Message was committed but could not be marked seen; it will retry idempotently.")
                result["processed"] += 1
                result["created"] += int(outcome.created)
                result["duplicates"] += int(outcome.duplicate)
                if outcome.disposition == "promotional":
                    result["promotional"] += 1
                elif outcome.disposition == "needs_review":
                    result["review"] += 1
                elif outcome.disposition == "suppressed":
                    result["suppressed"] += 1
            except Exception as error:
                db.session.rollback()
                current_app.logger.exception("Failed to ingest IMAP UID %s", uid_text)
                result["errors"].append(f"UID {uid_text}: {str(error)[:200]}")

        state = _state()
        state.last_success_at = utcnow()
        state.last_error = "\n".join(result["errors"][-5:]) or None
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.exception("Mailbox polling failed")
        state = _state()
        state.last_error = str(error)[:1000]
        db.session.commit()
        result["status"] = "error"
        result["errors"].append("Mailbox polling failed; inspect server logs for details.")
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass
    return result
