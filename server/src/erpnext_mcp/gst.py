"""India GST rules and GSTR-2B reconciliation. Pure functions, no HTTP."""

from __future__ import annotations

import re
from datetime import datetime

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
VALID_STATE_CODES = frozenset({f"{i:02d}" for i in range(1, 39)} | {"97"})
# 40 = GST 2.0 slab (from 22 Sep 2025); 12 and 28 kept for older invoices.
STANDARD_RATES = frozenset({0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28, 40})
FORCE_INTER_STATE = frozenset({"SEZ", "Overseas"})
NO_GSTIN_EXPECTED = frozenset({"Overseas", "Unregistered"})
GST_TYPES = ("igst", "cgst", "sgst", "cess")


# ------------------------------------------------------------------ GSTIN

def gstin_checksum(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14.upper()):
        product = _ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def validate_gstin(value: str | None) -> dict:
    gstin = (value or "").strip().upper()

    def bad(reason: str) -> dict:
        return {"valid": False, "gstin": gstin, "state_code": None, "pan": None, "reason": reason}

    if not gstin:
        return bad("GSTIN is empty")
    if len(gstin) != 15:
        return bad(f"GSTIN must be 15 characters (got {len(gstin)})")
    if not _GSTIN_RE.match(gstin):
        return bad("GSTIN format is wrong (expected 2 digits, PAN, entity code, 'Z', check digit)")
    if gstin[:2] not in VALID_STATE_CODES:
        return bad(f"GSTIN state code {gstin[:2]} does not exist")
    if gstin_checksum(gstin[:14]) != gstin[14]:
        return bad("GSTIN checksum digit is wrong (likely a typo)")
    return {"valid": True, "gstin": gstin, "state_code": gstin[:2], "pan": gstin[2:12], "reason": None}


# ------------------------------------------------------------------ tax rows and HSN

def classify_tax_row(row: dict) -> str | None:
    tax_type = (row.get("gst_tax_type") or "").lower().removesuffix("_rcm")
    if tax_type in GST_TYPES:
        return tax_type
    head = (row.get("account_head") or row.get("description") or "").upper()
    if "IGST" in head:
        return "igst"
    if "CGST" in head:
        return "cgst"
    if "SGST" in head or "UTGST" in head:
        return "sgst"
    if "CESS" in head:
        return "cess"
    return None


def hsn_issue(code: str | None) -> tuple[str, str] | None:
    code = (code or "").strip()
    if not code:
        return ("hsn_missing", "HSN/SAC code is missing")
    if not code.isdigit():
        return ("hsn_format", f"HSN/SAC '{code}' must contain digits only")
    if code.startswith("99"):
        if len(code) != 6:
            return ("hsn_format", f"SAC '{code}' must be 6 digits")
    elif len(code) not in (4, 6, 8):
        return ("hsn_format", f"HSN '{code}' must be 4, 6 or 8 digits")
    return None


def _issue(check: str, severity: str, message: str) -> dict:
    return {"check": check, "severity": severity, "message": message}


def _rate_ok(rate: float, half: bool) -> bool:
    full = round(float(rate) * (2 if half else 1), 4)
    return full in STANDARD_RATES


def check_invoice(doc: dict, ctx: dict) -> list[dict]:
    """GST problems in one invoice. ctx: company_gstin, party_gstin, item_hsn, place_of_supply_state."""
    issues: list[dict] = []
    is_sales = (doc.get("doctype") or "").startswith("Sales")

    company = validate_gstin(ctx.get("company_gstin"))
    if not company["valid"]:
        issues.append(_issue("company_gstin_invalid", "error", f"Company GSTIN: {company['reason']}"))

    party_raw = ctx.get("party_gstin")
    party = validate_gstin(party_raw) if party_raw else None
    who = "Customer" if is_sales else "Supplier"
    category = doc.get("gst_category") or ctx.get("gst_category")
    if not party_raw and category in NO_GSTIN_EXPECTED:
        pass
    elif not party_raw:
        issues.append(_issue("party_gstin_missing", "warning",
                             f"{who} has no GSTIN (fine only if unregistered; B2B needs it)"))
    elif not party["valid"]:
        issues.append(_issue("party_gstin_invalid", "error", f"{who} GSTIN: {party['reason']}"))

    seen: set[str] = set()
    for item in doc.get("items") or []:
        code = item.get("item_code") or item.get("item_name") or "?"
        if code in seen:
            continue
        seen.add(code)
        hsn = item.get("gst_hsn_code") or (ctx.get("item_hsn") or {}).get(code)
        problem = hsn_issue(hsn)
        if problem:
            issues.append(_issue(problem[0], "error", f"Item {code}: {problem[1]}"))

    amounts = {t: 0.0 for t in GST_TYPES}
    rates: dict[str, set[float]] = {t: set() for t in GST_TYPES}
    for row in doc.get("taxes") or []:
        kind = classify_tax_row(row)
        if not kind:
            continue
        amounts[kind] += float(row.get("tax_amount") or 0)
        if float(row.get("rate") or 0):
            rates[kind].add(float(row["rate"]))
    present = {t for t in ("igst", "cgst", "sgst") if amounts[t] or rates[t]}

    if not present:
        issues.append(_issue("no_gst_tax", "warning", "No GST tax rows (CGST/SGST/IGST) on this invoice"))
    else:
        company_state = company["state_code"]
        party_state = party["state_code"] if party and party["valid"] else None
        pos = ctx.get("place_of_supply_state")
        # Sales: company state vs place of supply. Purchases: supplier state vs company state; unknown → skip.
        supply_state = (pos or party_state) if is_sales else party_state
        forced_inter = category in FORCE_INTER_STATE
        if forced_inter or (company_state and supply_state):
            intra = False if forced_inter else company_state == supply_state
            if intra and (("igst" in present) or not {"cgst", "sgst"} <= present):
                issues.append(_issue("wrong_tax_type", "error",
                                     f"Intra-state supply (state {company_state}) needs CGST + SGST, not IGST"))
            if not intra and (present & {"cgst", "sgst"} or "igst" not in present):
                where = f"{category} supply" if forced_inter else f"Inter-state supply ({company_state} → {supply_state})"
                issues.append(_issue("wrong_tax_type", "error", f"{where} needs IGST, not CGST/SGST"))
        if {"cgst", "sgst"} <= present and (
            abs(amounts["cgst"] - amounts["sgst"]) > 1.0 or (rates["cgst"] and rates["cgst"] != rates["sgst"])
        ):
            issues.append(_issue("cgst_sgst_unequal", "error",
                                 f"CGST ({amounts['cgst']:.2f}) and SGST ({amounts['sgst']:.2f}) must be equal"))
        for kind in ("igst", "cgst", "sgst"):
            for rate in rates[kind]:
                if not _rate_ok(rate, half=kind != "igst"):
                    issues.append(_issue("rate_not_standard", "error", f"{kind.upper()} rate {rate}% is not a GST slab"))

    if not is_sales and not (doc.get("bill_no") or "").strip():
        issues.append(_issue("bill_no_missing", "error",
                             "Supplier invoice number (bill_no) is missing; GSTR-2B matching needs it"))
    if not is_sales and not doc.get("bill_date"):
        issues.append(_issue("bill_date_missing", "error",
                             "Supplier invoice date (bill_date) is missing; GSTR-2B matching needs it"))
    return issues


# ------------------------------------------------------------------ GSTR-2B

def _iso(date: str | None) -> str | None:
    if not date:
        return None
    try:
        return datetime.strptime(date, "%d-%m-%Y").date().isoformat()
    except ValueError:
        return date


def _amounts(doc: dict) -> dict:
    keys = ("txval", "igst", "cgst", "sgst", "cess")
    if any(k in doc for k in keys) and not doc.get("items"):
        src = [doc]
    else:
        src = doc.get("items") or [doc]
    return {k: round(sum(float(i.get(k) or 0) for i in src), 2) for k in keys}


def _row(supplier: dict, doc: dict, number_key: str, doc_type: str, sign: int) -> dict:
    a = _amounts(doc)
    taxes = {k: round(sign * a[k], 2) for k in GST_TYPES}
    return {
        "supplier_gstin": (supplier.get("ctin") or "").upper(),
        "supplier_name": supplier.get("trdnm"),
        "bill_no": doc.get(number_key),
        "bill_date": _iso(doc.get("dt")),
        "doc_type": doc_type,
        "taxable_value": round(sign * a["txval"], 2),
        **taxes,
        "total_tax": round(sum(taxes.values()), 2),
        "reverse_charge": (doc.get("rev") or "N").upper() == "Y",
        "itc_available": (doc.get("itcavl") or "Y").upper() == "Y",
    }


def parse_gstr2b(data: dict) -> dict:
    """Rows from a GSTR-2B JSON (portal download). Handles B2B invoices and CDNR notes; counts skipped sections."""
    inner = data.get("data", data) if isinstance(data, dict) else {}
    docdata = inner.get("docdata", {}) if isinstance(inner, dict) else {}
    rows: list[dict] = []
    for sup in docdata.get("b2b") or []:
        for inv in sup.get("inv") or []:
            rows.append(_row(sup, inv, "inum", "Invoice", 1))
    for sup in docdata.get("cdnr") or []:
        for note in sup.get("nt") or []:
            credit = (note.get("typ") or "C").upper() == "C"
            rows.append(_row(sup, note, "ntnum", "Credit Note" if credit else "Debit Note", -1 if credit else 1))
    skipped = {k: len(v) for k, v in docdata.items() if k not in ("b2b", "cdnr") and isinstance(v, list) and v}
    period = inner.get("rtnprd") if isinstance(inner, dict) else None
    return {"rows": rows, "skipped": skipped, "return_period": period}


def norm_bill_no(value: str | None) -> str:
    tokens = re.split(r"[^0-9A-Z]+", (value or "").upper())
    return "".join(re.sub(r"(?<!\d)0+(?=\d)", "", t) for t in tokens if t)


def reconcile(gstr_rows: list[dict], book_rows: list[dict], amount_tolerance: float = 1.0) -> dict:
    tol = float(amount_tolerance)
    books = {i: b for i, b in enumerate(book_rows)}
    index = {}
    for i, b in books.items():
        if b.get("supplier_gstin"):
            index.setdefault(((b["supplier_gstin"] or "").upper(), norm_bill_no(b.get("bill_no"))), []).append(i)

    out = {k: [] for k in ("matched", "mismatch", "suggested", "missing_in_books", "missing_in_2b")}
    excess_at_risk = 0.0
    unmatched: list[dict] = []

    for g in gstr_rows:
        key = (g["supplier_gstin"], norm_bill_no(g["bill_no"]))
        hits = [i for i in index.get(key, []) if i in books]
        if not hits:
            unmatched.append(g)
            continue
        b = books.pop(hits[0])
        diffs = []
        if g.get("bill_date") and b.get("bill_date") and str(g["bill_date"]) != str(b["bill_date"]):
            diffs.append("bill_date")
        if abs(float(g["taxable_value"]) - float(b.get("taxable_value") or 0)) > tol:
            diffs.append("taxable_value")
        if abs(float(g["total_tax"]) - float(b.get("total_tax") or 0)) > tol:
            diffs.append("total_tax")
        base = {"supplier_gstin": g["supplier_gstin"], "bill_no": g["bill_no"], "purchase_invoice": b.get("name")}
        if diffs:
            excess_at_risk += max(0.0, float(b.get("total_tax") or 0) - float(g["total_tax"]))
            out["mismatch"].append({**base, "differences": diffs, "gstr_2b": g, "books": b})
        else:
            out["matched"].append({**base, "taxable_value": g["taxable_value"], "total_tax": g["total_tax"]})

    for g in unmatched:
        cand = next((i for i, b in books.items()
                     if (b.get("supplier_gstin") or "").upper() == g["supplier_gstin"]
                     and abs(float(b.get("taxable_value") or 0) - float(g["taxable_value"])) <= tol
                     and abs(float(b.get("total_tax") or 0) - float(g["total_tax"])) <= tol), None)
        if cand is not None:
            b = books.pop(cand)
            out["suggested"].append({"supplier_gstin": g["supplier_gstin"], "purchase_invoice": b.get("name"),
                                     "gstr_2b_bill_no": g["bill_no"], "books_bill_no": b.get("bill_no"),
                                     "reason": "same supplier and amounts, bill number differs"})
        else:
            out["missing_in_books"].append(g)

    for b in books.values():
        note = None if b.get("supplier_gstin") else "supplier has no GSTIN in ERPNext"
        out["missing_in_2b"].append({"purchase_invoice": b.get("name"), "supplier": b.get("supplier"),
                                     "supplier_gstin": b.get("supplier_gstin"), "bill_no": b.get("bill_no"),
                                     "bill_date": b.get("bill_date"), "total_tax": b.get("total_tax"), "note": note})

    at_risk = sum(float(r.get("total_tax") or 0) for r in out["missing_in_2b"]) + excess_at_risk
    out["summary"] = {k: len(v) for k, v in out.items()}
    out["summary"]["itc_at_risk"] = round(at_risk, 2)
    return out
