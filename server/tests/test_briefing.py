import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from erpnext_mcp.briefing import render_markdown

BASE = "http://erp.test"
DAY = "2026-10-27"


def _q(request):
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


def _flt(request):
    return json.loads(_q(request).get("filters", "[]"))


def _has(flt, field, op=None, value=None):
    return any(f[0] == field and (op is None or f[1] == op) and (value is None or f[2] == value) for f in flt)


def _invoices(party):
    def handler(request):
        flt = _flt(request)
        if _has(flt, "status", "=", "Overdue"):
            return httpx.Response(200, json={"data": [
                {party: "A", "outstanding_amount": 500, "due_date": "2026-10-01"},
                {party: "A", "outstanding_amount": 250, "due_date": "2026-10-05"},
                {party: "B", "outstanding_amount": 100, "due_date": "2026-10-10"}]})
        return httpx.Response(200, json={"data": [{"base_grand_total": 1000}, {"base_grand_total": 180}]})
    return handler


def _report(request):
    flt = json.loads(_q(request)["filters"])
    rows = {"Bank": [{"account": "HDFC - C", "balance": 50000, "currency": "INR"}],
            "Cash": [{"account": "Cash - C", "balance": 2500, "currency": "INR"}]}[flt["account_type"]]
    return httpx.Response(200, json={"message": {"columns": [], "result": rows}})


def _count(request):
    return httpx.Response(200, json={"message": {"Sales Order": 2, "Purchase Order": 1}.get(_q(request)["doctype"], 0)})


def _mock_site(cash_status=200, empty=False):
    respx.get(f"{BASE}/api/resource/Company").respond(json={"data": [{"name": "C"}]})
    respx.get(f"{BASE}/api/resource/Sales%20Invoice").mock(side_effect=_invoices("customer"))
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice").mock(side_effect=_invoices("supplier"))
    respx.get(f"{BASE}/api/resource/Sales%20Order").respond(json={"data": [{"base_grand_total": 700}]})
    if cash_status is not None:
        respx.get(f"{BASE}/api/resource/Account", name="accounts").respond(json={"data": [
            {"name": "HDFC - C", "account_type": "Bank", "account_currency": "INR"},
            {"name": "Cash - C", "account_type": "Cash", "account_currency": "INR"}]})
    if cash_status == 200:
        respx.get(f"{BASE}/api/method/frappe.desk.query_report.run").mock(side_effect=_report)
    elif cash_status is not None:
        respx.get(f"{BASE}/api/method/frappe.desk.query_report.run").respond(cash_status, json={})
    respx.get(f"{BASE}/api/method/frappe.client.get_count").mock(side_effect=_count)
    if empty:
        respx.get(f"{BASE}/api/resource/Item%20Reorder").respond(json={"data": []})
        respx.get(f"{BASE}/api/resource/Workflow%20Action").respond(json={"data": []})
        return
    respx.get(f"{BASE}/api/resource/Item%20Reorder").respond(json={"data": [
        {"parent": "I1", "warehouse": "Stores - C", "warehouse_reorder_level": 10, "warehouse_reorder_qty": 50},
        {"parent": "I2", "warehouse": "Stores - C", "warehouse_reorder_level": 5, "warehouse_reorder_qty": 20},
        {"parent": "I3", "warehouse": "Stores - C", "warehouse_reorder_level": 4, "warehouse_reorder_qty": 8}]})
    respx.get(f"{BASE}/api/resource/Bin").respond(json={"data": [
        {"item_code": "I1", "warehouse": "Stores - C", "actual_qty": 3, "projected_qty": 3},
        {"item_code": "I2", "warehouse": "Stores - C", "actual_qty": 9, "projected_qty": 9}]})
    respx.get(f"{BASE}/api/resource/Workflow%20Action").respond(json={"data": [
        {"reference_doctype": "Purchase Order"}, {"reference_doctype": "Purchase Order"},
        {"reference_doctype": "Leave Application"}]})


@respx.mock
def test_sections_happy_path(tools_env):
    _mock_site()
    out = tools_env.daily_briefing(date=DAY)
    s = out["sections"]
    assert out["company"] == "C" and out["date"] == DAY
    assert s["sales"]["invoices"] == {"count": 2, "total": 1180.0, "truncated": False}
    assert s["sales"]["orders"]["total"] == 700.0
    assert s["cash"]["total"] == 52500.0 and len(s["cash"]["accounts"]) == 2
    assert s["receivables_overdue"]["count"] == 3 and s["receivables_overdue"]["total"] == 850.0
    assert s["receivables_overdue"]["top"][0] == {"party": "A", "amount": 750.0, "oldest_due": "2026-10-01"}
    assert s["payables_overdue"]["top"][1]["party"] == "B"
    low = s["low_stock"]["items"]
    assert [(i["item_code"], i["shortfall"]) for i in low] == [("I1", 7.0), ("I3", 4.0)]  # I3 has no Bin → 0 qty
    assert s["approvals"]["workflow_open"] == {"Purchase Order": 2, "Leave Application": 1}
    assert s["approvals"]["drafts"]["Sales Order"] == 2
    assert "# Daily briefing" in out["markdown"]


@respx.mock
def test_section_error_isolated(tools_env):
    _mock_site(cash_status=403)
    out = tools_env.daily_briefing(date=DAY)
    assert "error" in out["sections"]["cash"]
    assert "error" not in out["sections"]["sales"]
    assert "unavailable" in out["markdown"]


@respx.mock
def test_empty_site(tools_env):
    _mock_site(empty=True)
    s = tools_env.daily_briefing(date=DAY)["sections"]
    assert s["low_stock"]["items"] == [] and "error" not in s["low_stock"]
    assert s["approvals"]["workflow_open"] == {}


@respx.mock
def test_company_required_when_many(tools_env):
    respx.get(f"{BASE}/api/resource/Company").respond(json={"data": [{"name": "C1"}, {"name": "C2"}]})
    with pytest.raises(ToolError) as e:
        tools_env.daily_briefing()
    assert "C1" in str(e.value) and "C2" in str(e.value)


@respx.mock
def test_company_filter_applied(tools_env):
    _mock_site()
    tools_env.daily_briefing(company="C", date=DAY)
    req = next(c.request for c in respx.calls if c.request.url.path == "/api/resource/Sales Order")
    flt = _flt(req)
    assert ["company", "=", "C"] in flt and ["transaction_date", "=", DAY] in flt


def test_render_markdown_headings():
    md = render_markdown({"company": "C", "date": DAY, "sections": {
        "sales": {"error": "boom"}, "cash": {"accounts": [], "total": 0}}})
    assert md.startswith("# Daily briefing — C — 2026-10-27")
    assert "## Sales" in md and "unavailable: boom" in md and "## Cash" in md


@respx.mock
def test_cli_briefing(tools_env, capsys):
    from erpnext_mcp.__main__ import main

    _mock_site()
    assert main(["briefing", "--date", DAY]) == 0
    assert "# Daily briefing — C" in capsys.readouterr().out
    assert main(["briefing", "--date", DAY, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["company"] == "C"


# ---- final review fixes ------------------------------------------------------


@respx.mock
def test_cash_excludes_groups_and_totals_per_currency(tools_env):
    _mock_site(cash_status=None)
    acct = respx.get(f"{BASE}/api/resource/Account", name="accounts_usd").respond(json={"data": [
        {"name": "HDFC - C", "account_type": "Bank", "account_currency": "INR"},
        {"name": "Cash - C", "account_type": "Cash", "account_currency": "INR"},
        {"name": "Citi USD - C", "account_type": "Bank", "account_currency": "USD"}]})

    def report(request):
        flt = json.loads(_q(request)["filters"])
        rows = {"Bank": [{"account": "Bank Accounts - C", "balance": 50000, "currency": "INR"},
                         {"account": "HDFC - C", "balance": 50000, "currency": "INR"},
                         {"account": "Citi USD - C", "balance": 100, "currency": "USD"}],
                "Cash": [{"account": "Cash In Hand - C", "balance": 2500, "currency": "INR"},
                         {"account": "Cash - C", "balance": 2500, "currency": "INR"}]}[flt["account_type"]]
        return httpx.Response(200, json={"message": {"result": rows}})

    respx.get(f"{BASE}/api/method/frappe.desk.query_report.run").mock(side_effect=report)
    cash = tools_env.daily_briefing(date=DAY)["sections"]["cash"]
    assert cash["totals"] == {"INR": 52500.0, "USD": 100.0}
    assert cash["total"] is None  # mixed currencies: no single total
    assert {a["account"] for a in cash["accounts"]} == {"HDFC - C", "Cash - C", "Citi USD - C"}
    flt = json.loads(_q(acct.calls.last.request)["filters"])
    assert ["is_group", "=", 0] in flt and ["company", "=", "C"] in flt


@respx.mock
def test_truncated_marked_in_markdown(tools_env, monkeypatch):
    from erpnext_mcp import briefing

    monkeypatch.setattr(briefing, "MAX_ROWS", 1)
    _mock_site()
    out = tools_env.daily_briefing(date=DAY)
    assert out["sections"]["sales"]["invoices"]["truncated"] is True
    assert "partial" in out["markdown"]
