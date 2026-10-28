import json
from urllib.parse import parse_qs, urlparse

import pytest
import respx

from erpnext_mcp.p2p import match_flags

BASE = "http://erp.test"


def line(qty=10, received=10, rate=100, billed=1000):
    return {"qty": qty, "received_qty": received, "rate": rate, "amount": qty * rate, "billed_amt": billed}


@pytest.mark.parametrize("ln,inv,tol,expected", [
    (line(), [{"rate": 100}], 0, []),
    (line(received=0, billed=0), [], 0, ["not_received"]),
    (line(received=5, billed=1000), [{"rate": 100}], 0, ["billed_more_than_received", "partially_received"]),
    (line(received=12, billed=1200), [{"rate": 100}], 0, ["over_billed", "over_received"]),
    (line(received=10, billed=0), [], 0, ["received_not_billed"]),
    (line(billed=1000), [{"rate": 105}], 0, ["rate_mismatch"]),
    (line(billed=1000), [{"rate": 105}], 10, []),
    (line(received=9.5, billed=950), [{"rate": 100}], 10, []),
])
def test_match_flags(ln, inv, tol, expected):
    assert sorted(match_flags(ln, inv, tol)) == expected


def _q(call):
    return {k: v[0] for k, v in parse_qs(urlparse(str(call.request.url)).query).items()}


def _mock_two_orders():
    po_list = respx.get(f"{BASE}/api/resource/Purchase%20Order").respond(json={"data": [
        {"name": "PO-1", "supplier": "S", "transaction_date": "2026-10-01", "status": "To Bill",
         "per_received": 100, "per_billed": 0, "grand_total": 1000},
        {"name": "PO-2", "supplier": "S", "transaction_date": "2026-10-02", "status": "Completed",
         "per_received": 100, "per_billed": 100, "grand_total": 500}]})
    respx.get(f"{BASE}/api/resource/Purchase%20Order/PO-1").respond(json={"data": {"items": [
        {"name": "L1", "item_code": "I1", "qty": 10, "received_qty": 10, "rate": 100, "amount": 1000, "billed_amt": 0}]}})
    respx.get(f"{BASE}/api/resource/Purchase%20Order/PO-2").respond(json={"data": {"items": [
        {"name": "L2", "item_code": "I2", "qty": 5, "received_qty": 5, "rate": 100, "amount": 500, "billed_amt": 500}]}})
    pii = respx.get(f"{BASE}/api/resource/Purchase%20Invoice%20Item").respond(json={"data": [
        {"parent": "PI-7", "po_detail": "L2", "qty": 5, "rate": 100, "amount": 500}]})
    return po_list, pii


@respx.mock
def test_three_way_match_queries(tools_env):
    po_list, pii = _mock_two_orders()
    out = tools_env.three_way_match(supplier="S", from_date="2026-10-01", to_date="2026-10-31", only_issues=False)
    flt = json.loads(_q(po_list.calls.last)["filters"])
    assert ["docstatus", "=", 1] in flt and ["supplier", "=", "S"] in flt
    assert ["transaction_date", "between", ["2026-10-01", "2026-10-31"]] in flt
    q = _q(pii.calls.last)
    assert q["parent"] == "Purchase Invoice"
    assert ["po_detail", "in", ["L1", "L2"]] in json.loads(q["filters"])
    assert [o["name"] for o in out["orders"]] == ["PO-1", "PO-2"]
    assert out["orders"][0]["lines"][0]["flags"] == ["received_not_billed"]
    assert out["orders"][1]["lines"][0]["invoices"] == ["PI-7"]
    assert out["summary"] == {"orders_checked": 2, "orders_with_issues": 1, "flag_counts": {"received_not_billed": 1}}


@respx.mock
def test_only_issues_filters_clean_orders(tools_env):
    _mock_two_orders()
    out = tools_env.three_way_match()
    assert [o["name"] for o in out["orders"]] == ["PO-1"]


@respx.mock
def test_single_po_and_limit_clamped(tools_env):
    po_list = respx.get(f"{BASE}/api/resource/Purchase%20Order").respond(json={"data": []})
    out = tools_env.three_way_match(purchase_order="PO-9", limit=999)
    q = _q(po_list.calls.last)
    assert q["limit_page_length"] == "201"
    assert ["name", "=", "PO-9"] in json.loads(q["filters"])
    assert out["orders"] == [] and out["summary"]["orders_checked"] == 0
