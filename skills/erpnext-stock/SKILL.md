---
name: erpnext-stock
description: Use when the user asks about stock or inventory in ERPNext - available stock today, how many units of an item are on hand, stock per warehouse, low stock, what to reorder, stock value or stock movement over a period.
---

# ERPNext stock questions

## Current stock ("available stock today", "how many X do we have")

1. Call `get_stock_levels(item_code, warehouse)`. Leave both empty for everything (non-zero only).
   - If the user names an item loosely ("blue chairs"), first find the code:
     `list_docs("Item", [["item_name", "like", "%blue chair%"]], ["name", "item_name", "stock_uom"])`.
   - If they name a warehouse loosely, find it: `list_docs("Warehouse", [["name", "like", "%stores%"]])`.
2. Present a table: Item, Warehouse, On hand (`actual_qty`), Reserved, Available (= actual − reserved),
   Projected. Sum per item when several warehouses. Say if results were truncated.
3. "Available" means on hand minus reserved unless the user means projected; state the definition you used.

## Low stock / what to reorder

1. `get_stock_levels()` for current quantities.
2. Reorder levels live in the Item child table `Item Reorder`:
   `get_doc("Item", item_code)` → `reorder_levels` (warehouse, warehouse_reorder_level, warehouse_reorder_qty).
   For many items, prefer `run_report("Stock Projected Qty", {"company": ...})` which includes re-order level.
3. List items where projected qty is below the reorder level, with suggested order qty.
   Offer to create a draft Material Request with `create_draft` (never submit without explicit yes).

## Stock over a period / stock value

Use `run_report("Stock Balance", {"company": ..., "from_date": ..., "to_date": ...})`.
For movements of one item use `run_report("Stock Ledger", {...})`. If a required filter is missing,
ERPNext says so — add it (company: `list_docs("Company")`) and retry once.

## Rules

- Dates: "today" is the current date; use ISO `YYYY-MM-DD`.
- Never guess quantities. If a tool fails, show the one-line error.
