"""Error types returned to Claude as short, readable messages."""

from __future__ import annotations

NOT_CONFIGURED_MESSAGE = "ERPNext not connected. Run /erp-connect."


class ERPNextError(Exception):
    """A failure talking to ERPNext, already reduced to one readable line."""

    def __init__(self, message: str, status: int | None = None, kind: str = "Error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind

    def __str__(self) -> str:
        return self.message


class NotConfiguredError(ERPNextError):
    def __init__(self, message: str = NOT_CONFIGURED_MESSAGE):
        super().__init__(message, kind="NotConfigured")


class GuardError(ERPNextError):
    """A safety rule refused the action before anything was sent."""

    def __init__(self, message: str):
        super().__init__(message, kind="Refused")


# --- Frappe error parsing -------------------------------------------------

import html as _html
import json as _json
import re as _re

_TAG = _re.compile(r"<[^>]+>")
_AUTH_HINT = " Check the API key/secret and the API user's roles, then run /erp-doctor."


def clean_text(text: str, limit: int = 400) -> str:
    text = _html.unescape(_TAG.sub("", text or ""))
    text = " ".join(text.split())
    return text[:limit]


def _server_messages(raw) -> list[str]:
    out: list[str] = []
    try:
        items = _json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        return out
    for item in items:
        try:
            msg = _json.loads(item) if isinstance(item, str) else item
        except ValueError:
            msg = item
        text = msg.get("message") if isinstance(msg, dict) else msg
        if text:
            out.append(clean_text(str(text)))
    return out


def parse_frappe_error(resp) -> ERPNextError:
    """Turn an error response from Frappe into one readable ERPNextError."""
    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = None

    kind = "HTTPError"
    parts: list[str] = []
    if isinstance(body, dict):
        kind = body.get("exc_type") or kind
        parts = _server_messages(body.get("_server_messages"))
        if not parts and isinstance(body.get("message"), str):
            parts = [clean_text(body["message"])]
        if not parts and body.get("exception"):
            last = str(body["exception"]).strip().splitlines()[-1]
            parts = [clean_text(last.split(":", 1)[-1])]
    elif resp.text:
        parts = [clean_text(resp.text, 200)]

    detail = " ".join(p for p in parts if p) or (resp.reason_phrase or "request failed")
    message = f"ERPNext error {status} ({kind}): {detail}"
    if status in (401, 403):
        message += _AUTH_HINT
    return ERPNextError(message, status=status, kind=kind)
