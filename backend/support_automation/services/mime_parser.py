"""MIME normalization for inbound customer mail."""

from __future__ import annotations

import hashlib
import re
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import Any


class _HTMLTextExtractor(HTMLParser):
    """Small dependency-free HTML-to-text converter for email alternatives."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self._ignored_depth += 1
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        return _normalize_text("".join(self.parts))


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    decoded: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            for encoding in (charset, "utf-8", "latin-1"):
                if encoding:
                    try:
                        decoded.append(chunk.decode(encoding, errors="replace"))
                        break
                    except (LookupError, UnicodeDecodeError):
                        continue
        else:
            decoded.append(chunk)
    return "".join(decoded).strip()


def _decode_payload(payload: bytes | None, charset: str | None) -> str:
    data = payload or b""
    for encoding in (charset, "utf-8", "latin-1"):
        if encoding:
            try:
                return data.decode(encoding, errors="replace")
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("utf-8", errors="replace")


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_quoted_reply(text: str) -> str:
    """Keep the latest response while retaining mail that lacks conventional quoting."""

    markers = (
        r"^On .+ wrote:$",
        r"^From:\s.+$",
        r"^-{2,}\s*Original Message\s*-{2,}$",
    )
    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        if any(re.match(marker, line.strip(), flags=re.IGNORECASE) for marker in markers):
            break
        kept.append(line)
    return _normalize_text("\n".join(kept))


def _message_id(value: str | None, raw: bytes) -> str:
    candidate = (value or "").strip()
    if candidate:
        return candidate if candidate.startswith("<") else f"<{candidate}>"
    return f"<generated-{hashlib.sha256(raw).hexdigest()[:32]}@ingested.local>"


def _address_list(value: str | None) -> list[str]:
    return [email.lower() for _, email in getaddresses([value or ""]) if email]


_AUTOMATED_LOCAL_PART = re.compile(
    r"^(?:mailer-daemon|postmaster|daemon|bounce(?:s)?|no[-_.]?reply|do[-_.]?not[-_.]?reply)(?:[+._-].*)?$",
    flags=re.IGNORECASE,
)
_LIST_HEADERS = (
    "List-Id",
    "List-Help",
    "List-Subscribe",
    "List-Unsubscribe",
    "List-Post",
    "List-Owner",
    "List-Archive",
    "Mailing-List",
)
_TRACKED_HEADERS = (
    "Date",
    "From",
    "To",
    "Cc",
    "Message-ID",
    "In-Reply-To",
    "References",
    "Auto-Submitted",
    "Precedence",
    *_LIST_HEADERS,
)
_BULK_PRECEDENCE = {"bulk", "list", "junk"}


def _normalise_email(value: str) -> str:
    _, address = parseaddr(value)
    normalized = address.strip().lower()
    local_part, separator, domain = normalized.partition("@")
    return normalized if separator and local_part and domain else ""


def _is_human_ack_eligible(
    message: Any, sender: str, support_addresses: list[str] | tuple[str, ...]
) -> bool:
    """Return true only when no observed automated or bulk-mail signal is present.

    This is intentionally not identity verification: mail headers can be forged.
    It is a conservative guard against acknowledgement loops and bulk-mail replies.
    """

    if sender == "unknown@example.invalid":
        return False
    configured_support_addresses = {_normalise_email(address) for address in support_addresses}
    configured_support_addresses.discard("")
    if sender in configured_support_addresses:
        return False
    if _AUTOMATED_LOCAL_PART.fullmatch(sender.partition("@")[0]):
        return False
    if message.get("Auto-Submitted"):
        return False
    if str(message.get("Precedence", "")).strip().lower() in _BULK_PRECEDENCE:
        return False
    return not any(message.get(header) for header in _LIST_HEADERS)


def parse_rfc822(
    raw: bytes, *, support_addresses: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    """Parse RFC 5322 mail and derive a conservative auto-reply eligibility flag."""

    message = BytesParser(policy=policy.default).parsebytes(raw)
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type().lower()
        disposition = part.get_content_disposition()
        filename = _decode_header(part.get_filename())
        payload = part.get_payload(decode=True) or b""

        if filename or disposition == "attachment":
            attachments.append(
                {
                    "filename": filename or "attachment.bin",
                    "content_type": content_type,
                    "data": payload,
                }
            )
            continue
        if content_type == "text/plain":
            plain_parts.append(_decode_payload(payload, part.get_content_charset()))
        elif content_type == "text/html":
            html_parts.append(_decode_payload(payload, part.get_content_charset()))

    body_html = "\n".join(html_parts).strip() or None
    if plain_parts:
        body_text = _normalize_text("\n".join(plain_parts))
    elif body_html:
        extractor = _HTMLTextExtractor()
        extractor.feed(body_html)
        body_text = extractor.text()
    else:
        body_text = ""

    from_name, from_email = parseaddr(str(message.get("From", "")))
    sender = _normalise_email(from_email) or "unknown@example.invalid"
    headers = {
        key: _decode_header(str(message.get(key, "")))
        for key in _TRACKED_HEADERS
        if message.get(key)
    }
    automated_sender = bool(_AUTOMATED_LOCAL_PART.fullmatch(sender.partition("@")[0]))
    return {
        "sender": sender,
        "sender_name": _decode_header(from_name),
        "recipients": _address_list(str(message.get("To", ""))),
        "cc": _address_list(str(message.get("Cc", ""))),
        "subject": _decode_header(str(message.get("Subject", ""))) or "(no subject)",
        "body_text": _strip_quoted_reply(body_text),
        "body_html": body_html,
        "message_id": _message_id(str(message.get("Message-ID", "")), raw),
        "in_reply_to": _decode_header(str(message.get("In-Reply-To", ""))) or None,
        "references": _decode_header(str(message.get("References", ""))) or None,
        "headers": headers,
        "attachments": attachments,
        "is_automated": bool(message.get("Auto-Submitted")) or automated_sender,
        "is_human_ack_eligible": _is_human_ack_eligible(message, sender, support_addresses),
    }
