"""Procure-to-pay tools: purchase order ↔ receipt ↔ invoice matching."""

from __future__ import annotations

import json

from .tools import _clamp, _connect, _tool

__all__ = ["three_way_match"]

MAX_ORDERS = 200
_CHUNK = 200


def match_flags(line: dict, invoiced: list[dict], tolerance_pct: float) -> list[str]:
    """Problems on one PO line. line: qty, received_qty, rate, amount, billed_amt; invoiced: PI item rows (rate)."""
    tol = max(float(tolerance_pct or 0), 0.0) / 100
    qty = float(line.get("qty") or 0)
    received = float(line.get("received_qty") or 0)
    rate = float(line.get("rate") or 0)
    amount = float(line.get("amount") or qty * rate)
    billed = float(line.get("billed_amt") or 0)
    tol_qty = qty * tol
    tol_amt = max(1.0, amount * tol)

    flags = []
    if received <= 0:
        flags.append("not_received")
    elif received < qty - tol_qty - 1e-9:
        flags.append("partially_received")
    elif received > qty + tol_qty + 1e-9:
        flags.append("over_received")

    received_value = received * rate
    if billed < received_value - tol_amt:
        flags.append("received_not_billed")
    if billed > received_value + tol_amt:
        flags.append("billed_more_than_received")
    if billed > amount + tol_amt:
        flags.append("over_billed")
    rate_tol = max(0.01, rate * tol)
    if any(abs(float(i.get("rate") or 0) - rate) > rate_tol for i in invoiced):
        flags.append("rate_mismatch")
    return flags


@_tool
def three_way_match(
    purchase_order: str | None = None,
    supplier: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    tolerance_pct: float = 0.0,
    only_issues: bool = True,
    limit: int = 50,
) -> dict:
    """Match submitted Purchase Orders to receipts and supplier invoices, line by line.

    Flags per PO line: not_received, partially_received, over_received, received_not_billed,
    billed_more_than_received, over_billed, rate_mismatch (invoice rate ≠ PO rate).
    tolerance_pct allows small differences (e.g. 2 = 2%). Dates filter the PO transaction_date.
    Example: three_way_match(supplier="Alpha Traders", from_date="2026-10-01", to_date="2026-10-31").
    """
    limit = min(_clamp(limit, 50), MAX_ORDERS)
    filters: list[list] = [["docstatus", "=", 1]]
    if purchase_order:
        filters.append(["name", "=", purchase_order])
    if supplier:
        filters.append(["supplier", "=", supplier])
    if from_date and to_date:
        filters.append(["transaction_date", "between", [from_date, to_date]])
    elif from_date:
        filters.append(["transaction_date", ">=", from_date])
    elif to_date:
        filters.append(["transaction_date", "<=", to_date])

    fields = ["name", "supplier", "transaction_date", "status", "per_received", "per_billed", "grand_total"]
    _, client = _connect()
    with client:
        pos = client.get(client.resource_path("Purchase Order"), params={
            "filters": json.dumps(filters), "fields": json.dumps(fields),
            "order_by": "transaction_date desc", "limit_page_length": limit + 1,
        }).get("data", [])
        truncated = len(pos) > limit
        pos = pos[:limit]
        lines_by_po = {po["name"]: client.get(client.resource_path("Purchase Order", po["name"]))
                       .get("data", {}).get("items", []) for po in pos}

        line_names = [ln["name"] for lines in lines_by_po.values() for ln in lines if ln.get("name")]
        invoiced: dict[str, list[dict]] = {}
        for i in range(0, len(line_names), _CHUNK):
            chunk = line_names[i:i + _CHUNK]
            rows = client.get(client.resource_path("Purchase Invoice Item"), params={
                "parent": "Purchase Invoice",
                "filters": json.dumps([["po_detail", "in", chunk], ["docstatus", "=", 1]]),
                "fields": json.dumps(["parent", "po_detail", "qty", "rate", "amount"]),
                "limit_page_length": 0,
            }).get("data", [])
            for r in rows:
                invoiced.setdefault(r["po_detail"], []).append(r)

    orders, flag_counts = [], {}
    for po in pos:
        out_lines = []
        for ln in lines_by_po[po["name"]]:
            inv = invoiced.get(ln.get("name"), [])
            flags = match_flags(ln, inv, tolerance_pct)
            for f in flags:
                flag_counts[f] = flag_counts.get(f, 0) + 1
            if flags or not only_issues:
                out_lines.append({
                    "item_code": ln.get("item_code"), "ordered_qty": ln.get("qty"),
                    "received_qty": ln.get("received_qty"), "rate": ln.get("rate"), "amount": ln.get("amount"),
                    "billed_amt": ln.get("billed_amt"), "invoices": sorted({r["parent"] for r in inv}),
                    "flags": flags,
                })
        has_issues = any(line["flags"] for line in out_lines)
        if has_issues or not only_issues:
            orders.append({**po, "lines": out_lines, "has_issues": has_issues})

    return {
        "orders": orders,
        "summary": {
            "orders_checked": len(pos),
            "orders_with_issues": sum(1 for o in orders if o["has_issues"]),
            "flag_counts": flag_counts,
        },
        "truncated": truncated,
    }
