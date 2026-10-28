"""Daily briefing: sales, cash, overdue invoices, low stock and pending approvals in one call."""

from __future__ import annotations

import json
from datetime import date as _date

from mcp.server.fastmcp.exceptions import ToolError

from .client import ERPNextClient
from .errors import ERPNextError
from .tools import _chunks, _connect, _fetch_all, _tool

__all__ = ["daily_briefing"]

MAX_ROWS = 5000
TOP = 5
LOW_STOCK_TOP = 20
DRAFT_DOCTYPES = ("Sales Order", "Purchase Order", "Purchase Invoice", "Sales Invoice", "Material Request")


def _rows(client: ERPNextClient, doctype: str, filters, fields, parent: str | None = None) -> tuple[list, bool]:
    return _fetch_all(client, doctype, filters, fields, parent=parent, max_rows=MAX_ROWS)


def _sum(rows, field) -> float:
    return round(sum(float(r.get(field) or 0) for r in rows), 2)


def _sales(client, company, day) -> dict:
    inv, inv_t = _rows(client, "Sales Invoice",
                       [["docstatus", "=", 1], ["posting_date", "=", day], ["company", "=", company]],
                       ["base_grand_total"])
    so, so_t = _rows(client, "Sales Order",
                     [["docstatus", "=", 1], ["transaction_date", "=", day], ["company", "=", company]],
                     ["base_grand_total"])
    return {"invoices": {"count": len(inv), "total": _sum(inv, "base_grand_total"), "truncated": inv_t},
            "orders": {"count": len(so), "total": _sum(so, "base_grand_total"), "truncated": so_t}}


def _cash(client, company, day) -> dict:
    accounts_meta, _ = _rows(client, "Account",
                             [["company", "=", company], ["account_type", "in", ["Bank", "Cash"]], ["is_group", "=", 0]],
                             ["name", "account_type", "account_currency"])
    if not accounts_meta:
        return {"accounts": [], "totals": {}, "total": 0.0, "note": "No Bank or Cash accounts found."}
    meta = {a["name"]: a for a in accounts_meta}
    accounts = []
    for account_type in sorted({a["account_type"] for a in accounts_meta}):
        msg = client.call_method("frappe.desk.query_report.run", report_name="Account Balance",
                                 filters=json.dumps({"company": company, "report_date": day,
                                                     "account_type": account_type})) or {}
        if msg.get("prepared_report"):
            raise ERPNextError("Account Balance is a prepared (background) report on this site")
        for r in msg.get("result") or []:
            if isinstance(r, dict) and r.get("account") in meta:  # leaf accounts only: no group double counting
                accounts.append({"account": r["account"], "type": account_type,
                                 "balance": round(float(r.get("balance") or 0), 2),
                                 "currency": r.get("currency") or meta[r["account"]].get("account_currency")})
    totals: dict[str, float] = {}
    for a in accounts:
        totals[a["currency"]] = round(totals.get(a["currency"], 0.0) + a["balance"], 2)
    return {"accounts": accounts, "totals": totals, "total": next(iter(totals.values())) if len(totals) == 1 else None}


def _overdue(client, doctype, party_field, company) -> dict:
    rows, truncated = _rows(client, doctype,
                            [["docstatus", "=", 1], ["status", "=", "Overdue"], ["company", "=", company]],
                            [party_field, "outstanding_amount", "due_date"])
    by_party: dict[str, dict] = {}
    for r in rows:
        p = by_party.setdefault(r.get(party_field), {"party": r.get(party_field), "amount": 0.0, "oldest_due": None})
        p["amount"] = round(p["amount"] + float(r.get("outstanding_amount") or 0), 2)
        due = r.get("due_date")
        if due and (p["oldest_due"] is None or due < p["oldest_due"]):
            p["oldest_due"] = due
    top = sorted(by_party.values(), key=lambda p: p["amount"], reverse=True)[:TOP]
    return {"count": len(rows), "total": _sum(rows, "outstanding_amount"), "top": top, "truncated": truncated}


def _low_stock(client) -> dict:
    levels, truncated = _rows(client, "Item Reorder", [["warehouse_reorder_level", ">", 0]],
                              ["parent", "warehouse", "warehouse_reorder_level", "warehouse_reorder_qty"], parent="Item")
    if not levels:
        return {"items": [], "note": "No reorder levels are set on Items.", "truncated": False}
    bins: list[dict] = []
    for chunk in _chunks(sorted({lv["parent"] for lv in levels})):
        part, _ = _rows(client, "Bin", [["item_code", "in", chunk]], ["item_code", "warehouse", "actual_qty", "projected_qty"])
        bins += part
    qty = {(b["item_code"], b["warehouse"]): float(b.get("actual_qty") or 0) for b in bins}
    low = []
    for lv in levels:
        level = float(lv.get("warehouse_reorder_level") or 0)
        on_hand = qty.get((lv["parent"], lv.get("warehouse")), 0.0)
        if on_hand < level:
            low.append({"item_code": lv["parent"], "warehouse": lv.get("warehouse"), "actual_qty": on_hand,
                        "reorder_level": level, "reorder_qty": float(lv.get("warehouse_reorder_qty") or 0),
                        "shortfall": round(level - on_hand, 3)})
    low.sort(key=lambda x: x["shortfall"], reverse=True)
    return {"items": low[:LOW_STOCK_TOP], "count": len(low), "truncated": truncated}


def _approvals(client, company) -> dict:
    actions, truncated = _rows(client, "Workflow Action", [["status", "=", "Open"]], ["reference_doctype"])
    workflow: dict[str, int] = {}
    for a in actions:
        workflow[a["reference_doctype"]] = workflow.get(a["reference_doctype"], 0) + 1
    drafts = {dt: client.call_method("frappe.client.get_count", doctype=dt,
                                     filters=json.dumps([["docstatus", "=", 0], ["company", "=", company]]))
              for dt in DRAFT_DOCTYPES}
    return {"workflow_open": workflow, "drafts": drafts, "truncated": truncated}


def collect(client: ERPNextClient, company: str, day: str) -> dict:
    parts = {
        "sales": lambda: _sales(client, company, day),
        "cash": lambda: _cash(client, company, day),
        "receivables_overdue": lambda: _overdue(client, "Sales Invoice", "customer", company),
        "payables_overdue": lambda: _overdue(client, "Purchase Invoice", "supplier", company),
        "low_stock": lambda: _low_stock(client),
        "approvals": lambda: _approvals(client, company),
    }
    sections = {}
    for key, fn in parts.items():
        try:
            sections[key] = fn()
        except ERPNextError as exc:
            sections[key] = {"error": str(exc)}
    return sections


def _money(x) -> str:
    return f"{float(x or 0):,.2f}"


def _partial(d: dict) -> str:
    return f" _(partial: first {MAX_ROWS} rows)_" if d.get("truncated") else ""


def render_markdown(b: dict) -> str:
    s = b.get("sections", {})
    out = [f"# Daily briefing — {b.get('company')} — {b.get('date')}", ""]

    def section(title, key, body):
        out.append(f"## {title}")
        data = s.get(key)
        if data is None:
            return
        if "error" in data:
            out.append(f"_unavailable: {data['error']}_")
        else:
            out.extend(body(data))
        out.append("")

    section("Sales", "sales", lambda d: [
        f"- Invoices today: {d['invoices']['count']} · {_money(d['invoices']['total'])}{_partial(d['invoices'])}",
        f"- Orders today: {d['orders']['count']} · {_money(d['orders']['total'])}{_partial(d['orders'])}"])
    section("Cash", "cash", lambda d: [f"- {a['account']}: {_money(a['balance'])} {a.get('currency') or ''}".rstrip()
                                       for a in d["accounts"]]
            + [f"- **Total {cur}: {_money(amt)}**" for cur, amt in (d.get("totals") or {}).items()]
            + ([f"- {d['note']}"] if d.get("note") else []))
    for title, key in (("Overdue receivables", "receivables_overdue"), ("Overdue payables", "payables_overdue")):
        section(title, key, lambda d: [f"- {d['count']} invoices · {_money(d['total'])}{_partial(d)}"]
                + [f"  - {p['party']}: {_money(p['amount'])} (oldest due {p['oldest_due']})" for p in d["top"]])
    section("Low stock", "low_stock", lambda d: ([f"- {i['item_code']} @ {i['warehouse']}: {i['actual_qty']:g} "
                                                   f"(reorder level {i['reorder_level']:g})" for i in d["items"]]
                                                  or [f"- {d.get('note') or 'Nothing below reorder level.'}"]))
    section("Approvals and drafts", "approvals", lambda d: (
        [f"- Waiting for approval: {dt} × {n}" for dt, n in d["workflow_open"].items()]
        + ["- Drafts: " + ", ".join(f"{dt} {n}" for dt, n in d["drafts"].items() if n)]))
    return "\n".join(out).rstrip() + "\n"


def _resolve_company(client: ERPNextClient, company: str | None) -> str:
    if company:
        return company
    names = [r["name"] for r in client.get(client.resource_path("Company"),
                                           params={"fields": json.dumps(["name"]), "limit_page_length": 50})
             .get("data", [])]
    if len(names) == 1:
        return names[0]
    if not names:
        raise ToolError("No Company found in ERPNext.")
    raise ToolError(f"Several companies exist; pass company= one of: {', '.join(names)}")


@_tool
def daily_briefing(company: str | None = None, date: str | None = None) -> dict:
    """Morning summary: today's sales, bank/cash balances, overdue receivables and payables, low stock,
    pending workflow approvals and open drafts. One call; a failing section does not stop the others.

    date: YYYY-MM-DD (default today). company: needed only when the site has several companies.
    Returns {company, date, sections, markdown}.
    """
    day = date or _date.today().isoformat()
    _, client = _connect()
    with client:
        company = _resolve_company(client, company)
        sections = collect(client, company, day)
    result = {"company": company, "date": day, "sections": sections}
    result["markdown"] = render_markdown(result)
    return result
