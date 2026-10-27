---
name: erpnext-order-to-cash
description: Use when the user asks about customers, quotations, sales orders, deliveries or sales invoices in ERPNext, wants the status of an order, or asks to create or change a quotation, sales order or invoice (for example "make an order for Acme, 10 units of ITEM-001").
---

# ERPNext order to cash

## Look-ups

- Order status: `list_docs("Sales Order", {"customer": C}, ["name", "transaction_date", "status", "grand_total", "per_delivered", "per_billed"], "transaction_date desc")`.
- One document in full: `get_doc("Sales Order", name)`.
- Deliveries: `list_docs("Delivery Note", {"customer": C, "docstatus": 1}, [...])`.
- Invoices: `list_docs("Sales Invoice", {"customer": C}, ["name", "posting_date", "status", "grand_total", "outstanding_amount"])`.
- Counts ("how many open orders"): `count_docs("Sales Order", {"status": ["in", ["To Deliver and Bill", "To Bill", "To Deliver"]]})`.

## Creating a document (quotation, sales order, invoice)

1. Resolve real names first — never invent them:
   - customer: `list_docs("Customer", [["customer_name", "like", "%acme%"]], ["name", "customer_name"])`
   - items: `list_docs("Item", [["item_name", "like", "%...%"]], ["name", "item_name", "stock_uom"])`
   - price if needed: `list_docs("Item Price", {"item_code": I, "selling": 1}, ["price_list", "price_list_rate"])`
   - stock if relevant: `get_stock_levels(item_code=I)`
2. If unsure of fields, `get_doctype_schema("Sales Order")`. Sales Order needs `customer`, `delivery_date`,
   and `items` rows with `item_code`, `qty` (and usually `delivery_date` per row).
3. Show a short summary (customer, items, qty, rate, delivery date) and ask the user to confirm.
4. `create_draft("Sales Order", {...})`. Share the returned `url` so they can review it in ERPNext.
5. To change it: `update_draft(...)` (drafts only).
6. Submit only if the user explicitly asks: summarise again, get a clear yes, then
   `submit_doc("Sales Order", name, confirm=true)`. If submit is disabled for the profile, tell the user to submit
   in ERPNext, or to enable it themselves in a terminal with `erpnext-mcp setup`. Never try to enable it yourself.

## Rules

- Never submit, cancel or post payments on your own initiative.
- Amounts: show currency. Dates: ISO `YYYY-MM-DD`.
