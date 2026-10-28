---
name: erpnext-procure-to-pay
description: Use when the user asks about buying in ERPNext - supplier quotations, comparing suppliers, creating purchase orders, what has been received or billed, matching purchase orders to receipts and supplier invoices, or what we owe suppliers (payables ageing).
---

# ERPNext procure to pay

## Compare supplier quotations

1. List them: `list_docs("Supplier Quotation", {"docstatus": 1}, ["name", "supplier", "transaction_date", "grand_total", "status"], "transaction_date desc")`.
   Compare rates per item: `run_report("Supplier Quotation Comparison", {"company": CO, "from_date": D1, "to_date": D2})`
   — add `"item_code": I` or `"request_for_quotation": RFQ` to narrow it.
2. Present per item: supplier, rate, lead time, validity; mark the cheapest and the fastest. Don't pick for the user
   when price and delivery conflict — show the trade-off.

## Draft a purchase order

1. Resolve supplier (`find_party("Supplier", ...)`) and items (`match_items([...], supplier=S)`); check stock with
   `get_stock_levels(item_code=I)` when the reason is replenishment.
2. If from a quotation, copy its supplier, items, qty and rates. Required: `supplier`, `schedule_date`, `items`
   with `item_code`, `qty`, `rate`, `schedule_date` (check with `get_doctype_schema("Purchase Order")` if unsure).
3. Show a summary, get a yes, then `create_draft("Purchase Order", {...})` and share the link. Never submit unless
   the user asks and confirms (`submit_doc(..., confirm=true)`).

## Match PO → receipt → invoice

`three_way_match(supplier=S, from_date=D1, to_date=D2)` (or `purchase_order=PO`). Explain each flag in plain words:

| Flag | Meaning | Usual action |
|---|---|---|
| not_received / partially_received | goods not (fully) in | chase supplier |
| over_received | more received than ordered | check receipt / return |
| received_not_billed | received, no supplier invoice yet | ask supplier for invoice |
| billed_more_than_received | invoiced before or beyond receipt | hold payment |
| over_billed | invoiced above order value | dispute |
| rate_mismatch | invoice rate ≠ PO rate | check price change |

Use `tolerance_pct` (e.g. 2) when the user accepts small differences.

## Payables ageing / what we owe

`run_report("Accounts Payable", {"company": CO, "report_date": TODAY, "ageing_based_on": "Due Date"})`, or the
summary version `run_report("Accounts Payable Summary", {...})`. Quick list of overdue bills:
`list_docs("Purchase Invoice", {"status": "Overdue", "docstatus": 1}, ["name", "supplier", "due_date", "outstanding_amount"], "due_date asc", 200)`.
Group by supplier, show totals and the oldest due date. Never create Payment Entries; suggest them only.
