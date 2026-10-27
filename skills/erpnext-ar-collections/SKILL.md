---
name: erpnext-ar-collections
description: Use when the user asks who owes money, overdue or unpaid invoices, outstanding receivables, customer balances, ageing, or wants payment reminder emails drafted for customers in ERPNext.
---

# ERPNext receivables and collections

## Who owes what

1. Preferred: `run_report("Accounts Receivable", {"company": CO, "report_date": TODAY, "ageing_based_on": "Due Date"})`.
   - Company unknown? `list_docs("Company", fields=["name", "default_currency"])`; if one, use it; else ask.
   - Summary per customer: `run_report("Accounts Receivable Summary", {...same filters...})`.
2. Quick alternative for overdue only:
   `list_docs("Sales Invoice", {"status": "Overdue", "docstatus": 1}, ["name", "customer", "due_date", "outstanding_amount", "currency"], "due_date asc", 200)`.
3. Threshold questions ("owes more than 50k"): group by customer, sum `outstanding_amount`, filter, sort desc.
   Use `count_docs` to check if the list was truncated before summing.

## Presenting

Table: Customer, Invoices, Total outstanding, Oldest due date, Days overdue, Ageing bucket.
Put totals at the bottom. Mention currency.

## Reminder drafts

For each customer the user picks, draft a short, polite email listing invoice numbers, due dates and amounts.
Get contact email if needed: `list_docs("Contact", [["Dynamic Link", "link_name", "=", CUSTOMER]], ["name", "email_id"])`.
Do not send anything — only draft text. Never create Payment Entries; you may suggest them.
