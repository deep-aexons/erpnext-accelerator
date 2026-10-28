import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from erpnext_mcp.gst import gstin_checksum

BASE = "http://erp.test"
FIXTURE = Path(__file__).parent / "fixtures" / "gstr2b_sample.json"
MH = "27AAACA1111A1Z" + gstin_checksum("27AAACA1111A1Z")
KA = "29AAACB2222B1Z" + gstin_checksum("29AAACB2222B1Z")
CO = "27AAACZ9999Z1Z" + gstin_checksum("27AAACZ9999Z1Z")
CGST9 = {"account_head": "Input CGST - C", "rate": 9, "tax_amount": 90}
SGST9 = {"account_head": "Input SGST - C", "rate": 9, "tax_amount": 90}
IC_METHOD = "india_compliance.gst_india.utils"


def _q(route):
    return {k: v[0] for k, v in parse_qs(urlparse(str(route.calls.last.request.url)).query).items()}


def _versions(ic: bool):
    apps = {"frappe": {"version": "17.0.0-dev"}, "erpnext": {"version": "17.0.0-dev"}}
    if ic:
        apps["india_compliance"] = {"version": "17.0.0-dev"}
    respx.get(f"{BASE}/api/method/frappe.utils.change_log.get_versions").respond(json={"message": apps})


@respx.mock
def test_capabilities_without_ic(tools_env):
    _versions(False)
    out = tools_env.gst_capabilities()
    assert out["india_compliance"] is False and out["hsn_field"] == "customs_tariff_number"


@respx.mock
def test_capabilities_with_ic(tools_env):
    _versions(True)
    out = tools_env.gst_capabilities()
    assert out["india_compliance"] is True and out["hsn_field"] == "gst_hsn_code"


def test_validate_gstin_tool(tools_env):
    assert tools_env.gst_validate_gstin(MH.lower())["valid"] is True


@respx.mock
def test_check_invoices_core_fields(tools_env):
    _versions(False)
    lst = respx.get(f"{BASE}/api/resource/Purchase%20Invoice").respond(json={"data": [{"name": "PI-1"}]})
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice/PI-1").respond(json={"data": {
        "doctype": "Purchase Invoice", "name": "PI-1", "company": "C", "supplier": "S1", "bill_no": "B1",
        "bill_date": "2026-09-01",
        "items": [{"item_code": "I1"}, {"item_code": "I2"}], "taxes": [CGST9, SGST9]}})
    respx.get(f"{BASE}/api/resource/Company").respond(json={"data": [{"name": "C", "tax_id": CO}]})
    respx.get(f"{BASE}/api/resource/Supplier").respond(json={"data": [{"name": "S1", "tax_id": MH}]})
    respx.get(f"{BASE}/api/resource/Item").respond(json={"data": [
        {"name": "I1", "customs_tariff_number": "8471"}, {"name": "I2", "customs_tariff_number": None}]})
    out = tools_env.gst_check_invoices("Purchase Invoice", "2026-09-01", "2026-09-30")
    assert out["invoices_checked"] == 1
    assert [(i["invoice"], i["check"]) for i in out["issues"]] == [("PI-1", "hsn_missing")]
    assert out["summary"]["errors"] == 1
    flt = json.loads(_q(lst)["filters"])
    assert ["posting_date", "between", ["2026-09-01", "2026-09-30"]] in flt and ["docstatus", "<", 2] in flt


@respx.mock
def test_check_invoices_party_without_tax_id(tools_env):
    _versions(False)
    respx.get(f"{BASE}/api/resource/Sales%20Invoice").respond(json={"data": [{"name": "SI-1"}]})
    respx.get(f"{BASE}/api/resource/Sales%20Invoice/SI-1").respond(json={"data": {
        "doctype": "Sales Invoice", "name": "SI-1", "company": "C", "customer": "Walk-in",
        "items": [{"item_code": "I1"}], "taxes": [CGST9, SGST9]}})
    respx.get(f"{BASE}/api/resource/Company").respond(json={"data": [{"name": "C", "tax_id": CO}]})
    respx.get(f"{BASE}/api/resource/Customer").respond(json={"data": [{"name": "Walk-in", "tax_id": None}]})
    respx.get(f"{BASE}/api/resource/Item").respond(json={"data": [{"name": "I1", "customs_tariff_number": None}]})
    out = tools_env.gst_check_invoices("Sales Invoice", "2026-09-01", "2026-09-30")
    assert {i["check"] for i in out["issues"]} == {"party_gstin_missing", "hsn_missing"}


@respx.mock
def test_check_invoices_uses_ic_fields(tools_env):
    _versions(True)
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice").respond(json={"data": [{"name": "PI-1"}]})
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice/PI-1").respond(json={"data": {
        "doctype": "Purchase Invoice", "name": "PI-1", "company": "C", "company_gstin": CO, "supplier": "S1",
        "supplier_gstin": KA, "bill_no": "B1", "bill_date": "2026-09-01", "place_of_supply": "27-Maharashtra",
        "items": [{"item_code": "I1", "gst_hsn_code": "8471"}], "taxes": [CGST9, SGST9]}})
    company = respx.get(f"{BASE}/api/resource/Company")
    out = tools_env.gst_check_invoices("Purchase Invoice", "2026-09-01", "2026-09-30")
    assert [i["check"] for i in out["issues"]] == ["wrong_tax_type"]  # KA supplier → IGST expected
    assert not company.called


def test_check_invoices_bad_doctype(tools_env):
    with pytest.raises(ToolError):
        tools_env.gst_check_invoices("Journal Entry", "2026-09-01", "2026-09-30")


def _books():
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice").respond(json={"data": [
        {"name": "PI-1", "supplier": "Alpha", "bill_no": "inv-1", "bill_date": "2026-09-05",
         "base_net_total": 10000, "base_total_taxes_and_charges": 1800},
        {"name": "PI-9", "supplier": "Alpha", "bill_no": "OLD-9", "bill_date": "2026-09-06",
         "base_net_total": 100, "base_total_taxes_and_charges": 18}]})
    respx.get(f"{BASE}/api/resource/Supplier").respond(json={"data": [{"name": "Alpha", "tax_id": MH}]})
    respx.get(f"{BASE}/api/resource/Purchase%20Taxes%20and%20Charges").respond(json={"data": [
        {"parent": "PI-1", "account_head": "Input CGST - C", "add_deduct_tax": "Add", "base_tax_amount": 900},
        {"parent": "PI-1", "account_head": "Input SGST - C", "add_deduct_tax": "Add", "base_tax_amount": 900},
        {"parent": "PI-9", "account_head": "Input IGST - C", "add_deduct_tax": "Add", "base_tax_amount": 18}]})


@respx.mock
def test_reconcile_from_file(tools_env):
    _versions(False)
    _books()
    out = tools_env.gst_reconcile_2b(file_path=str(FIXTURE))
    s = out["summary"]
    assert s["matched"] == 1 and s["missing_in_2b"] == 1 and s["missing_in_books"] == 4
    assert out["skipped"] == {"b2ba": 1}
    assert out["period"] == {"from_date": "2026-09-01", "to_date": "2026-09-30"}


def test_reconcile_file_missing(tools_env):
    with pytest.raises(ToolError) as e:
        tools_env.gst_reconcile_2b(file_path="/nope/2b.json")
    assert "not found" in str(e.value)


def test_reconcile_bad_json(tools_env, tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{oops")
    with pytest.raises(ToolError):
        tools_env.gst_reconcile_2b(file_path=str(p))


@respx.mock
def test_reconcile_from_ic_inward_supply(tools_env):
    _versions(True)
    inward = respx.get(f"{BASE}/api/resource/GST%20Inward%20Supply").respond(json={"data": [
        {"supplier_gstin": MH, "supplier_name": "Alpha", "bill_no": "INV/001", "bill_date": "2026-09-05",
         "doc_type": "Invoice", "taxable_value": 10000, "igst": 0, "cgst": 900, "sgst": 900, "cess": 0,
         "is_reverse_charge": 0, "itc_availability": "Yes"}]})
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice").respond(json={"data": [
        {"name": "PI-1", "supplier": "Alpha", "supplier_gstin": MH, "bill_no": "inv-1", "bill_date": "2026-09-05",
         "base_net_total": 10000, "base_total_taxes_and_charges": 1800}]})
    respx.get(f"{BASE}/api/resource/Purchase%20Taxes%20and%20Charges").respond(json={"data": [
        {"parent": "PI-1", "account_head": "x", "gst_tax_type": "cgst", "add_deduct_tax": "Add", "base_tax_amount": 900},
        {"parent": "PI-1", "account_head": "y", "gst_tax_type": "sgst", "add_deduct_tax": "Add", "base_tax_amount": 900}]})
    out = tools_env.gst_reconcile_2b(source="india_compliance", from_date="2026-09-01", to_date="2026-09-30")
    assert out["summary"]["matched"] == 1
    assert ["classification", "in", ["B2B", "CDNR"]] in json.loads(_q(inward)["filters"])


@respx.mock
def test_reconcile_ic_source_needs_ic(tools_env):
    _versions(False)
    with pytest.raises(ToolError) as e:
        tools_env.gst_reconcile_2b(source="india_compliance", from_date="2026-09-01", to_date="2026-09-30")
    assert "India Compliance" in str(e.value)


@respx.mock
def test_status_requires_ic(tools_env):
    _versions(False)
    with pytest.raises(ToolError) as e:
        tools_env.gst_status(from_date="2026-09-01", to_date="2026-09-30")
    assert "India Compliance" in str(e.value)


@respx.mock
def test_status_lists_pending(tools_env):
    _versions(True)
    respx.get(f"{BASE}/api/resource/Sales%20Invoice").respond(json={"data": [
        {"name": "SI-1", "customer": "A", "einvoice_status": "Pending", "e_waybill_status": "Not Applicable"},
        {"name": "SI-2", "customer": "B", "einvoice_status": "Generated", "irn": "X", "e_waybill_status": "Failed"},
        {"name": "SI-3", "customer": "C", "einvoice_status": "Not Applicable", "e_waybill_status": "Generated"}]})
    out = tools_env.gst_status(from_date="2026-09-01", to_date="2026-09-30")
    assert [r["name"] for r in out["e_invoice_pending"]] == ["SI-1"]
    assert [r["name"] for r in out["e_waybill_pending"]] == ["SI-2"]
    assert out["counts"] == {"documents": 3, "e_invoice_pending": 1, "e_waybill_pending": 1}


def _audit(home):
    return [json.loads(x) for x in (home / "audit.log").read_text().splitlines()]


@respx.mock
def test_generate_e_invoice_gated(tools_env, home, monkeypatch):
    post = respx.post(f"{BASE}/api/method/{IC_METHOD}.e_invoice.generate_e_invoice")
    with pytest.raises(ToolError):
        tools_env.gst_generate_e_invoice("SINV-1", confirm=True)  # flag off
    monkeypatch.setenv("ERPNEXT_ALLOW_GST_ACTIONS", "1")
    with pytest.raises(ToolError):
        tools_env.gst_generate_e_invoice("SINV-1")  # no confirm
    assert not post.called
    assert [a["outcome"] for a in _audit(home)] == ["refused", "refused"]


@respx.mock
def test_generate_e_invoice_ok(tools_env, home, monkeypatch):
    monkeypatch.setenv("ERPNEXT_ALLOW_GST_ACTIONS", "1")
    _versions(True)
    post = respx.post(f"{BASE}/api/method/{IC_METHOD}.e_invoice.generate_e_invoice").respond(json={})
    respx.get(f"{BASE}/api/resource/Sales%20Invoice/SINV-1").respond(json={"data": {
        "name": "SINV-1", "irn": "IRN123", "einvoice_status": "Generated"}})
    out = tools_env.gst_generate_e_invoice("SINV-1", confirm=True)
    assert json.loads(post.calls.last.request.content) == {"docname": "SINV-1"}
    assert out == {"name": "SINV-1", "irn": "IRN123", "einvoice_status": "Generated"}
    assert _audit(home)[-1]["tool"] == "gst_generate_e_invoice" and _audit(home)[-1]["outcome"] == "ok"


@respx.mock
def test_generate_e_invoice_without_ic(tools_env, monkeypatch):
    monkeypatch.setenv("ERPNEXT_ALLOW_GST_ACTIONS", "1")
    _versions(False)
    with pytest.raises(ToolError) as e:
        tools_env.gst_generate_e_invoice("SINV-1", confirm=True)
    assert "India Compliance" in str(e.value)


@respx.mock
def test_generate_e_waybill_ok(tools_env, monkeypatch):
    monkeypatch.setenv("ERPNEXT_ALLOW_GST_ACTIONS", "1")
    _versions(True)
    post = respx.post(f"{BASE}/api/method/{IC_METHOD}.e_waybill.generate_e_waybill").respond(json={})
    respx.get(f"{BASE}/api/resource/Delivery%20Note/DN-1").respond(json={"data": {
        "name": "DN-1", "ewaybill": "EWB1", "e_waybill_status": "Generated"}})
    out = tools_env.gst_generate_e_waybill("Delivery Note", "DN-1", {"vehicle_no": "MH12AB1234", "distance": 40},
                                           confirm=True)
    body = json.loads(post.calls.last.request.content)
    assert body["doctype"] == "Delivery Note" and body["docname"] == "DN-1"
    assert json.loads(body["values"]) == {"vehicle_no": "MH12AB1234", "distance": 40}
    assert out["ewaybill"] == "EWB1"


# ---- final review fixes ------------------------------------------------------

import httpx


def _pi_router(period_rows, extra_rows=()):
    def handler(request):
        flt = json.loads(_q_req(request).get("filters", "[]"))
        if any(f[0] == "bill_no" for f in flt):
            return httpx.Response(200, json={"data": list(extra_rows)})
        return httpx.Response(200, json={"data": period_rows})
    return handler


def _q_req(request):
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


def _tax_rows(rows):
    return respx.get(f"{BASE}/api/resource/Purchase%20Taxes%20and%20Charges").respond(json={"data": rows})


@respx.mock
def test_reconcile_period_from_return_period_and_late_bills(tools_env):
    _versions(False)
    pi = respx.get(f"{BASE}/api/resource/Purchase%20Invoice").mock(side_effect=_pi_router(
        [{"name": "PI-1", "supplier": "Alpha", "bill_no": "inv-1", "bill_date": "2026-09-05",
          "base_net_total": 10000, "posting_date": "2026-09-06"}],
        extra_rows=[{"name": "PI-0", "supplier": "Alpha", "bill_no": "A-77", "bill_date": "2026-09-09",
                     "base_net_total": 2000, "posting_date": "2026-10-02"},
                    {"name": "PI-X", "supplier": "Alpha", "bill_no": "UNRELATED", "bill_date": "2026-08-01",
                     "base_net_total": 5, "posting_date": "2026-08-01"}]))
    respx.get(f"{BASE}/api/resource/Supplier").respond(json={"data": [{"name": "Alpha", "tax_id": MH}]})
    _tax_rows([
        {"parent": "PI-1", "account_head": "Input CGST - C", "add_deduct_tax": "Add", "base_tax_amount": 900},
        {"parent": "PI-1", "account_head": "Input SGST - C", "add_deduct_tax": "Add", "base_tax_amount": 900},
        {"parent": "PI-1", "account_head": "TDS 194C - C", "add_deduct_tax": "Deduct", "base_tax_amount": 100},
        {"parent": "PI-1", "account_head": "Freight - C", "add_deduct_tax": "Add", "base_tax_amount": 50},
        {"parent": "PI-0", "account_head": "Input CGST - C", "add_deduct_tax": "Add", "base_tax_amount": 180},
        {"parent": "PI-0", "account_head": "Input SGST - C", "add_deduct_tax": "Add", "base_tax_amount": 180}])
    out = tools_env.gst_reconcile_2b(file_path=str(FIXTURE))
    assert out["period"] == {"from_date": "2026-09-01", "to_date": "2026-09-30"}
    period_call = next(c for c in pi.calls if not any(f[0] == "bill_no" for f in
                                                      json.loads(_q_req(c.request)["filters"])))
    assert ["posting_date", "between", ["2026-09-01", "2026-09-30"]] in json.loads(_q_req(period_call.request)["filters"])
    s = out["summary"]
    # PI-1 matches INV/001 despite TDS + freight rows; late bill A-77 found outside the period; PI-X ignored
    assert s["matched"] == 2 and s["mismatch"] == 0 and s["missing_in_2b"] == 0


@respx.mock
def test_reconcile_refuses_when_books_too_many(tools_env, monkeypatch):
    from erpnext_mcp import gst_tools

    monkeypatch.setattr(gst_tools, "MAX_ROWS", 2)
    _versions(False)
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice").respond(json={"data": [
        {"name": f"PI-{i}", "supplier": "Alpha", "bill_no": str(i), "bill_date": "2026-09-01",
         "base_net_total": 1} for i in range(3)]})
    with pytest.raises(ToolError) as e:
        tools_env.gst_reconcile_2b(file_path=str(FIXTURE))
    assert "narrow" in str(e.value).lower()


@respx.mock
def test_ic_credit_notes_negated(tools_env):
    _versions(True)
    respx.get(f"{BASE}/api/resource/GST%20Inward%20Supply").respond(json={"data": [
        {"supplier_gstin": MH, "bill_no": "CN-5", "bill_date": "2026-09-20", "doc_type": "Credit Note",
         "taxable_value": 1000, "igst": 0, "cgst": 90, "sgst": 90, "cess": 0}]})
    respx.get(f"{BASE}/api/resource/Purchase%20Invoice").mock(side_effect=_pi_router([
        {"name": "PI-R", "supplier": "Alpha", "supplier_gstin": MH, "bill_no": "CN-5", "bill_date": "2026-09-20",
         "base_net_total": -1000, "posting_date": "2026-09-21"}]))
    _tax_rows([{"parent": "PI-R", "account_head": "x", "gst_tax_type": "cgst", "add_deduct_tax": "Add", "base_tax_amount": -90},
               {"parent": "PI-R", "account_head": "y", "gst_tax_type": "sgst", "add_deduct_tax": "Add", "base_tax_amount": -90}])
    out = tools_env.gst_reconcile_2b(source="india_compliance", from_date="2026-09-01", to_date="2026-09-30")
    assert out["summary"]["matched"] == 1


@respx.mock
def test_lookup_chunks_long_lists(tools_env):
    from erpnext_mcp.gst_tools import _lookup
    from erpnext_mcp.tools import _connect

    route = respx.get(f"{BASE}/api/resource/Item").respond(json={"data": []})
    _, client = _connect()
    with client:
        _lookup(client, "Item", {f"ITEM-{i:04d}" for i in range(250)}, ["customs_tariff_number"])
    assert len(route.calls) == 3


@respx.mock
def test_find_party_gstin_uses_ic_field(tools_env):
    _versions(True)
    route = respx.get(f"{BASE}/api/resource/Supplier").respond(json={"data": [
        {"name": "Alpha", "supplier_name": "Alpha Traders", "tax_id": "AAACA1111A", "gstin": MH}]})
    out = tools_env.find_party("Supplier", MH)
    assert out[0]["name"] == "Alpha" and out[0]["match_by"] == "gstin"
    assert ["gstin", "=", MH] in json.loads(_q_req(route.calls.last.request)["filters"])
