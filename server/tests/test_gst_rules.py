import json
from pathlib import Path

import pytest

from erpnext_mcp.gst import (
    STANDARD_RATES,
    check_invoice,
    classify_tax_row,
    gstin_checksum,
    hsn_issue,
    norm_bill_no,
    parse_gstr2b,
    reconcile,
    validate_gstin,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gstr2b_sample.json"


def gstin(prefix14: str) -> str:
    return prefix14 + gstin_checksum(prefix14)


MH = gstin("27AAACA1111A1Z")  # Maharashtra
KA = gstin("29AAACB2222B1Z")  # Karnataka
CO = gstin("27AAACZ9999Z1Z")  # company, Maharashtra


# ---------------------------------------------------------------- GSTIN

def test_valid_gstin():
    out = validate_gstin(MH)
    assert out == {"valid": True, "gstin": MH, "state_code": "27", "pan": "AAACA1111A", "reason": None}


def test_lowercase_and_spaces_normalised():
    assert validate_gstin(f"  {MH.lower()} ")["valid"] is True


@pytest.mark.parametrize("bad,reason_part", [
    ("27AAACA1111A1Z", "15 characters"),
    ("40AAACA1111A1Z5", "state code"),
    ("27AAACA1111A1X5", "format"),
    ("", "empty"),
])
def test_invalid_gstin(bad, reason_part):
    out = validate_gstin(bad)
    assert out["valid"] is False and reason_part in out["reason"]


def test_bad_checksum():
    wrong = MH[:-1] + ("0" if MH[-1] != "0" else "1")
    out = validate_gstin(wrong)
    assert out["valid"] is False and "checksum" in out["reason"]


def test_state_97_allowed():
    assert validate_gstin(gstin("97AAACA1111A1Z"))["valid"] is True


# ---------------------------------------------------------------- tax rows / HSN

@pytest.mark.parametrize("row,expected", [
    ({"gst_tax_type": "igst_rcm"}, "igst"),
    ({"gst_tax_type": "cgst"}, "cgst"),
    ({"account_head": "Output Tax CGST - AC"}, "cgst"),
    ({"account_head": "Input Tax UTGST - AC"}, "sgst"),
    ({"account_head": "IGST Payable - AC"}, "igst"),
    ({"account_head": "Compensation Cess - AC"}, "cess"),
    ({"account_head": "Freight - AC"}, None),
])
def test_classify_tax_row(row, expected):
    assert classify_tax_row(row) == expected


@pytest.mark.parametrize("code,check", [
    (None, "hsn_missing"), ("", "hsn_missing"), ("8471", None), ("84713010", None), ("847130", None),
    ("84713", "hsn_format"), ("99", "hsn_format"), ("998314", None), ("99831", "hsn_format"), ("84A1", "hsn_format"),
])
def test_hsn_issue(code, check):
    res = hsn_issue(code)
    assert (res[0] if res else None) == check


def test_standard_rates():
    assert {0, 5, 12, 18, 28} <= STANDARD_RATES


# ---------------------------------------------------------------- invoice checks

def _pi(taxes, **kw):
    doc = {"doctype": "Purchase Invoice", "name": "PI-1", "bill_no": "INV-1", "bill_date": "2026-09-01",
           "items": [{"item_code": "I1"}], "taxes": taxes}
    doc.update(kw)
    return doc


def _ctx(**kw):
    ctx = {"company_gstin": CO, "party_gstin": MH, "item_hsn": {"I1": "8471"}, "place_of_supply_state": None}
    ctx.update(kw)
    return ctx


CGST9 = {"account_head": "Input CGST - C", "rate": 9, "tax_amount": 90}
SGST9 = {"account_head": "Input SGST - C", "rate": 9, "tax_amount": 90}
IGST18 = {"account_head": "Input IGST - C", "rate": 18, "tax_amount": 180}


def checks(issues):
    return sorted(i["check"] for i in issues)


def test_clean_intra_state():
    assert check_invoice(_pi([CGST9, SGST9]), _ctx()) == []


def test_clean_inter_state():
    assert check_invoice(_pi([IGST18]), _ctx(party_gstin=KA)) == []


def test_intra_state_with_igst():
    assert "wrong_tax_type" in checks(check_invoice(_pi([IGST18]), _ctx()))


def test_inter_state_with_cgst_sgst():
    assert "wrong_tax_type" in checks(check_invoice(_pi([CGST9, SGST9]), _ctx(party_gstin=KA)))


def test_sales_uses_place_of_supply():
    si = {"doctype": "Sales Invoice", "name": "SI-1", "items": [{"item_code": "I1"}], "taxes": [IGST18]}
    assert "wrong_tax_type" not in checks(check_invoice(si, _ctx(party_gstin=None, place_of_supply_state="29")))
    assert "wrong_tax_type" in checks(check_invoice(si, _ctx(party_gstin=None, place_of_supply_state="27")))


def test_cgst_sgst_unequal():
    sgst6 = {"account_head": "Input SGST - C", "rate": 6, "tax_amount": 60}
    assert "cgst_sgst_unequal" in checks(check_invoice(_pi([CGST9, sgst6]), _ctx()))


def test_rate_not_standard():
    igst17 = {"account_head": "Input IGST - C", "rate": 17, "tax_amount": 170}
    assert "rate_not_standard" in checks(check_invoice(_pi([igst17]), _ctx(party_gstin=KA)))


def test_zero_rate_row_not_flagged():
    igst0 = {"account_head": "Input IGST - C", "rate": 0, "tax_amount": 180}
    assert "rate_not_standard" not in checks(check_invoice(_pi([igst0]), _ctx(party_gstin=KA)))


def test_no_gst_tax_warning():
    issues = check_invoice(_pi([]), _ctx())
    assert [(i["check"], i["severity"]) for i in issues] == [("no_gst_tax", "warning")]


def test_party_gstin_missing_and_invalid():
    assert ("party_gstin_missing", "warning") in [(i["check"], i["severity"]) for i in
                                                  check_invoice(_pi([CGST9, SGST9]), _ctx(party_gstin=None))]
    assert "party_gstin_invalid" in checks(check_invoice(_pi([CGST9, SGST9]), _ctx(party_gstin="27BAD")))


def test_company_gstin_invalid():
    assert "company_gstin_invalid" in checks(check_invoice(_pi([CGST9, SGST9]), _ctx(company_gstin=None)))


def test_hsn_from_row_beats_item_master():
    doc = _pi([CGST9, SGST9], items=[{"item_code": "I1", "gst_hsn_code": "84713"}])
    assert "hsn_format" in checks(check_invoice(doc, _ctx()))


def test_hsn_missing_reported_once_per_item():
    doc = _pi([CGST9, SGST9], items=[{"item_code": "I2"}, {"item_code": "I2"}])
    assert checks(check_invoice(doc, _ctx())).count("hsn_missing") == 1


def test_bill_no_missing():
    assert "bill_no_missing" in checks(check_invoice(_pi([CGST9, SGST9], bill_no=None), _ctx()))


# ---------------------------------------------------------------- GSTR-2B parsing

def test_parse_fixture():
    out = parse_gstr2b(json.loads(FIXTURE.read_text()))
    rows = out["rows"]
    assert len(rows) == 5
    assert out["skipped"] == {"b2ba": 1}
    first = rows[0]
    assert first == {"supplier_gstin": MH, "supplier_name": "Alpha Traders", "bill_no": "INV/001",
                     "bill_date": "2026-09-05", "doc_type": "Invoice", "taxable_value": 10000.0,
                     "igst": 0.0, "cgst": 900.0, "sgst": 900.0, "cess": 0.0, "total_tax": 1800.0,
                     "reverse_charge": False, "itc_available": True}
    v3 = next(r for r in rows if r["bill_no"] == "A-77")
    assert v3["taxable_value"] == 2000.0 and v3["total_tax"] == 360.0 and v3["reverse_charge"] is True
    cn = next(r for r in rows if r["doc_type"] == "Credit Note")
    assert cn["taxable_value"] == -1000.0 and cn["total_tax"] == -180.0


def test_parse_accepts_inner_data_and_empty():
    inner = json.loads(FIXTURE.read_text())["data"]
    assert len(parse_gstr2b(inner)["rows"]) == 5
    assert parse_gstr2b({}) == {"rows": [], "skipped": {}, "return_period": None}


# ---------------------------------------------------------------- reconciliation

@pytest.mark.parametrize("a,b", [("INV/001", "inv-1"), ("A 77", "a77"), ("2026/0010", "2026-10"), ("100", "100")])
def test_norm_bill_no_equal(a, b):
    assert norm_bill_no(a) == norm_bill_no(b)


def test_norm_bill_no_keeps_inner_zeros():
    assert norm_bill_no("100") != norm_bill_no("1")


def _g(no, taxable, tax, gst=MH, date="2026-09-05"):
    return {"supplier_gstin": gst, "supplier_name": "S", "bill_no": no, "bill_date": date, "doc_type": "Invoice",
            "taxable_value": taxable, "igst": 0, "cgst": tax / 2, "sgst": tax / 2, "cess": 0, "total_tax": tax,
            "reverse_charge": False, "itc_available": True}


def _b(name, no, taxable, tax, gst=MH, date="2026-09-05"):
    return {"name": name, "supplier": "S", "supplier_gstin": gst, "bill_no": no, "bill_date": date,
            "taxable_value": taxable, "total_tax": tax}


def test_reconcile_all_statuses():
    gstr = [_g("INV/001", 10000, 1800), _g("INV-002", 5000, 900), _g("Z-9", 300, 54, gst=KA),
            _g("NEW-1", 700, 126)]
    books = [_b("PI-1", "inv-1", 10000, 1800), _b("PI-2", "INV002", 5200, 936),
             _b("PI-3", "Z9-OLD", 300.5, 54, gst=KA), _b("PI-4", "LOST-1", 1000, 180), _b("PI-5", "X", 50, 9, gst=None)]
    out = reconcile(gstr, books)
    s = out["summary"]
    assert (s["matched"], s["mismatch"], s["suggested"], s["missing_in_books"], s["missing_in_2b"]) == (1, 1, 1, 1, 2)
    mm = out["mismatch"][0]
    assert mm["purchase_invoice"] == "PI-2" and sorted(mm["differences"]) == ["taxable_value", "total_tax"]
    assert out["suggested"][0]["purchase_invoice"] == "PI-3"
    assert out["missing_in_books"][0]["bill_no"] == "NEW-1"
    assert {r["purchase_invoice"] for r in out["missing_in_2b"]} == {"PI-4", "PI-5"}
    # ITC at risk: PI-4 180 + PI-5 9 + mismatch excess (936 - 900) = 225
    assert s["itc_at_risk"] == 225.0


def test_reconcile_date_mismatch_only():
    out = reconcile([_g("A1", 100, 18, date="2026-09-01")], [_b("PI-1", "A1", 100, 18, date="2026-09-02")])
    assert out["mismatch"][0]["differences"] == ["bill_date"]


def test_reconcile_tolerance():
    out = reconcile([_g("A1", 100, 18)], [_b("PI-1", "A1", 100.9, 18.5)], amount_tolerance=1.0)
    assert out["summary"]["matched"] == 1


# ---- final review fixes ------------------------------------------------------


def test_forty_percent_slab():
    igst40 = {"account_head": "Output IGST - C", "rate": 40, "tax_amount": 400}
    cgst20 = {"account_head": "Output CGST - C", "rate": 20, "tax_amount": 200}
    sgst20 = {"account_head": "Output SGST - C", "rate": 20, "tax_amount": 200}
    assert "rate_not_standard" not in checks(check_invoice(_pi([igst40]), _ctx(party_gstin=KA)))
    assert "rate_not_standard" not in checks(check_invoice(_pi([cgst20, sgst20]), _ctx()))


def test_sez_same_state_igst_ok():
    si = {"doctype": "Sales Invoice", "name": "SI-1", "items": [{"item_code": "I1"}], "taxes": [IGST18],
          "gst_category": "SEZ"}
    assert "wrong_tax_type" not in checks(check_invoice(si, _ctx(place_of_supply_state="27")))
    si_cs = dict(si, taxes=[CGST9, SGST9])
    assert "wrong_tax_type" in checks(check_invoice(si_cs, _ctx(place_of_supply_state="27")))


def test_overseas_purchase_rcm_igst_ok():
    doc = _pi([IGST18], gst_category="Overseas")
    issues = check_invoice(doc, _ctx(party_gstin=None, place_of_supply_state="27"))
    assert "wrong_tax_type" not in checks(issues)
    assert "party_gstin_missing" not in checks(issues)


def test_purchase_unknown_supplier_state_skips_type_check():
    assert "wrong_tax_type" not in checks(check_invoice(_pi([IGST18]), _ctx(party_gstin=None, place_of_supply_state="27")))


def test_bill_date_missing():
    assert "bill_date_missing" in checks(check_invoice(_pi([CGST9, SGST9], bill_date=None), _ctx()))
    assert "bill_date_missing" not in checks(check_invoice(_pi([CGST9, SGST9], bill_date="2026-09-01"), _ctx()))


def test_parse_returns_period():
    assert parse_gstr2b(json.loads(FIXTURE.read_text()))["return_period"] == "092026"
