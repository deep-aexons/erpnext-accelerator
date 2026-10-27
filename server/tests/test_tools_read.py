import json
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

BASE = "http://erp.test"


def _q(route):
    return {k: v[0] for k, v in parse_qs(urlparse(str(route.calls.last.request.url)).query).items()}


@respx.mock
def test_ping(tools_env):
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x.com"})
    out = tools_env.ping()
    assert out["user"] == "api@x.com"
    assert out["url"] == BASE


@respx.mock
def test_list_docs_filters_encoding(tools_env):
    route = respx.get(f"{BASE}/api/resource/Sales%20Invoice").respond(json={"data": []})
    tools_env.list_docs("Sales Invoice", filters={"status": "Overdue"})
    q = _q(route)
    assert json.loads(q["filters"]) == {"status": "Overdue"}
    assert json.loads(q["fields"]) == ["name"]
    tools_env.list_docs("Sales Invoice", filters=[["status", "=", "Overdue"]], fields=["name", "grand_total"])
    q = _q(route)
    assert json.loads(q["filters"]) == [["status", "=", "Overdue"]]
    assert json.loads(q["fields"]) == ["name", "grand_total"]


@respx.mock
def test_list_docs_truncation(tools_env):
    route = respx.get(f"{BASE}/api/resource/Item").respond(json={"data": [{"name": i} for i in range(21)]})
    out = tools_env.list_docs("Item", limit=20)
    assert _q(route)["limit_page_length"] == "21"
    assert len(out["rows"]) == 20 and out["truncated"] is True and out["count"] == 20
    tools_env.list_docs("Item", limit=9999)
    assert _q(route)["limit_page_length"] == "501"


@respx.mock
def test_list_docs_not_truncated(tools_env):
    respx.get(f"{BASE}/api/resource/Item").respond(json={"data": [{"name": 1}]})
    out = tools_env.list_docs("Item")
    assert out["truncated"] is False


@respx.mock
def test_get_doc(tools_env):
    respx.get(f"{BASE}/api/resource/Customer/Acme%20Ltd").respond(json={"data": {"name": "Acme Ltd"}})
    assert tools_env.get_doc("Customer", "Acme Ltd") == {"name": "Acme Ltd"}


@respx.mock
def test_count_docs(tools_env):
    route = respx.get(f"{BASE}/api/method/frappe.client.get_count").respond(json={"message": 7})
    assert tools_env.count_docs("Item", {"disabled": 0}) == 7
    q = _q(route)
    assert q["doctype"] == "Item" and json.loads(q["filters"]) == {"disabled": 0}


@respx.mock
def test_schema_compact(tools_env):
    docs = [
        {"name": "Sales Order", "is_submittable": 1, "fields": [
            {"fieldname": "customer", "label": "Customer", "fieldtype": "Link", "options": "Customer", "reqd": 1},
            {"fieldname": "sb1", "fieldtype": "Section Break"},
            {"fieldname": "cb1", "fieldtype": "Column Break"},
            {"fieldname": "tab", "fieldtype": "Tab Break"},
            {"fieldname": "items", "label": "Items", "fieldtype": "Table", "options": "Sales Order Item"},
        ]},
        {"name": "Sales Order Item", "istable": 1, "fields": [
            {"fieldname": "item_code", "label": "Item", "fieldtype": "Link", "options": "Item", "reqd": 1},
        ]},
    ]
    respx.get(f"{BASE}/api/method/frappe.desk.form.load.getdoctype").respond(json={"docs": docs})
    out = tools_env.get_doctype_schema("Sales Order")
    names = [f["fieldname"] for f in out["fields"]]
    assert names == ["customer", "items"]
    assert out["fields"][0] == {"fieldname": "customer", "label": "Customer", "fieldtype": "Link",
                                "options": "Customer", "reqd": 1}
    assert out["is_submittable"] is True
    assert out["child_tables"]["Sales Order Item"][0]["fieldname"] == "item_code"


@respx.mock
def test_run_report_shapes_rows(tools_env):
    msg = {
        "columns": [{"fieldname": "party", "label": "Customer"}, "Outstanding:Currency:120"],
        "result": [{"party": "A", "outstanding": 10}, ["B", 20], ["C", 30]],
    }
    route = respx.get(f"{BASE}/api/method/frappe.desk.query_report.run").respond(json={"message": msg})
    out = tools_env.run_report("Accounts Receivable", {"company": "X"}, limit=2)
    assert out["columns"] == ["party", "Outstanding"]
    assert out["rows"][1] == {"party": "B", "Outstanding": 20}
    assert len(out["rows"]) == 2 and out["truncated"] is True and out["count"] == 3
    assert json.loads(_q(route)["filters"]) == {"company": "X"}


@respx.mock
def test_stock_levels(tools_env):
    route = respx.get(f"{BASE}/api/resource/Bin").respond(json={"data": [{"item_code": "I1", "actual_qty": 5}]})
    out = tools_env.get_stock_levels(item_code="I1")
    q = _q(route)
    assert json.loads(q["fields"]) == ["item_code", "warehouse", "actual_qty", "reserved_qty",
                                       "ordered_qty", "projected_qty"]
    assert json.loads(q["filters"]) == [["item_code", "=", "I1"], ["actual_qty", "!=", 0]]
    assert out["rows"][0]["item_code"] == "I1"
    tools_env.get_stock_levels(warehouse="Stores - X", include_zero=True)
    assert json.loads(_q(route)["filters"]) == [["warehouse", "=", "Stores - X"]]


def test_not_configured_tool_error():
    from erpnext_mcp import tools

    with pytest.raises(ToolError) as e:
        tools.ping()
    assert str(e.value) == "ERPNext not connected. Run /erp-connect."


@respx.mock
def test_erpnext_error_becomes_tool_error(tools_env):
    respx.get(f"{BASE}/api/resource/Item/X").respond(404, json={"exc_type": "DoesNotExistError",
                                                             "_server_messages": '["{\\"message\\": \\"Item X not found\\"}"]'})
    with pytest.raises(ToolError) as e:
        tools_env.get_doc("Item", "X")
    assert "Item X not found" in str(e.value)


# ---- final review fixes ------------------------------------------------------


@respx.mock
def test_run_report_prepared_report_is_explicit(tools_env):
    respx.get(f"{BASE}/api/method/frappe.desk.query_report.run").respond(
        json={"message": {"prepared_report": True, "doc": None}})
    with pytest.raises(ToolError) as e:
        tools_env.run_report("General Ledger", {"company": "X"})
    assert "prepared" in str(e.value).lower()
