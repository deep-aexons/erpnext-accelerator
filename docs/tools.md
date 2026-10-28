# Tool reference

The MCP server is named `erpnext` and has 25 tools. In Claude the tools appear as `mcp__…erpnext__<tool>`.
All tools read the active profile at call time (see [setup.md](setup.md)). Errors come back as one readable
line, e.g. `ERPNext error 417 (ValidationError): Row 1: Qty required`.

List-shaped results use `{"rows": [...], "count": n, "truncated": bool}`. `limit` is capped at 500.

## Connection

### `connect`

Save a profile after verifying it with a login check. Nothing is saved if the check fails.

| Param | Type | Default | Notes |
|---|---|---|---|
| `profile` | str | — | e.g. `dev` |
| `url` | str | — | `localhost:8000` is normalised to `http://localhost:8000` |
| `api_key`, `api_secret` | str | — | from ERPNext API Access |
| `site` | str | none | sent as `Host` header on multi-site benches |
| `make_default` | bool | `true` | |

Keeps the profile's existing submit setting. Claude cannot enable submit; the user does that with
`erpnext-mcp setup` in a terminal or `ERPNEXT_ALLOW_SUBMIT=1`.

Returns `{profile, user, url, site, allow_submit, default, saved_to}` (never the secret).

### `use_profile`

Switch the default profile. `use_profile("prod")` → `{profile, url, known_profiles}`. Unknown names list the known ones.
Refused while `ERPNEXT_PROFILE` is set in the environment (it would override the switch).

### `diagnose`

Health check used by `/erp-doctor`. Never raises. Returns `{ok, checks: [{name, ok, detail}]}` with checks
`config`, `reachable`, `auth`, `versions`, `read:Item`, `read:Customer`, `read:Sales Order`, `read:Bin`, `submit`.
Runs without retries so a down site is reported quickly.

### `ping`

`ping()` → `{user, url, site, profile}`.

## Read

### `list_docs`

`list_docs(doctype, filters=None, fields=None, order_by=None, limit=20)`

- `filters`: dict `{"status": "Overdue"}` or list `[["grand_total", ">", 50000]]`
- `fields`: default `["name"]`
- Example: `list_docs("Sales Invoice", {"status": "Overdue"}, ["name", "customer", "outstanding_amount"], "due_date asc", 100)`

### `get_doc`

`get_doc(doctype, name)` → the full document, including child tables.

### `count_docs`

`count_docs(doctype, filters=None)` → integer. Use it to check whether a list was truncated.

### `get_doctype_schema`

`get_doctype_schema(doctype)` → `{doctype, is_submittable, fields: [{fieldname, label, fieldtype, options, reqd}], child_tables: {name: fields}}`.
Layout fields (section/column/tab breaks, HTML, buttons) are removed.

### `run_report`

`run_report(report_name, filters=None, limit=100)` → `{report, columns, rows, count, truncated}`.
`count` is the total rows the report returned before capping. Required filters differ per report; ERPNext's
error message names the missing one. Reports configured as *prepared* (background) reports return a clear
error instead of an empty result.

Common reports: `Accounts Receivable`, `Accounts Receivable Summary`, `Stock Balance`, `Stock Ledger`,
`Stock Projected Qty`, `Sales Analytics`, `General Ledger`.

### `get_stock_levels`

`get_stock_levels(item_code=None, warehouse=None, include_zero=False, limit=100)`
→ rows from `Bin` with `item_code, warehouse, actual_qty, reserved_qty, ordered_qty, projected_qty`.
Zero-stock rows are skipped unless `include_zero=true`.

## Write (drafts)

### `create_draft`

`create_draft(doctype, data)` → `{name, doctype, docstatus: 0, url}`. `docstatus` is always forced to 0.
Refused for blocklisted DocTypes and for code values on Custom Field / Property Setter ([security.md](security.md)). Audited.

### `update_draft`

`update_draft(doctype, name, data)` → `{name, doctype, docstatus, modified, url}`.
Refused if the document is submitted/cancelled, or if `data` tries to change `docstatus`, `name`, `doctype`,
`owner`, `creation`, `modified` or `amended_from`. Same code-value checks as `create_draft`. Audited.

## Gated

### `submit_doc`

`submit_doc(doctype, name, confirm=false)`. Runs only when the profile has `allow_submit` **and** `confirm=true`.
Claude is instructed to summarise the document and get an explicit yes first. Audited.

### `cancel_doc`

`cancel_doc(doctype, name, confirm=false)`. Same gate as `submit_doc`. Audited.

## Procure to pay

### `three_way_match`

`three_way_match(purchase_order=None, supplier=None, from_date=None, to_date=None, tolerance_pct=0.0, only_issues=True, limit=50)`
→ `{orders: [{name, supplier, transaction_date, status, per_received, per_billed, lines: [{item_code, ordered_qty, received_qty, rate, amount, billed_amt, invoices, flags}], has_issues}], summary: {orders_checked, orders_with_issues, flag_counts}, truncated}`.

Flags: `not_received`, `partially_received`, `over_received`, `received_not_billed`, `billed_more_than_received`,
`over_billed`, `rate_mismatch`. `tolerance_pct` applies to qty, amount and rate (amount tolerance at least ₹1).
Max 200 orders per call.

## Documents to drafts

### `find_party`

`find_party(party_type, query, limit=5)` — `party_type` is `Customer` or `Supplier`. A GSTIN-shaped query matches
`tax_id` exactly (`match_by: "gstin"`); otherwise name search ranked by similarity (Pvt/Ltd/Co expanded).
→ `[{name, display_name, tax_id, score, match_by}]`.

### `match_items`

`match_items(lines, supplier=None, limit_per_line=3)` — `lines`: strings or `{description, code?, supplier_part_no?, hsn?}`, max 100.
Order: exact item code → barcode (8–14 digits) → supplier part number (needs `supplier`) → name similarity.
→ `{lines: [{input, candidates, best, confident}], unmatched}`; `confident` = exact/barcode/part-no match or score ≥ 0.85.

## Daily briefing

### `daily_briefing`

`daily_briefing(company=None, date=None)` → `{company, date, sections, markdown}`. Sections: `sales` (invoices and
orders on the date), `cash` (Account Balance report for leaf Bank/Cash accounts of the company, `totals` per
currency, `total` only when single-currency), `receivables_overdue`, `payables_overdue`
(count, total, top 5 parties), `low_stock` (on hand below Item reorder level), `approvals` (open Workflow Actions
per DocType, draft counts). A section that fails returns `{error}`; the rest still work. Lists are paged up to
5,000 rows; capped totals are marked `truncated` and shown as *partial* in the markdown. Also available as
`erpnext-mcp briefing` and `/erp-briefing`.

## India GST

Without India Compliance the GST tools use core fields: GSTIN = `tax_id`, HSN = Item `customs_tariff_number`.
With it they use `gstin`, `company_gstin`, `supplier_gstin`, `billing_address_gstin`, `gst_hsn_code`,
`place_of_supply` and `gst_tax_type`.

### `gst_capabilities`

`gst_capabilities()` → `{india_compliance, versions, hsn_field, gstin_field, e_invoice_e_waybill}`.

### `gst_validate_gstin`

`gst_validate_gstin(gstin)` → `{valid, gstin, state_code, pan, reason}`. Offline: format, state code (01–38, 97), check digit.

### `gst_check_invoices`

`gst_check_invoices(doctype, from_date, to_date, company=None, include_drafts=True, limit=100)` — `doctype` is
`Sales Invoice` or `Purchase Invoice`; max 200 invoices per call.
→ `{invoices_checked, issues: [{invoice, check, severity, message}], summary: {errors, warnings, invoices_with_errors, by_check}, truncated, data_source}`.
Checks: `company_gstin_invalid`, `party_gstin_missing` (warning), `party_gstin_invalid`, `hsn_missing`, `hsn_format`,
`no_gst_tax` (warning), `wrong_tax_type`, `cgst_sgst_unequal`, `rate_not_standard`, `bill_no_missing`,
`bill_date_missing`. Standard slabs include 40% (GST 2.0). SEZ and Overseas parties (India Compliance `gst_category`)
are treated as inter-state; purchases from a supplier without a known state skip the CGST/SGST-vs-IGST check.

### `gst_reconcile_2b`

`gst_reconcile_2b(source="file", file_path=None, from_date=None, to_date=None, company=None, amount_tolerance=1.0, limit=100)`.
`source="file"` reads a GSTR-2B JSON from the GST portal (B2B invoices and CDNR notes; amendments are listed in
`skipped`); dates default to the file's return month (`rtnprd`). `source="india_compliance"` reads `GST Inward Supply`
(credit notes made negative). Book side: submitted Purchase Invoices **posted** in the period, plus out-of-period
invoices whose bill matches a 2B row (late-filed bills). Book tax = CGST/SGST/IGST/Cess "Add" rows only (TDS, freight
ignored). Refuses with a clear message above 5,000 rows instead of giving partial results. Match key: supplier GSTIN + bill number ignoring case, spaces,
`/`, `-` and leading zeros.
→ `{summary: {matched, mismatch, suggested, missing_in_books, missing_in_2b, itc_at_risk}, matched, mismatch, suggested, missing_in_books, missing_in_2b, truncated, period, skipped, notes}`.

### `gst_status`

`gst_status(doctype="Sales Invoice", from_date, to_date, company=None, limit=100)` — India Compliance only. Lists
submitted documents whose e-invoice / e-way status is Pending, Failed or Auto-Retry.
→ `{e_invoice_pending, e_waybill_pending, counts, truncated}`.

### `gst_generate_e_invoice`

`gst_generate_e_invoice(name, confirm=false)` — India Compliance only. Calls
`india_compliance.gst_india.utils.e_invoice.generate_e_invoice`, then returns `{name, irn, einvoice_status}`.
Runs only when the profile has `allow_gst_actions` **and** `confirm=true`. Audited.

### `gst_generate_e_waybill`

`gst_generate_e_waybill(doctype, name, values=None, confirm=false)` — India Compliance only. Calls
`india_compliance.gst_india.utils.e_waybill.generate_e_waybill`; `values` carries transport details
(`transporter`, `vehicle_no`, `distance`, `mode_of_transport`, …). Same gate as e-invoice. Audited.

## Retry behaviour

| Request | Retried on | Not retried |
|---|---|---|
| Reads (GET) | 429, 500, 502, 503, 504, network errors | other 4xx |
| Writes (POST/PUT) | 429, connection refused/failed | 5xx, timeouts after sending (avoids duplicates) |

A write that times out after it was sent returns an `UncertainWrite` error: "it may have been applied —
check with list_docs before trying again".

Backoff: `random() × min(8 s, 0.5 s × 2^attempt)`, or the `Retry-After` header (max 60 s). Default 3 retries.
