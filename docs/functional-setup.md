# Functional setup

For the ERP lead and business users: the ERPNext master data the plugin relies on, and how to run each workflow.
Technical install is in [configuration-setup.md](configuration-setup.md).

## 1. ERPNext master data

The plugin answers only from data that exists in ERPNext. Claude resolves real names (customer, item, warehouse)
before creating anything and never invents them.

| # | Master / setting | Where in ERPNext | Needed by | Minimum to set |
|---|---|---|---|---|
| 1 | Company | Setup → Company | All reports (AR, Stock Balance) | Name, default currency (INR), abbreviation; chart of accounts |
| 2 | Fiscal Year | Accounting → Fiscal Year | Reports with date ranges | Current fiscal year (Apr–Mar for India) |
| 3 | Warehouses | Stock → Warehouse | Stock skill, Sales Order delivery | Stores, Finished Goods, etc.; names users will say aloud |
| 4 | Item Groups & UOMs | Stock → Item Group / UOM | Item look-ups | Groups that match how users describe products |
| 5 | Items | Stock → Item | Stock, order-to-cash | Item code, name, stock UOM, "Maintain Stock" on, default warehouse |
| 6 | Reorder levels | Item → Inventory → Auto Re-order table | "What should I reorder?" | Per warehouse: re-order level and qty |
| 7 | Opening stock | Stock Reconciliation / Stock Entry (Material Receipt) | "Available stock today" | Opening quantities and valuation rates |
| 8 | Price Lists & Item Prices | Selling → Price List / Item Price | Quotes and orders | Standard Selling rate per item |
| 9 | Customer Groups & Territories | Selling → Customer Group / Territory | Customer creation | At least one default of each |
| 10 | Customers + Contacts | Selling → Customer; Contact linked to customer | Orders, AR reminders | Name, payment terms; contact with email |
| 11 | Payment Terms | Accounting → Payment Terms Template | Due dates → ageing | e.g. Net 30 assigned to customers |
| 12 | Taxes | Accounting → Sales Taxes and Charges Template | Correct totals | GST template set as default |
| 13 | Selling / Stock Settings | Selling Settings, Stock Settings | Order behaviour | Default price list, naming, negative stock policy |
| 14 | API user roles | Setup → Users | Every read and write | Roles matching the skills you use |

On a fresh site, the ERPNext home page checklist (Accounts, Selling, Buying, Stock…) walks through most of 1–12.
Quick check: ask *"how many items, customers and warehouses do we have?"* — non-zero counts mean the plugin sees them.

## 2. Workflows

Ask in plain words; the matching skill loads automatically. Every write is a **draft** with a link to review in ERPNext.

| Skill | Ask it like | What Claude does | Writes | Roles |
|---|---|---|---|---|
| Stock | "available stock today", "how many ITEM-001 in Stores?", "what should we reorder?" | Reads stock levels and Stock Balance / Projected Qty / Ledger reports | Draft Material Request (on request) | Stock User |
| Order-to-Cash | "status of Acme's orders", "draft order for Acme, 10 × ITEM-001, delivery next Friday" | Resolves customer, items, price, stock; summarises; creates draft | Draft Quotation / Sales Order / Sales Invoice | Sales User (+ Accounts User) |
| Receivables & Collections | "who owes more than 50k?", "draft reminders for the top 3" | Runs Accounts Receivable by due date; groups by customer; drafts emails | None — emails are text only | Accounts User |
| Customization | "what fields does Sales Order have?", "add a Delivery Slot field" | Reads schema and existing customizations; proposes a plan | Custom Field, Property Setter (after a yes) | System Manager (dev/test) |

### Order-to-cash

1. Ask for the document in plain words (customer, items, quantities, delivery date).
2. Claude looks up the real customer and item codes, prices and stock, then shows a summary.
3. Confirm; Claude creates the **draft** and returns its ERPNext link.
4. Changes: ask again — Claude updates the draft (drafts only).
5. Submit in ERPNext, or ask Claude if submit is enabled for the profile — it re-summarises and needs an explicit yes.

### Receivables

1. Ask *"who owes us money?"* — table of customer, invoices, total outstanding, oldest due date, days overdue, ageing bucket.
2. Pick customers; Claude drafts polite reminder emails with invoice numbers, due dates and amounts.
3. Send them yourself. Claude never creates Payment Entries.

### Customization

1. Ask what a form contains or what you want to add.
2. Claude proposes fieldname, label, type, options and position; confirm.
3. The Custom Field is live on save — reload the form. Server/Client Scripts, Reports and Print Formats are blocked:
   Claude writes the code and tells you where to paste it.

### Tips

- Name the company, warehouse or date range when you know it; otherwise Claude asks.
- "Available" = on hand minus reserved; ask for "projected" to include incoming.
- Dates are `YYYY-MM-DD`; amounts carry currency.
- Several sites: *"use the prod profile"*.

## 3. Go-live checklist

- [ ] Dedicated API user with only the needed roles
- [ ] No System Manager role on production
- [ ] Masters 1–14 completed and spot-checked
- [ ] Profiles for each site; `/erp-doctor` passes on each
- [ ] `allow_submit` off on production unless clearly needed
- [ ] Secrets entered via terminal setup on shared machines
- [ ] Test prompts run: stock today, a draft Sales Order, AR summary
- [ ] Team briefed: Claude drafts, people submit
- [ ] Audit log (`~/.config/erpnext-accelerator/audit.log`) review owner agreed
- [ ] Key rotation plan: regenerate keys in ERPNext, re-run setup
