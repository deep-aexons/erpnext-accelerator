"""Safety rules applied before any write reaches ERPNext, plus the audit log."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from .config import Profile, config_home
from .errors import GuardError

log = logging.getLogger("erpnext_mcp.guards")

# DocTypes Claude may never create or change: security, permissions, code execution, system config.
WRITE_BLOCKLIST: frozenset[str] = frozenset({
    "User",
    "Role",
    "Role Profile",
    "Has Role",
    "DocType",
    "Custom DocPerm",
    "DocPerm",
    "System Settings",
    "OAuth Client",
    "OAuth Bearer Token",
    "Server Script",
    "Scheduled Job Type",
    "User Permission",
    "Module Def",
    "Website Settings",
    # code execution or side effects outside ERPNext
    "Client Script",
    "Report",
    "Print Format",
    "Web Page",
    "Web Form",
    "Webhook",
    "Notification",
    "Auto Email Report",
    "Email Queue",
    "Data Import",
    # auth / permissions
    "Custom Role",
    "Role Permission for Page and Report",
    "Social Login Key",
    "LDAP Settings",
    # India Compliance: GSP credentials and GST account mapping
    "GST Settings",
    "GST Credential",
})
_BLOCKED = frozenset(d.casefold() for d in WRITE_BLOCKLIST)

# Fields that ERPNext evaluates as code when set on Custom Field / Property Setter.
_CODE_CHECKED_DOCTYPES = frozenset({"custom field", "property setter"})
_IDENTITY_KEYS = frozenset({"name", "doctype", "owner", "creation", "modified", "amended_from"})


def check_writable(doctype: str) -> None:
    if doctype.strip().casefold() in _BLOCKED:
        raise GuardError(
            f"Writing to '{doctype}' is blocked by the ERPNext Accelerator safety rules. "
            "Ask a system manager to make this change in ERPNext directly."
        )


def check_safe_values(doctype: str, data: dict) -> None:
    """Refuse Custom Field / Property Setter values that would run code in the browser."""
    if doctype.strip().casefold() not in _CODE_CHECKED_DOCTYPES:
        return
    if str(data.get("fieldtype", "")).strip() == "HTML":
        raise GuardError("HTML fields can carry scripts, so the plugin will not create them. Add them in ERPNext directly.")
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        text = value.strip().lower()
        if text.startswith("eval:") or "<script" in text:
            raise GuardError(
                f"'{key}' contains code (eval:/script), which the plugin will not write. "
                "Give the user the value to set in ERPNext themselves."
            )


def sanitize_new_doc(data: dict) -> dict:
    out = dict(data)
    out["docstatus"] = 0
    return out


def check_draft_update(current: dict, changes: dict) -> None:
    bad = sorted(_IDENTITY_KEYS & set(changes))
    if bad:
        raise GuardError(f"update_draft cannot change {', '.join(bad)}. Only change business fields.")
    status = int(current.get("docstatus") or 0)
    if status != 0:
        state = {1: "submitted", 2: "cancelled"}.get(status, f"docstatus {status}")
        raise GuardError(
            f"This document is {state}; only drafts can be edited. "
            "Use an amendment in ERPNext instead."
        )
    if "docstatus" in changes and int(changes["docstatus"] or 0) != 0:
        raise GuardError("update_draft cannot change docstatus. Use submit_doc or cancel_doc.")


def check_submit_allowed(profile: Profile, confirm: bool) -> None:
    if not profile.allow_submit:
        raise GuardError(
            f"Submit/cancel is turned off for profile '{profile.name}'. Tell the user they can submit it in "
            "ERPNext, or enable submit themselves in a terminal with `erpnext-mcp setup` "
            "(or ERPNEXT_ALLOW_SUBMIT=1). You cannot enable it; then ask the user to confirm."
        )
    if confirm is not True:
        raise GuardError(
            "Submit/cancel needs confirm=true. First show a short summary of the document and ask the user "
            "to confirm, then call again with confirm=true."
        )


def check_gst_action_allowed(profile: Profile, confirm: bool) -> None:
    if not profile.allow_gst_actions:
        raise GuardError(
            f"e-invoice/e-way generation is turned off for profile '{profile.name}'. Tell the user they can generate "
            "it in ERPNext, or enable it themselves in a terminal with `erpnext-mcp setup` "
            "(or ERPNEXT_ALLOW_GST_ACTIONS=1). You cannot enable it."
        )
    if confirm is not True:
        raise GuardError(
            "e-invoice/e-way generation needs confirm=true. It is reported to the government portal and cannot be "
            "undone from here. Show the user the document summary, ask the user to confirm, then call again with confirm=true."
        )


def audit(profile: str, tool: str, doctype: str, name: str | None, outcome: str) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": profile,
        "tool": tool,
        "doctype": doctype,
        "name": name,
        "outcome": outcome,
    }
    try:
        home = config_home()
        home.mkdir(parents=True, exist_ok=True)
        fd = os.open(home / "audit.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:  # never let auditing break a tool call
        log.error("could not write audit log: %s", exc)
