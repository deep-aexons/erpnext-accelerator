"""India GST tools: checks and GSTR-2B reconciliation (core ERPNext), status and generation (India Compliance)."""

from __future__ import annotations

import calendar
import json
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

from .client import ERPNextClient
from .errors import ERPNextError
from .gst import (
    check_invoice,
    classify_tax_row,
    norm_bill_no,
    parse_gstr2b,
    reconcile,
    validate_gstin,
)
from .guards import check_gst_action_allowed, check_writable
from .tools import _audited, _chunks, _clamp, _connect, _fetch_all, _tool

IC_APP = "india_compliance"
IC_E_INVOICE = "india_compliance.gst_india.utils.e_invoice.generate_e_invoice"
IC_E_WAYBILL = "india_compliance.gst_india.utils.e_waybill.generate_e_waybill"
NEEDS_IC = "This needs the India Compliance app installed on the ERPNext site."
INVOICE_DOCTYPES = ("Purchase Invoice", "Sales Invoice")
PENDING = {"Pending", "Failed", "Auto-Retry"}
ROW_CAP = 500
MAX_ROWS = 5000

__all__ = [
    "gst_capabilities",
    "gst_check_invoices",
    "gst_generate_e_invoice",
    "gst_generate_e_waybill",
    "gst_reconcile_2b",
    "gst_status",
    "gst_validate_gstin",
]


def _versions(client: ERPNextClient) -> dict:
    try:
        return client.call_method("frappe.utils.change_log.get_versions") or {}
    except ERPNextError:
        return {}


def _has_ic(client: ERPNextClient) -> bool:
    return IC_APP in _versions(client)


def _require_ic(client: ERPNextClient) -> None:
    if not _has_ic(client):
        raise ToolError(NEEDS_IC)


def _rows(client: ERPNextClient, doctype: str, filters, fields, limit: int = ROW_CAP, order_by=None) -> list[dict]:
    params = {"filters": json.dumps(filters), "fields": json.dumps(fields), "limit_page_length": limit}
    if order_by:
        params["order_by"] = order_by
    return client.get(client.resource_path(doctype), params=params).get("data", [])


def _lookup(client: ERPNextClient, doctype: str, names: set, fields: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for chunk in _chunks(sorted({n for n in names if n})):
        for r in _rows(client, doctype, [["name", "in", chunk]], ["name", *fields], limit=len(chunk)):
            out[r["name"]] = r
    return out


def _date_range(from_date, to_date) -> list:
    return ["posting_date", "between", [from_date, to_date]]


@_tool
def gst_capabilities() -> dict:
    """What GST features this site supports: is India Compliance installed, and which field holds HSN codes.

    Without India Compliance: GSTIN = party/company `tax_id`, HSN = Item `customs_tariff_number`.
    """
    _, client = _connect()
    with client:
        versions = _versions(client)
    ic = IC_APP in versions
    return {
        "india_compliance": ic,
        "versions": {app: (v or {}).get("version") for app, v in versions.items()},
        "hsn_field": "gst_hsn_code" if ic else "customs_tariff_number",
        "gstin_field": "gstin (party) / company_gstin, supplier_gstin, billing_address_gstin (documents)" if ic
        else "tax_id on Customer, Supplier and Company",
        "e_invoice_e_waybill": ic,
    }


@_tool
def gst_validate_gstin(gstin: str) -> dict:
    """Check a GSTIN offline: format, state code and check digit. Returns {valid, gstin, state_code, pan, reason}."""
    return validate_gstin(gstin)


@_tool
def gst_check_invoices(
    doctype: str,
    from_date: str,
    to_date: str,
    company: str | None = None,
    include_drafts: bool = True,
    limit: int = 100,
) -> dict:
    """Find GST mistakes before filing in Purchase Invoices or Sales Invoices of a date range.

    Checks: company/party GSTIN (format + checksum), HSN/SAC present and well-formed, CGST+SGST vs IGST by state,
    CGST = SGST, standard GST rates, supplier bill number. Example:
    gst_check_invoices("Sales Invoice", "2026-09-01", "2026-09-30").
    """
    if doctype not in INVOICE_DOCTYPES:
        raise ToolError(f"doctype must be one of {', '.join(INVOICE_DOCTYPES)}")
    limit = min(_clamp(limit, 100), 200)
    is_sales = doctype == "Sales Invoice"
    party_field, party_doctype = ("customer", "Customer") if is_sales else ("supplier", "Supplier")
    party_gstin_field = "billing_address_gstin" if is_sales else "supplier_gstin"

    filters = [_date_range(from_date, to_date), ["docstatus", "<", 2] if include_drafts else ["docstatus", "=", 1]]
    if company:
        filters.append(["company", "=", company])

    _, client = _connect()
    with client:
        ic = _has_ic(client)
        names = [r["name"] for r in _rows(client, doctype, filters, ["name"], limit=limit + 1, order_by="posting_date asc")]
        truncated = len(names) > limit
        docs = [client.get(client.resource_path(doctype, n)).get("data", {}) for n in names[:limit]]

        need_company = {d.get("company") for d in docs if not d.get("company_gstin")}
        need_party = {d.get(party_field) for d in docs if not d.get(party_gstin_field)}
        need_items = {i.get("item_code") for d in docs for i in d.get("items") or [] if not i.get("gst_hsn_code")}
        hsn_field = "gst_hsn_code" if ic else "customs_tariff_number"
        companies = _lookup(client, "Company", need_company, ["tax_id"])
        parties = _lookup(client, party_doctype, need_party, ["tax_id"])
        items = _lookup(client, "Item", need_items, [hsn_field])

    issues, by_check = [], {}
    for d in docs:
        pos = (d.get("place_of_supply") or "")[:2]
        ctx = {
            "company_gstin": d.get("company_gstin") or companies.get(d.get("company"), {}).get("tax_id"),
            "party_gstin": d.get(party_gstin_field) or parties.get(d.get(party_field), {}).get("tax_id"),
            "item_hsn": {code: row.get(hsn_field) for code, row in items.items()},
            "place_of_supply_state": pos if pos.isdigit() else None,
        }
        for issue in check_invoice(d, ctx):
            issues.append({"invoice": d.get("name"), **issue})
            by_check[issue["check"]] = by_check.get(issue["check"], 0) + 1

    return {
        "doctype": doctype,
        "invoices_checked": len(docs),
        "issues": issues,
        "summary": {
            "errors": sum(1 for i in issues if i["severity"] == "error"),
            "warnings": sum(1 for i in issues if i["severity"] == "warning"),
            "invoices_with_errors": len({i["invoice"] for i in issues if i["severity"] == "error"}),
            "by_check": by_check,
        },
        "truncated": truncated,
        "data_source": "India Compliance fields" if ic else "core fields (tax_id, customs_tariff_number)",
    }


def _ic_inward_rows(client: ERPNextClient, from_date: str, to_date: str, company: str | None) -> list[dict]:
    filters = [["bill_date", "between", [from_date, to_date]], ["classification", "in", ["B2B", "CDNR"]]]
    if company:
        filters.append(["company", "=", company])
    fields = ["supplier_gstin", "supplier_name", "bill_no", "bill_date", "doc_type", "taxable_value",
              "igst", "cgst", "sgst", "cess", "is_reverse_charge", "itc_availability"]
    rows, truncated = _fetch_all(client, "GST Inward Supply", filters, fields, max_rows=MAX_ROWS)
    if truncated:
        raise ToolError(f"More than {MAX_ROWS} GSTR-2B rows in this range; narrow the dates.")
    out = []
    for r in rows:
        sign = -1 if r.get("doc_type") == "Credit Note" else 1  # India Compliance stores notes as positive
        taxes = {k: sign * float(r.get(k) or 0) for k in ("igst", "cgst", "sgst", "cess")}
        out.append({
            "supplier_gstin": (r.get("supplier_gstin") or "").upper(), "supplier_name": r.get("supplier_name"),
            "bill_no": r.get("bill_no"), "bill_date": r.get("bill_date"), "doc_type": r.get("doc_type"),
            "taxable_value": sign * float(r.get("taxable_value") or 0), **taxes,
            "total_tax": round(sum(taxes.values()), 2), "reverse_charge": bool(r.get("is_reverse_charge")),
            "itc_available": r.get("itc_availability") != "No",
        })
    return out


def _gst_tax_by_invoice(client: ERPNextClient, names: list[str], ic: bool) -> dict[str, float]:
    """Sum of GST rows (CGST/SGST/IGST/Cess, 'Add' only) per Purchase Invoice; ignores TDS, freight etc."""
    fields = ["parent", "account_head", "description", "add_deduct_tax", "base_tax_amount"]
    if ic:
        fields.append("gst_tax_type")
    totals: dict[str, float] = {}
    for chunk in _chunks(names):
        rows, _ = _fetch_all(client, "Purchase Taxes and Charges", [["parent", "in", chunk]], fields,
                             parent="Purchase Invoice", max_rows=MAX_ROWS)
        for r in rows:
            if r.get("add_deduct_tax") == "Deduct" or not classify_tax_row(r):
                continue
            totals[r["parent"]] = totals.get(r["parent"], 0.0) + float(r.get("base_tax_amount") or 0)
    return {k: round(v, 2) for k, v in totals.items()}


def _book_rows(client: ERPNextClient, from_date: str, to_date: str, company: str | None, ic: bool,
               gstr_rows: list[dict]) -> list[dict]:
    """Purchase Invoices posted in the period, plus earlier/later ones whose bill matches a 2B row."""
    base = [["docstatus", "=", 1]] + ([["company", "=", company]] if company else [])
    fields = ["name", "supplier", "bill_no", "bill_date", "posting_date", "base_net_total"]
    if ic:
        fields.append("supplier_gstin")
    rows, truncated = _fetch_all(client, "Purchase Invoice", base + [["posting_date", "between", [from_date, to_date]]],
                                 fields, max_rows=MAX_ROWS)
    if truncated:
        raise ToolError(f"More than {MAX_ROWS} purchase invoices in this period; narrow the dates or pass company.")

    in_period = {r["name"] for r in rows}
    wanted = sorted({g["bill_no"] for g in gstr_rows if g.get("bill_no")})
    extra: list[dict] = []
    for chunk in _chunks(wanted):
        found, _ = _fetch_all(client, "Purchase Invoice", base + [["bill_no", "in", chunk]], fields, max_rows=MAX_ROWS)
        extra += [r for r in found if r["name"] not in in_period]

    gstins: dict = {}
    if not ic:
        gstins = {k: v.get("tax_id") for k, v in
                  _lookup(client, "Supplier", {r.get("supplier") for r in rows + extra}, ["tax_id"]).items()}

    def to_book(r):
        return {"name": r["name"], "supplier": r.get("supplier"),
                "supplier_gstin": (r.get("supplier_gstin") or gstins.get(r.get("supplier")) or "").upper() or None,
                "bill_no": r.get("bill_no"), "bill_date": r.get("bill_date"),
                "taxable_value": float(r.get("base_net_total") or 0)}

    books = [to_book(r) for r in rows]
    keys = {(g["supplier_gstin"], norm_bill_no(g["bill_no"])) for g in gstr_rows}
    books += [b for b in map(to_book, extra) if (b["supplier_gstin"], norm_bill_no(b["bill_no"])) in keys]
    taxes = _gst_tax_by_invoice(client, [b["name"] for b in books], ic)
    for b in books:
        b["total_tax"] = taxes.get(b["name"], 0.0)
    return books


def _month_range(return_period: str | None) -> tuple[str | None, str | None]:
    """'092026' → ('2026-09-01', '2026-09-30')."""
    rp = (return_period or "").strip()
    if len(rp) != 6 or not rp.isdigit() or not 1 <= int(rp[:2]) <= 12:
        return None, None
    month, year = int(rp[:2]), int(rp[2:])
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


@_tool
def gst_reconcile_2b(
    source: str = "file",
    file_path: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    company: str | None = None,
    amount_tolerance: float = 1.0,
    limit: int = 100,
) -> dict:
    """Reconcile GSTR-2B against submitted Purchase Invoices.

    source="file": GSTR-2B JSON downloaded from the GST portal; pass its local file_path. Dates default to the
    file's return month.
    source="india_compliance": use 2B data India Compliance already downloaded (GST Inward Supply); needs dates.
    Books = Purchase Invoices posted in the period (the 2B return month for files), plus late bills that match.
    Matches by supplier GSTIN + bill number (ignoring case, spaces, / - and leading zeros); amounts within
    amount_tolerance rupees. Statuses: matched, mismatch, suggested, missing_in_books, missing_in_2b; summary has
    itc_at_risk.
    """
    limit = _clamp(limit, 100)
    skipped: dict = {}
    _, client = _connect()
    with client:
        ic = _has_ic(client)
        if source == "file":
            if not file_path:
                raise ToolError("file_path is required for source='file' (the GSTR-2B JSON from the GST portal).")
            path = Path(file_path).expanduser()
            if not path.is_file():
                raise ToolError(f"GSTR-2B file not found: {path}")
            try:
                parsed = parse_gstr2b(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ToolError(f"Could not read GSTR-2B JSON ({exc}).") from None
            gstr_rows, skipped = parsed["rows"], parsed["skipped"]
            if not gstr_rows:
                raise ToolError(f"The GSTR-2B file has no B2B/CDNR documents to reconcile (skipped: {skipped or 'none'}).")
            month_from, month_to = _month_range(parsed.get("return_period"))
            dates = sorted(r["bill_date"] for r in gstr_rows if r.get("bill_date"))
            from_date = from_date or month_from or dates[0]
            to_date = to_date or month_to or dates[-1]
        elif source == "india_compliance":
            if not ic:
                raise ToolError(NEEDS_IC)
            if not (from_date and to_date):
                raise ToolError("from_date and to_date are required for source='india_compliance'.")
            gstr_rows = _ic_inward_rows(client, from_date, to_date, company)
        else:
            raise ToolError("source must be 'file' or 'india_compliance'.")
        books = _book_rows(client, from_date, to_date, company, ic, gstr_rows)

    result = reconcile(gstr_rows, books, amount_tolerance)
    lists = {}
    for key in ("matched", "mismatch", "suggested", "missing_in_books", "missing_in_2b"):
        lists[key] = result[key][:limit]
    return {
        "summary": result["summary"],
        **lists,
        "truncated": any(len(result[k]) > limit for k in lists),
        "period": {"from_date": from_date, "to_date": to_date},
        "skipped": skipped,
        "notes": ["Books = Purchase Invoices posted in the period, plus out-of-period invoices whose bill matches a "
                  "2B row. Book tax = CGST/SGST/IGST/Cess rows only (TDS, freight etc. ignored)."],
    }


@_tool
def gst_status(
    doctype: str = "Sales Invoice",
    from_date: str | None = None,
    to_date: str | None = None,
    company: str | None = None,
    limit: int = 100,
) -> dict:
    """e-Invoice and e-Way Bill status of submitted documents (needs India Compliance).

    Lists documents whose status is Pending, Failed or Auto-Retry. doctype: Sales Invoice (e-invoice + e-way)
    or Delivery Note / Purchase Invoice / Purchase Receipt (e-way only).
    """
    if not (from_date and to_date):
        raise ToolError("from_date and to_date are required.")
    limit = _clamp(limit, 100)
    is_si = doctype == "Sales Invoice"
    fields = ["name", "posting_date", "grand_total", "ewaybill", "e_waybill_status"]
    fields.insert(1, "customer" if doctype in ("Sales Invoice", "Delivery Note") else "supplier")
    if is_si:
        fields += ["irn", "einvoice_status"]
    filters = [["docstatus", "=", 1], _date_range(from_date, to_date)]
    if company:
        filters.append(["company", "=", company])
    _, client = _connect()
    with client:
        _require_ic(client)
        rows = _rows(client, doctype, filters, fields, limit=ROW_CAP + 1, order_by="posting_date asc")
    truncated = len(rows) > ROW_CAP
    rows = rows[:ROW_CAP]
    einv = [r for r in rows if is_si and r.get("einvoice_status") in PENDING]
    ewb = [r for r in rows if r.get("e_waybill_status") in PENDING]
    return {
        "doctype": doctype,
        "e_invoice_pending": einv[:limit],
        "e_waybill_pending": ewb[:limit],
        "counts": {"documents": len(rows), "e_invoice_pending": len(einv), "e_waybill_pending": len(ewb)},
        "truncated": truncated,
    }


@_tool
def gst_generate_e_invoice(name: str, confirm: bool = False) -> dict:
    """Generate the e-Invoice (IRN) for a submitted Sales Invoice through India Compliance.

    Reported to the government portal; cannot be undone here. Only when the user explicitly asks: show a summary,
    get a clear yes, then call with confirm=true. Also requires GST actions enabled for the profile.
    """
    prof, client = _connect()

    def action():
        check_writable("Sales Invoice")
        check_gst_action_allowed(prof, confirm)
        with client:
            if not _has_ic(client):
                raise ERPNextError(NEEDS_IC)
            client.call_method(IC_E_INVOICE, http="POST", docname=name)
            doc = client.get(client.resource_path("Sales Invoice", name)).get("data", {})
        return {"name": name, "irn": doc.get("irn"), "einvoice_status": doc.get("einvoice_status")}

    return _audited("gst_generate_e_invoice", prof, "Sales Invoice", name, action)


@_tool
def gst_generate_e_waybill(doctype: str, name: str, values: dict | None = None, confirm: bool = False) -> dict:
    """Generate the e-Way Bill for a submitted document through India Compliance.

    doctype: Sales Invoice, Delivery Note, Purchase Invoice, Purchase Receipt, Stock Entry.
    values: transport details if missing on the document, e.g. {"transporter": "...", "vehicle_no": "MH12AB1234",
    "distance": 40, "mode_of_transport": "Road"}. Same rules as gst_generate_e_invoice: explicit yes, confirm=true.
    """
    prof, client = _connect()

    def action():
        check_writable(doctype)
        check_gst_action_allowed(prof, confirm)
        with client:
            if not _has_ic(client):
                raise ERPNextError(NEEDS_IC)
            payload = {"doctype": doctype, "docname": name}
            if values:
                payload["values"] = json.dumps(values)
            client.call_method(IC_E_WAYBILL, http="POST", **payload)
            doc = client.get(client.resource_path(doctype, name)).get("data", {})
        return {"doctype": doctype, "name": name, "ewaybill": doc.get("ewaybill"),
                "e_waybill_status": doc.get("e_waybill_status")}

    return _audited("gst_generate_e_waybill", prof, doctype, name, action)
