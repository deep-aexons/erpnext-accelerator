---
name: erpnext-gst
description: Use when the user asks about India GST in ERPNext - GSTIN or HSN checks, GST mistakes before filing GSTR-1 or 3B, reconciling GSTR-2B with purchase invoices, input tax credit at risk, or e-invoice (IRN) and e-way bill status or generation.
---

# India GST in ERPNext

Start with `gst_capabilities()`: it tells you whether the India Compliance app is installed and where HSN lives
(`gst_hsn_code` with India Compliance, else Item `customs_tariff_number`; GSTIN is `tax_id` without it).

## Check before filing

`gst_check_invoices("Sales Invoice", FROM, TO)` (outward, GSTR-1) and/or `gst_check_invoices("Purchase Invoice", FROM, TO)`
(inward / ITC). Group issues by check, errors first, and give the fix for each:

| Check | Fix |
|---|---|
| company_gstin_invalid / party_gstin_invalid | correct GSTIN on Company / Customer / Supplier (`tax_id` or GSTIN field) |
| party_gstin_missing | add GSTIN if the party is registered (B2B) |
| hsn_missing / hsn_format | set HSN (4/6/8 digits) or SAC (6 digits, starts 99) on the Item |
| wrong_tax_type | same state → CGST+SGST; different state → IGST: fix the tax template |
| cgst_sgst_unequal / rate_not_standard | fix tax rates in the template |
| bill_no_missing / bill_date_missing | enter supplier invoice number and date on the Purchase Invoice |

Submitted invoices can't be edited from here: tell the user to cancel and amend in ERPNext. Drafts can be fixed
with `update_draft` after the user agrees. A single GSTIN: `gst_validate_gstin(G)`.

## GSTR-2B reconciliation

- With a downloaded file (GST portal → GSTR-2B → download JSON): `gst_reconcile_2b(file_path="/path/to/file.json")`.
- With India Compliance's downloaded data: `gst_reconcile_2b(source="india_compliance", from_date=FROM, to_date=TO)`.

The file's return month sets the period; invoices booked in other months still match late-filed bills.
Book tax counts only GST rows, so TDS or freight on an invoice does not cause a mismatch.

Explain: matched; mismatch (which fields); suggested (likely the same bill with a typo in the bill number —
ask the user to correct `bill_no`); missing_in_books (supplier filed, we have no invoice — enter it or ask why);
missing_in_2b (we booked it, supplier hasn't filed — ITC at risk, chase supplier). Lead with `itc_at_risk`.

## e-Invoice and e-Way Bill (India Compliance only)

- Status: `gst_status("Sales Invoice", FROM, TO)`; for transport documents use `"Delivery Note"`.
- Generate only when the user explicitly asks for a specific document: show its summary (customer, GSTIN, value,
  date), say it will be reported to the government portal and cannot be undone here, get a clear yes, then
  `gst_generate_e_invoice(NAME, confirm=true)` or
  `gst_generate_e_waybill(DOCTYPE, NAME, values={"vehicle_no": ..., "distance": ..., "mode_of_transport": "Road"}, confirm=true)`.
- If refused because GST actions are off, the user can enable them in a terminal with `erpnext-mcp setup`;
  never try to enable it yourself. Cancelling is done in ERPNext.
