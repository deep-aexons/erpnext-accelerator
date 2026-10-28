import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from erpnext_mcp.documents import looks_like_gstin, score
from erpnext_mcp.gst import gstin_checksum

BASE = "http://erp.test"
MH = "27AAACA1111A1Z" + gstin_checksum("27AAACA1111A1Z")


def _q(request):
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


def test_score():
    assert score("ACME Pvt. Ltd.", "Acme Private Limited") > 0.6
    assert score("Blue Widget", "blue widget") == 1.0
    assert score("Blue Widget", "Red Chair") < 0.5


def test_looks_like_gstin():
    assert looks_like_gstin(MH) and looks_like_gstin(MH.lower())
    assert not looks_like_gstin("Alpha Traders")


@respx.mock
def test_find_party_by_gstin(tools_env):
    respx.get(f"{BASE}/api/method/frappe.utils.change_log.get_versions").respond(json={"message": {"frappe": {}}})
    route = respx.get(f"{BASE}/api/resource/Supplier").respond(json={"data": [
        {"name": "Alpha", "supplier_name": "Alpha Traders", "tax_id": MH}]})
    out = tools_env.find_party("Supplier", MH.lower())
    assert out[0] == {"name": "Alpha", "display_name": "Alpha Traders", "tax_id": MH, "score": 1.0, "match_by": "gstin"}
    assert ["tax_id", "=", MH] in json.loads(_q(route.calls.last.request)["filters"])


@respx.mock
def test_find_party_by_name_ranks(tools_env):
    respx.get(f"{BASE}/api/resource/Customer").respond(json={"data": [
        {"name": "C-2", "customer_name": "Alphabet Inc", "tax_id": None},
        {"name": "C-1", "customer_name": "Alpha Traders", "tax_id": None}]})
    out = tools_env.find_party("Customer", "Alpha Traders Pvt")
    assert out[0]["name"] == "C-1" and out[0]["match_by"] == "name"
    assert out[0]["score"] > out[1]["score"]
    assert len({r["name"] for r in out}) == len(out)  # de-duplicated across token queries


def test_find_party_bad_type(tools_env):
    with pytest.raises(ToolError):
        tools_env.find_party("Employee", "x")


def _item_router(request):
    flt = json.loads(_q(request).get("filters", "[]"))
    if any(f[0] == "name" and f[1] == "in" for f in flt):
        names = next(f[2] for f in flt if f[0] == "name")
        known = {"ITEM-001": "Steel Bolt M8", "BAR-ITEM": "Barcode Thing", "WID-1": "Blue Widget"}
        return httpx.Response(200, json={"data": [
            {"name": n, "item_name": known[n], "stock_uom": "Nos"} for n in names if n in known]})
    if any(f[0] == "item_name" and f[1] == "like" for f in flt):
        return httpx.Response(200, json={"data": [
            {"name": "CH-1", "item_name": "Red Chair Large", "stock_uom": "Nos"},
            {"name": "CH-2", "item_name": "Red Chair Small", "stock_uom": "Nos"}]})
    return httpx.Response(200, json={"data": []})


@respx.mock
def test_match_items_all_paths(tools_env):
    respx.get(f"{BASE}/api/resource/Item").mock(side_effect=_item_router)
    barcode = respx.get(f"{BASE}/api/resource/Item%20Barcode").respond(json={"data": [
        {"parent": "BAR-ITEM", "barcode": "8901234567890"}]})
    part = respx.get(f"{BASE}/api/resource/Item%20Supplier").respond(json={"data": [
        {"parent": "WID-1", "supplier_part_no": "BW-9"}]})
    out = tools_env.match_items(
        ["ITEM-001", {"description": "8901234567890"}, {"description": "Blue widget", "supplier_part_no": "BW-9"},
         "red chair large"], supplier="Alpha")
    best = [(ln["best"] or {}).get("item_code") for ln in out["lines"]]
    by = [(ln["best"] or {}).get("match_by") for ln in out["lines"]]
    assert best == ["ITEM-001", "BAR-ITEM", "WID-1", "CH-1"]
    assert by == ["item_code", "barcode", "supplier_part_no", "name"]
    assert all(ln["confident"] for ln in out["lines"])
    assert _q(barcode.calls.last.request)["parent"] == "Item"
    assert ["supplier", "=", "Alpha"] in json.loads(_q(part.calls.last.request)["filters"])


@respx.mock
def test_match_items_no_supplier_skips_part_numbers(tools_env):
    respx.get(f"{BASE}/api/resource/Item").mock(side_effect=_item_router)
    respx.get(f"{BASE}/api/resource/Item%20Barcode").respond(json={"data": []})
    part = respx.get(f"{BASE}/api/resource/Item%20Supplier")
    out = tools_env.match_items([{"description": "chair", "supplier_part_no": "BW-9"}])
    assert not part.called
    assert out["lines"][0]["confident"] is False  # "chair" vs "Red Chair Large" is a weak match


def test_match_items_empty(tools_env):
    assert tools_env.match_items([]) == {"lines": [], "unmatched": 0}


def test_match_items_too_many(tools_env):
    with pytest.raises(ToolError):
        tools_env.match_items(["x"] * 101)
