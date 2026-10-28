import json

import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

BASE = "http://erp.test"


def _audit(home):
    p = home / "audit.log"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


@respx.mock
def test_create_draft_forces_docstatus_zero(tools_env, home):
    route = respx.post(f"{BASE}/api/resource/Sales%20Order").respond(
        json={"data": {"name": "SO-0001", "docstatus": 0}})
    out = tools_env.create_draft("Sales Order", {"customer": "Acme", "docstatus": 1})
    assert json.loads(route.calls.last.request.content)["docstatus"] == 0
    assert out == {"name": "SO-0001", "doctype": "Sales Order", "docstatus": 0,
                   "url": f"{BASE}/app/sales-order/SO-0001"}
    assert _audit(home)[-1]["outcome"] == "ok"


@respx.mock
def test_create_draft_blocklisted(tools_env, home):
    route = respx.post(f"{BASE}/api/resource/User")
    with pytest.raises(ToolError):
        tools_env.create_draft("User", {"email": "x@y.z"})
    assert not route.called
    assert _audit(home)[-1]["outcome"] == "refused"


@respx.mock
def test_create_draft_error_audited(tools_env, home):
    respx.post(f"{BASE}/api/resource/Item").respond(417, json={"exc_type": "ValidationError"})
    with pytest.raises(ToolError):
        tools_env.create_draft("Item", {"item_code": "X"})
    assert _audit(home)[-1]["outcome"] == "error"


@respx.mock
def test_update_draft_checks_status(tools_env):
    respx.get(f"{BASE}/api/resource/Sales%20Order/SO-1").respond(json={"data": {"name": "SO-1", "docstatus": 1}})
    put = respx.put(f"{BASE}/api/resource/Sales%20Order/SO-1")
    with pytest.raises(ToolError):
        tools_env.update_draft("Sales Order", "SO-1", {"delivery_date": "2026-11-01"})
    assert not put.called


@respx.mock
def test_update_draft_ok(tools_env):
    respx.get(f"{BASE}/api/resource/Sales%20Order/SO-1").respond(json={"data": {"name": "SO-1", "docstatus": 0}})
    put = respx.put(f"{BASE}/api/resource/Sales%20Order/SO-1").respond(
        json={"data": {"name": "SO-1", "docstatus": 0, "modified": "2026-10-27 10:00:00"}})
    out = tools_env.update_draft("Sales Order", "SO-1", {"delivery_date": "2026-11-01"})
    assert json.loads(put.calls.last.request.content) == {"delivery_date": "2026-11-01"}
    assert out["name"] == "SO-1" and out["docstatus"] == 0


@respx.mock
def test_submit_refused_without_confirm(tools_env, monkeypatch):
    monkeypatch.setenv("ERPNEXT_ALLOW_SUBMIT", "1")
    route = respx.post(f"{BASE}/api/method/frappe.client.submit")
    with pytest.raises(ToolError) as e:
        tools_env.submit_doc("Sales Order", "SO-1")
    assert "confirm=true" in str(e.value)
    assert not route.called


@respx.mock
def test_submit_refused_without_allow(tools_env):
    route = respx.post(f"{BASE}/api/method/frappe.client.submit")
    with pytest.raises(ToolError) as e:
        tools_env.submit_doc("Sales Order", "SO-1", confirm=True)
    assert "turned off" in str(e.value)
    assert not route.called


@respx.mock
def test_submit_ok(tools_env, monkeypatch, home):
    monkeypatch.setenv("ERPNEXT_ALLOW_SUBMIT", "1")
    doc = {"doctype": "Sales Order", "name": "SO-1", "docstatus": 0}
    respx.get(f"{BASE}/api/resource/Sales%20Order/SO-1").respond(json={"data": doc})
    route = respx.post(f"{BASE}/api/method/frappe.client.submit").respond(json={"message": {**doc, "docstatus": 1}})
    out = tools_env.submit_doc("Sales Order", "SO-1", confirm=True)
    assert json.loads(route.calls.last.request.content) == {"doc": doc}
    assert out == {"name": "SO-1", "doctype": "Sales Order", "docstatus": 1}
    assert _audit(home)[-1]["tool"] == "submit_doc" and _audit(home)[-1]["outcome"] == "ok"


@respx.mock
def test_cancel_ok(tools_env, monkeypatch):
    monkeypatch.setenv("ERPNEXT_ALLOW_SUBMIT", "1")
    route = respx.post(f"{BASE}/api/method/frappe.client.cancel").respond(
        json={"message": {"name": "SO-1", "docstatus": 2}})
    out = tools_env.cancel_doc("Sales Order", "SO-1", confirm=True)
    assert json.loads(route.calls.last.request.content) == {"doctype": "Sales Order", "name": "SO-1"}
    assert out["docstatus"] == 2


async def test_no_delete_tool():
    from erpnext_mcp.tools import mcp

    names = [t.name for t in await mcp.list_tools()]
    assert not any("delete" in n for n in names)
    assert len(names) == 25


# ---- final review fixes ------------------------------------------------------


@respx.mock
def test_create_draft_refuses_script_in_custom_field(tools_env, home):
    route = respx.post(f"{BASE}/api/resource/Custom%20Field")
    with pytest.raises(ToolError):
        tools_env.create_draft("Custom Field", {"dt": "Sales Order", "fieldname": "x", "fieldtype": "HTML",
                                                "options": "<script>evil()</script>"})
    assert not route.called
    assert _audit(home)[-1]["outcome"] == "refused"


@respx.mock
def test_update_draft_refuses_script_values(tools_env):
    respx.get(f"{BASE}/api/resource/Property%20Setter/PS-1").respond(json={"data": {"docstatus": 0}})
    put = respx.put(f"{BASE}/api/resource/Property%20Setter/PS-1")
    with pytest.raises(ToolError):
        tools_env.update_draft("Property Setter", "PS-1", {"value": "eval:frappe.boot"})
    assert not put.called
