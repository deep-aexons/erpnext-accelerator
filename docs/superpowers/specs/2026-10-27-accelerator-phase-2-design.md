# ERPNext Accelerator v0.2.0 — Phase 2 Design Spec

- Date: 2026-10-27 · Author: Deep (Aexons) with Claude · Status: Draft for review
- Builds on: `2026-10-27-erpnext-accelerator-design.md` (v0.1.0). All v0.1.0 rules (no delete, drafts, blocklist,
  double-gated submit, audit, retries) still apply.
- Feasibility notes: project doc `claude/feasibility-accelerator-phase-2.md`.

## 1. Goal

Add four capabilities to the plugin, usable in plain language:

1. **Procure-to-pay** — supplier quotations, purchase-order drafts, PO ↔ receipt ↔ invoice matching, payables ageing.
2. **Documents to drafts** — a PDF purchase order / invoice or a WhatsApp order photo becomes a draft Sales Order
   or Purchase Invoice.
3. **Daily briefing** — sales, cash, overdue invoices, low stock, pending approvals; on demand and via a scheduled task.
4. **India GST** — GSTIN/HSN/tax checks before filing and GSTR-2B reconciliation without any extra app; e-invoice /
   e-way bill status and generation when the India Compliance app is installed.

### Success criteria

- Each capability works from a plain-language request through a new skill.
- GST checks and 2B reconciliation work on core ERPNext (no India Compliance) using `tax_id` (GSTIN),
  `Item.customs_tariff_number` (HSN) and tax rows; they automatically use India Compliance fields when present.
- e-invoice / e-way generation runs only with profile `allow_gst_actions` (user-enabled outside Claude) AND `confirm=true`.
- All new tools unit-tested with mocked HTTP; v0.1.0 suite stays green.

### Non-goals

- Cancelling e-invoices / e-way bills (done in ERPNext UI).
- Downloading GSTR-2B from the GST portal (needs OTP; user downloads JSON, or India Compliance downloads it).
- GSTR-1/3B filing, ITC claim posting, payments.
- WhatsApp integration (user forwards the photo into Claude).
- OCR inside the server (Claude reads PDFs/images itself).

## 2. Facts this design relies on (verified in source, 2026-10-27)

- Local ERPNext/Frappe 17.0.0-dev; India localisation removed from core in v14.
- India Compliance `develop` is 17.0.0-dev and requires `frappe>=17.0.0-dev,<18`.
- India Compliance adds: `gstin`, `gst_category`, `gst_hsn_code` (Item + item tables), `supplier_gstin`,
  `billing_address_gstin`, `company_gstin`, `place_of_supply` ("27-Maharashtra"), `gst_tax_type` on tax rows
  (`cgst|sgst|igst|cess|…_rcm`), Sales Invoice `irn`, `einvoice_status`, `ewaybill`, `e_waybill_status`
  (options: Pending, Generated, Manually Generated, Auto-Retry, Cancelled, Manually Cancelled, Failed, Not Applicable,
  Pending Cancellation), DocType `GST Inward Supply` (2B rows: supplier_gstin, bill_no, bill_date, doc_type,
  classification, taxable_value, igst, cgst, sgst, cess, return_period_2b, match_status, link_name), `GST Settings`
  (single, holds `GST Credential` rows with passwords and `GST Account` rows).
- Whitelisted methods: `india_compliance.gst_india.utils.e_invoice.generate_e_invoice(docname, throw=True)`,
  `india_compliance.gst_india.utils.e_waybill.generate_e_waybill(*, doctype, docname, values=None)`.
- GSTR-2B JSON: `data.docdata.b2b[].{ctin, trdnm, inv[].{inum, dt (dd-mm-yyyy), val, txval, igst, cgst, sgst, cess, rev, itcavl, items[]?}}`
  and `cdnr[].{ctin, nt[].{ntnum, typ (C|D), dt, val, txval, …}}`. v4 has totals per document; v3 has them in `items[]`.
- Core: Purchase Order Item `received_qty`, `billed_amt`; Purchase Invoice Item `po_detail`, `pr_detail`;
  `Workflow Action` (status Open); `Item Reorder` (child of Item); report `Account Balance`
  (filters company, report_date, account_type); `erpnext.accounts.utils.get_balance_on` is NOT whitelisted.

## 3. Architecture changes

New modules in `server/src/erpnext_mcp/`, each registering tools on the existing `mcp` instance through the existing
`_tool` decorator (imported from `tools.py`; `tools.py` imports the modules at its end so registration happens on import):

| Module | Tools | Pure helpers (unit-testable without HTTP) |
|---|---|---|
| `p2p.py` | `three_way_match` | `match_flags(line, invoiced, tolerance_pct)` |
| `documents.py` | `find_party`, `match_items` | `score(a, b)`, `looks_like_gstin(s)` |
| `briefing.py` | `daily_briefing` | `render_markdown(briefing)` |
| `gst.py` | — | `validate_gstin`, `classify_tax_row`, `check_invoice`, `parse_gstr2b`, `reconcile` |
| `gst_tools.py` | `gst_capabilities`, `gst_validate_gstin`, `gst_check_invoices`, `gst_reconcile_2b`, `gst_status`, `gst_generate_e_invoice`, `gst_generate_e_waybill` | — |

Total tools: 14 → 25 (11 new).

Config/guards changes:
- `Profile.allow_gst_actions: bool = False`; env `ERPNEXT_ALLOW_GST_ACTIONS`; set only via `erpnext-mcp setup`, env or
  config file (the `connect` tool preserves it, never sets it).
- `guards.check_gst_action_allowed(profile, confirm)` — same two-key pattern as submit.
- Blocklist adds `GST Settings`, `GST Credential`.

CLI: `erpnext-mcp briefing [--company C] [--date YYYY-MM-DD] [--json]` prints the briefing (no Claude needed).
Command: `/erp-briefing`. Skills: `erpnext-procure-to-pay`, `erpnext-document-to-draft`, `erpnext-daily-briefing`,
`erpnext-gst`. Version 0.2.0.

## 4. Tool behaviour

### 4.1 `three_way_match(purchase_order=None, supplier=None, from_date=None, to_date=None, tolerance_pct=0.0, only_issues=True, limit=50)`
- Submitted POs (filters applied), newest first, capped by `limit` (max 200). For each: `get_doc` for item lines; one
  query for submitted Purchase Invoice Items with `po_detail` in the PO line names (child list via `parent=Purchase Invoice`).
- Per line flags (amount tolerance = `tolerance_pct`% of line amount, min ₹1):
  `not_received`, `partially_received`, `over_received`, `received_not_billed`, `billed_more_than_received`,
  `over_billed` (billed_amt > amount), `rate_mismatch` (any PI rate ≠ PO rate beyond tolerance).
- Returns `{orders: [...], summary: {orders_checked, orders_with_issues, flag_counts}, truncated}`.

### 4.2 `find_party(party_type, query, limit=5)`
- `party_type` ∈ Customer, Supplier. If `query` looks like a GSTIN: exact `tax_id` match first (and `gstin` when IC).
- Else name search: `like` on `customer_name`/`supplier_name` for the longest 1–2 tokens, ≤20 candidates, ranked by
  `score` (difflib ratio on normalised names). Returns `[{name, display_name, tax_id, score, match_by}]`.

### 4.3 `match_items(lines, supplier=None, limit_per_line=3)`
- `lines`: list of strings or `{description, code?, supplier_part_no?, hsn?}` (max 100).
- Order per line: exact Item name/code → barcode (`Item Barcode`) → supplier part no (`Item Supplier`, when `supplier`)
  → fuzzy name search. Returns per line `{input, candidates: [{item_code, item_name, stock_uom, score, match_by}], best, confident}`;
  `confident` = exact/barcode/part-no match or score ≥ 0.85.

### 4.4 `daily_briefing(company=None, date=None)`
Sections, each independently `{…}` or `{"error": msg}`:
- `sales`: submitted Sales Invoices on `date` (count, total), Sales Orders on `date` (count, total).
- `cash`: `Account Balance` report for account_type Bank and Cash (account, balance; total).
- `receivables_overdue` / `payables_overdue`: Sales/Purchase Invoices with status Overdue — count, total outstanding,
  top 5 parties.
- `low_stock`: items whose `Bin.actual_qty` < `Item Reorder.warehouse_reorder_level` (per warehouse), top 20.
- `approvals`: open `Workflow Action` counts per DocType; draft counts for Sales Order, Purchase Order,
  Purchase Invoice, Sales Invoice, Material Request.
Company: given, else the only Company, else error listing companies. Date defaults to today (server local date).
Sums are computed client-side from ≤500 rows per query; sections say `truncated` when capped.
Returns `{company, date, sections, markdown}`.

### 4.5 GST helpers (`gst.py`, pure)
- `validate_gstin(s)` → `{valid, gstin, state_code, pan, reason}`: regex `^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$`,
  state code 01–38 or 97, mod-36 checksum.
- `classify_tax_row(row)` → `cgst|sgst|igst|cess|None` using `gst_tax_type` (strip `_rcm`) else account head text
  (CGST, SGST/UTGST, IGST, Cess).
- `check_invoice(doc, ctx)` → issues `{check, severity (error|warning), message}`:
  `company_gstin_invalid`, `party_gstin_missing` (warning), `party_gstin_invalid`, `hsn_missing`, `hsn_format`
  (digits, length 4/6/8; SAC starts 99), `no_gst_tax` (warning), `wrong_tax_type` (intra-state needs CGST+SGST, no IGST;
  inter-state needs IGST only), `cgst_sgst_unequal`, `rate_not_standard` (IGST ∉ {0,0.1,0.25,1,1.5,3,5,6,7.5,12,18,28};
  CGST/SGST at half), `bill_no_missing` (Purchase Invoice).
  State of supply: IC `place_of_supply` prefix, else party GSTIN state; company state from company GSTIN.
- `parse_gstr2b(json)` → rows `{supplier_gstin, supplier_name, bill_no, bill_date (ISO), doc_type, taxable_value, igst, cgst, sgst, cess, total_tax, reverse_charge, itc_available}`
  from `b2b.inv` and `cdnr.nt` (credit notes negative); totals from document or summed `items`.
- `reconcile(gstr_rows, book_rows, amount_tolerance=1.0)`: key `(gstin, norm(bill_no))` where `norm` uppercases and
  drops spaces, `/`, `-`, leading zeros. Statuses: `matched`, `mismatch` (fields: bill_date, taxable_value, total_tax),
  `suggested` (same GSTIN, amounts within tolerance, bill no differs), `missing_in_books`, `missing_in_2b`.
  Summary: counts per status and `itc_at_risk` (total_tax of `missing_in_2b` + `mismatch`).

### 4.6 GST tools (`gst_tools.py`)
- `gst_capabilities()` → `{india_compliance: bool, versions, hsn_field: "gst_hsn_code"|"customs_tariff_number"}`.
- `gst_validate_gstin(gstin)` → `validate_gstin`.
- `gst_check_invoices(doctype="Purchase Invoice"|"Sales Invoice", from_date, to_date, company=None, include_drafts=True, limit=100)`
  → `{invoices_checked, issues: [{invoice, check, severity, message}], summary, truncated}`. Party `tax_id`, Item
  `customs_tariff_number` and Company `tax_id` fetched once per run (cached).
- `gst_reconcile_2b(source="file"|"india_compliance", file_path=None, from_date=None, to_date=None, company=None, amount_tolerance=1.0, limit=100)`.
  Book rows: submitted Purchase Invoices with posting_date in range (`bill_no, bill_date, base_net_total,
  base_total_taxes_and_charges, supplier`, plus `supplier_gstin` when IC else Supplier `tax_id`).
  `india_compliance` source reads `GST Inward Supply` (classification B2B/CDNR, bill_date in range).
  Lists per status capped at `limit`.
- `gst_status(doctype="Sales Invoice", from_date, to_date, company=None, limit=100)` — needs IC; lists submitted
  documents whose e-invoice / e-way status is Pending, Failed, Auto-Retry or empty-and-not-Not-Applicable.
- `gst_generate_e_invoice(name, confirm=False)` / `gst_generate_e_waybill(doctype, name, values=None, confirm=False)`:
  blocklist + `check_gst_action_allowed`, POST the IC method, then re-read the document and return `irn` /
  `einvoice_status` or `ewaybill` / `e_waybill_status`. Audited. Without IC: clear error.

## 5. Skills (plain-language triggers)
- `erpnext-procure-to-pay`: RFQ/supplier quotation comparison (`run_report("Supplier Quotation Comparison")`),
  PO drafts from approved quotation or reorder needs, `three_way_match`, payables ageing (`run_report("Accounts Payable")`).
- `erpnext-document-to-draft`: read the document → extract header + lines → `find_party` → `match_items` → show a
  mapping table with doubtful lines marked → user confirms → `create_draft` (Sales Order or Purchase Invoice; PO when
  asked). Never invent items; unmatched lines are asked about. Check totals against the document.
- `erpnext-daily-briefing`: `daily_briefing` → short narrative + key numbers; how to schedule it.
- `erpnext-gst`: pre-filing checks, 2B reconciliation, e-invoice/e-way status, gated generation.

## 6. Testing
Unit tests per module with respx; pure GST helpers with table-driven tests (valid/invalid GSTINs with real checksum,
intra/inter-state, CGST≠SGST, HSN formats); a hand-written GSTR-2B fixture (v3 items style and v4 totals style; not
copied from India Compliance, which is GPL); reconciliation statuses incl. suggested match; briefing partial failure;
plugin-file tests extended to new skills/commands/docs; tool count 25.

## 7. Risks
- India Compliance APIs change between versions → generation tools only call two documented whitelisted methods;
  errors are passed through readably.
- Large sites: briefing/2B queries capped at 500 rows per query and say `truncated`.
- Core-only GST data quality depends on users filling `tax_id` and `customs_tariff_number`.
- Scheduled briefing needs the machine that can reach ERPNext to be awake (local) or a public site.
