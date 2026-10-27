"""MCP tools exposed to Claude. Thin: config -> guards -> client -> shaped result."""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable
from dataclasses import replace as _replace
from typing import Any
from urllib.parse import quote as _quote

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .client import ERPNextClient
from .config import (
    Profile,
    list_profiles,
    load_profile,
    normalize_url,
    save_profile,
    set_default_profile,
)
from .errors import ERPNextError, GuardError
from .guards import (
    audit,
    check_draft_update,
    check_safe_values,
    check_submit_allowed,
    check_writable,
    sanitize_new_doc,
)

mcp = FastMCP(
    "erpnext",
    instructions=(
        "Tools for reading and drafting data in an ERPNext site. Prefer specific tools "
        "(get_stock_levels, run_report) over generic list_docs. Use get_doctype_schema before "
        "creating documents if you are unsure of field names. Never submit or cancel without "
        "showing the user a summary and getting an explicit yes."
    ),
)

MAX_LIMIT = 500
LAYOUT_FIELDTYPES = frozenset({"Section Break", "Column Break", "Tab Break", "Fold", "Heading", "HTML", "Button"})
BIN_FIELDS = ["item_code", "warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"]

_client_factory: Callable[[Profile], ERPNextClient] = ERPNextClient


def set_client_factory(fn: Callable[[Profile], ERPNextClient]) -> None:
    """Test hook: replace how clients are built."""
    global _client_factory
    _client_factory = fn


def _connect(profile: str | None = None) -> tuple[Profile, ERPNextClient]:
    prof = load_profile(profile)
    return prof, _client_factory(prof)


def _tool_errors(fn):
    """Convert ERPNextError into ToolError so Claude gets one clean line."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ERPNextError as exc:
            raise ToolError(str(exc)) from None

    return wrapper


def _tool(fn):
    wrapped = _tool_errors(fn)
    mcp.tool()(wrapped)
    return wrapped


def _clamp(limit: int, default: int) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, MAX_LIMIT))


def _shape(rows: list, limit: int) -> dict:
    return {"rows": rows[:limit], "count": min(len(rows), limit), "truncated": len(rows) > limit}


def _list(client: ERPNextClient, doctype: str, filters, fields, order_by, limit: int) -> dict:
    params: dict[str, Any] = {
        "fields": json.dumps(fields or ["name"]),
        "limit_page_length": limit + 1,
    }
    if filters:
        params["filters"] = json.dumps(filters)
    if order_by:
        params["order_by"] = order_by
    data = client.get(client.resource_path(doctype), params=params).get("data", [])
    return _shape(data, limit)


# ---------------------------------------------------------------- read tools


@_tool
def ping() -> dict:
    """Check the ERPNext connection. Returns the logged-in API user, site URL and profile name."""
    prof, client = _connect()
    with client:
        user = client.call_method("frappe.auth.get_logged_user")
    return {"user": user, "url": prof.url, "site": prof.site, "profile": prof.name}


@_tool
def list_docs(
    doctype: str,
    filters: dict | list | None = None,
    fields: list[str] | None = None,
    order_by: str | None = None,
    limit: int = 20,
) -> dict:
    """List records of any ERPNext DocType.

    filters: dict like {"status": "Overdue"} or list like [["grand_total", ">", 50000]].
    fields: e.g. ["name", "customer", "grand_total"] (default ["name"]).
    order_by: e.g. "posting_date desc". limit: max 500.
    Example: list_docs("Sales Invoice", {"status": "Overdue"}, ["name", "customer", "outstanding_amount"])
    Returns {"rows", "count", "truncated"}.
    """
    _, client = _connect()
    with client:
        return _list(client, doctype, filters, fields, order_by, _clamp(limit, 20))


@_tool
def get_doc(doctype: str, name: str) -> dict:
    """Fetch one full document including child tables. Example: get_doc("Sales Order", "SAL-ORD-2026-00001")."""
    _, client = _connect()
    with client:
        return client.get(client.resource_path(doctype, name)).get("data", {})


@_tool
def count_docs(doctype: str, filters: dict | list | None = None) -> int:
    """Count records matching filters. Example: count_docs("Sales Invoice", {"status": "Overdue"})."""
    _, client = _connect()
    with client:
        return client.call_method("frappe.client.get_count", doctype=doctype, filters=json.dumps(filters or {}))


def _compact_fields(fields: list[dict]) -> list[dict]:
    out = []
    for f in fields or []:
        if f.get("fieldtype") in LAYOUT_FIELDTYPES:
            continue
        out.append({
            "fieldname": f.get("fieldname"),
            "label": f.get("label"),
            "fieldtype": f.get("fieldtype"),
            "options": f.get("options"),
            "reqd": int(f.get("reqd") or 0),
        })
    return out


@_tool
def get_doctype_schema(doctype: str) -> dict:
    """Get the fields of a DocType (and its child tables) so you use correct fieldnames.

    Use before create_draft/update_draft when unsure. Example: get_doctype_schema("Sales Order").
    """
    _, client = _connect()
    with client:
        body = client.get("/api/method/frappe.desk.form.load.getdoctype", params={"doctype": doctype})
    docs = body.get("docs") or []
    if not docs:
        raise ToolError(f"No schema returned for DocType '{doctype}'.")
    main = next((d for d in docs if d.get("name") == doctype), docs[0])
    return {
        "doctype": main.get("name"),
        "is_submittable": bool(main.get("is_submittable")),
        "fields": _compact_fields(main.get("fields")),
        "child_tables": {d["name"]: _compact_fields(d.get("fields")) for d in docs if d is not main},
    }


def _column_name(col) -> str:
    if isinstance(col, dict):
        return col.get("fieldname") or col.get("label") or ""
    return str(col).split(":", 1)[0]


@_tool
def run_report(report_name: str, filters: dict | None = None, limit: int = 100) -> dict:
    """Run an ERPNext report (Script or Query report) and return its rows.

    Examples: run_report("Accounts Receivable", {"company": "My Co", "report_date": "2026-10-27"}),
    run_report("Stock Balance", {"company": "My Co", "from_date": "2026-10-01", "to_date": "2026-10-27"}).
    If ERPNext says a filter is missing, add it and call again.
    """
    limit = _clamp(limit, 100)
    _, client = _connect()
    with client:
        msg = client.call_method("frappe.desk.query_report.run", report_name=report_name,
                                 filters=json.dumps(filters or {})) or {}
    if msg.get("prepared_report"):
        raise ToolError(
            f"'{report_name}' is a prepared (background) report, so it cannot be run directly here. "
            "Open it in ERPNext, or answer from list_docs on the underlying DocType instead."
        )
    columns = [_column_name(c) for c in msg.get("columns") or []]
    rows = []
    for r in msg.get("result") or []:
        rows.append(dict(zip(columns, r)) if isinstance(r, (list, tuple)) else r)
    return {"report": report_name, "columns": columns, "rows": rows[:limit],
            "count": len(rows), "truncated": len(rows) > limit}


@_tool
def get_stock_levels(
    item_code: str | None = None,
    warehouse: str | None = None,
    include_zero: bool = False,
    limit: int = 100,
) -> dict:
    """Current stock per item and warehouse (live, from Bin).

    Use for "available stock", "how many X do we have", "stock in warehouse Y".
    Returns actual_qty (on hand), reserved_qty, ordered_qty and projected_qty.
    """
    filters: list[list] = []
    if item_code:
        filters.append(["item_code", "=", item_code])
    if warehouse:
        filters.append(["warehouse", "=", warehouse])
    if not include_zero:
        filters.append(["actual_qty", "!=", 0])
    _, client = _connect()
    with client:
        return _list(client, "Bin", filters, BIN_FIELDS, "item_code asc", _clamp(limit, 100))


# --------------------------------------------------------------- write tools

def _desk_url(prof: Profile, doctype: str, name: str) -> str:
    return f"{prof.url}/app/{doctype.lower().replace(' ', '-')}/{_quote(str(name), safe='')}"


def _audited(tool: str, prof: Profile, doctype: str, name: str | None, action: Callable[[], Any]) -> Any:
    try:
        result = action()
    except GuardError:
        audit(prof.name, tool, doctype, name, "refused")
        raise
    except ERPNextError:
        audit(prof.name, tool, doctype, name, "error")
        raise
    audit(prof.name, tool, doctype, name, "ok")
    return result


@_tool
def create_draft(doctype: str, data: dict) -> dict:
    """Create a NEW document as a draft (docstatus 0). Never submits.

    Before calling: look up real customer/item names, show the user what you will create and get a yes.
    Example: create_draft("Sales Order", {"customer": "Acme", "delivery_date": "2026-11-05",
    "items": [{"item_code": "ITEM-001", "qty": 10}]})
    Returns {"name", "doctype", "docstatus", "url"}.
    """
    prof, client = _connect()

    def action():
        check_writable(doctype)
        check_safe_values(doctype, data)
        with client:
            doc = client.post(client.resource_path(doctype), json=sanitize_new_doc(data)).get("data", {})
        return {"name": doc.get("name"), "doctype": doctype, "docstatus": doc.get("docstatus", 0),
                "url": _desk_url(prof, doctype, doc.get("name"))}

    return _audited("create_draft", prof, doctype, None, action)


@_tool
def update_draft(doctype: str, name: str, data: dict) -> dict:
    """Change fields on an existing DRAFT document. Refuses submitted/cancelled documents.

    Example: update_draft("Sales Order", "SAL-ORD-2026-00001", {"delivery_date": "2026-11-10"})
    """
    prof, client = _connect()

    def action():
        check_writable(doctype)
        check_safe_values(doctype, data)
        with client:
            path = client.resource_path(doctype, name)
            current = client.get(path).get("data", {})
            check_draft_update(current, data)
            doc = client.put(path, json=data).get("data", {})
        return {"name": doc.get("name", name), "doctype": doctype, "docstatus": doc.get("docstatus", 0),
                "modified": doc.get("modified"), "url": _desk_url(prof, doctype, name)}

    return _audited("update_draft", prof, doctype, name, action)


@_tool
def submit_doc(doctype: str, name: str, confirm: bool = False) -> dict:
    """Submit a draft document (makes it final; posts ledger entries).

    Only when the user explicitly asked. First show a summary and get a clear yes, then call with
    confirm=true. Also requires submit to be enabled for this profile.
    """
    prof, client = _connect()

    def action():
        check_writable(doctype)
        check_submit_allowed(prof, confirm)
        with client:
            doc = client.get(client.resource_path(doctype, name)).get("data", {})
            out = client.call_method("frappe.client.submit", http="POST", doc=doc) or {}
        return {"name": out.get("name", name), "doctype": doctype, "docstatus": out.get("docstatus")}

    return _audited("submit_doc", prof, doctype, name, action)


@_tool
def cancel_doc(doctype: str, name: str, confirm: bool = False) -> dict:
    """Cancel a submitted document. Same rules as submit_doc: explicit user yes, confirm=true, submit enabled."""
    prof, client = _connect()

    def action():
        check_writable(doctype)
        check_submit_allowed(prof, confirm)
        with client:
            out = client.call_method("frappe.client.cancel", http="POST", doctype=doctype, name=name) or {}
        return {"name": out.get("name", name), "doctype": doctype, "docstatus": out.get("docstatus")}

    return _audited("cancel_doc", prof, doctype, name, action)


# --------------------------------------------------------------- setup tools


def save_connection(
    profile: str,
    url: str,
    api_key: str,
    api_secret: str,
    site: str | None = None,
    make_default: bool = True,
    allow_submit: bool | None = None,
) -> dict:
    """Verify credentials with a login check, then save the profile.

    allow_submit=None keeps the profile's current setting (False for a new profile).
    Only the terminal setup and config/env can turn submit on; the MCP tool cannot.
    """
    if allow_submit is None:
        allow_submit = profile in list_profiles() and load_profile(profile).allow_submit
    prof = Profile(name=profile, url=normalize_url(url), api_key=api_key, api_secret=api_secret,
                   site=site or None, allow_submit=bool(allow_submit), max_retries=1)
    try:
        with _client_factory(prof) as client:
            user = client.call_method("frappe.auth.get_logged_user")
    except ERPNextError as exc:
        raise ERPNextError(f"Could not connect, nothing saved. {exc}") from None
    path = save_profile(_replace(prof, max_retries=3), make_default=make_default)
    return {"profile": profile, "user": user, "url": prof.url, "site": prof.site,
            "allow_submit": prof.allow_submit, "default": make_default, "saved_to": str(path)}


@_tool
def connect(
    profile: str,
    url: str,
    api_key: str,
    api_secret: str,
    site: str | None = None,
    make_default: bool = True,
) -> dict:
    """Save an ERPNext connection profile after checking it works. Used by /erp-connect.

    site: optional site name sent as Host header (e.g. "erpnext.localhost" on a multi-site bench).
    Keeps the profile's existing submit setting; submit can only be enabled by the user in a terminal.
    """
    return save_connection(profile, url, api_key, api_secret, site=site, make_default=make_default)


@_tool
def use_profile(profile: str) -> dict:
    """Switch the default ERPNext profile (site). Example: use_profile("prod")."""
    env_profile = os.environ.get("ERPNEXT_PROFILE")
    if env_profile and env_profile != profile:
        raise ToolError(
            f"ERPNEXT_PROFILE={env_profile} is set in the environment and overrides the saved default. "
            "Ask the user to unset it to switch profiles."
        )
    set_default_profile(profile)
    prof = load_profile(profile)
    return {"profile": prof.name, "url": prof.url, "known_profiles": list_profiles()}


def _check(name: str, fn: Callable[[], str]) -> dict:
    try:
        return {"name": name, "ok": True, "detail": fn()}
    except ERPNextError as exc:
        return {"name": name, "ok": False, "detail": str(exc)}


@_tool
def diagnose(profile: str | None = None) -> dict:
    """Health check for the ERPNext connection (used by /erp-doctor). Never raises; returns checks."""
    checks: list[dict] = []
    try:
        prof = load_profile(profile)
    except ERPNextError as exc:
        return {"ok": False, "checks": [{"name": "config", "ok": False, "detail": str(exc)}]}
    checks.append({"name": "config", "ok": True,
                   "detail": f"profile '{prof.name}' -> {prof.url}" + (f" (site {prof.site})" if prof.site else "")})

    with _client_factory(_replace(prof, max_retries=0)) as client:
        reach = _check("reachable", lambda: f"ping -> {client.call_method('ping')}")
        checks.append(reach)
        if reach["ok"]:
            checks.append(_check("auth", lambda: f"logged in as {client.call_method('frappe.auth.get_logged_user')}"))

            def versions() -> str:
                v = client.call_method("frappe.utils.change_log.get_versions") or {}
                return ", ".join(f"{app} {info.get('version')}" for app, info in v.items())

            checks.append(_check("versions", versions))
            for dt in ("Item", "Customer", "Sales Order", "Bin"):
                checks.append(_check(f"read:{dt}", lambda dt=dt: (
                    client.get(client.resource_path(dt), params={"limit_page_length": 1}) and "ok")))
    checks.append({"name": "submit", "ok": True,
                   "detail": "enabled (needs confirm=true)" if prof.allow_submit else "disabled (drafts only)"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
