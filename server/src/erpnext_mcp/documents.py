"""Document-to-draft helpers: find the right party and items for lines read from a PDF or photo."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from mcp.server.fastmcp.exceptions import ToolError

from .client import ERPNextClient
from .errors import ERPNextError
from .tools import _chunks, _connect, _tool

__all__ = ["find_party", "match_items"]

MAX_LINES = 100
CONFIDENT_SCORE = 0.85
_EXACT = ("item_code", "barcode", "supplier_part_no")
_ABBREV = {"pvt": "private", "ltd": "limited", "co": "company", "corp": "corporation", "inc": "incorporated",
           "intl": "international", "mfg": "manufacturing", "&": "and"}
_NOISE = {"private", "limited", "company", "the", "and", "llp", "incorporated", "corporation"}
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PARTY_NAME_FIELD = {"Customer": "customer_name", "Supplier": "supplier_name"}


def _normalise(text: str) -> str:
    words = re.findall(r"[a-z0-9&]+", (text or "").lower())
    return " ".join(_ABBREV.get(w, w) for w in words)


def score(a: str, b: str) -> float:
    """Similarity 0..1 of two names after lowercasing and expanding common abbreviations."""
    na, nb = _normalise(a), _normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return round(SequenceMatcher(None, na, nb).ratio(), 3)


def looks_like_gstin(value: str) -> bool:
    return bool(_GSTIN_RE.match((value or "").strip().upper()))


def _search_tokens(text: str, count: int = 2) -> list[str]:
    words = [w for w in _normalise(text).split() if len(w) >= 3 and w not in _NOISE]
    return sorted(set(words), key=len, reverse=True)[:count] or ([text.strip()] if text.strip() else [])


def _get(client: ERPNextClient, doctype: str, filters, fields, limit=20, parent: str | None = None) -> list[dict]:
    params = {"filters": json.dumps(filters), "fields": json.dumps(fields), "limit_page_length": limit}
    if parent:
        params["parent"] = parent
    return client.get(client.resource_path(doctype), params=params).get("data", [])


@_tool
def find_party(party_type: str, query: str, limit: int = 5) -> list[dict]:
    """Find a Customer or Supplier by name or GSTIN, best match first.

    Use when a document names a party: find_party("Supplier", "Alpha Traders Pvt Ltd") or
    find_party("Customer", "27AAACA1111A1ZS"). Returns [{name, display_name, tax_id, score, match_by}].
    Ask the user when the top score is below 0.85 or two candidates are close.
    """
    if party_type not in _PARTY_NAME_FIELD:
        raise ToolError("party_type must be 'Customer' or 'Supplier'.")
    name_field = _PARTY_NAME_FIELD[party_type]
    fields = ["name", name_field, "tax_id"]
    limit = max(1, min(int(limit or 5), 20))
    query = (query or "").strip()
    if not query:
        raise ToolError("query is empty.")

    _, client = _connect()
    with client:
        if looks_like_gstin(query):
            gstin = query.upper()
            rows: list[dict] = []
            if _has_india_compliance(client):  # India Compliance keeps GSTIN in `gstin` (tax_id holds the PAN)
                rows = _get(client, party_type, [["gstin", "=", gstin]], [*fields, "gstin"])
            if not rows:
                rows = _get(client, party_type, [["tax_id", "=", gstin]], fields)
            return [{"name": r["name"], "display_name": r.get(name_field), "tax_id": r.get("tax_id"),
                     "score": 1.0, "match_by": "gstin"} for r in rows][:limit]
        found: dict[str, dict] = {}
        for token in _search_tokens(query):
            for r in _get(client, party_type, [[name_field, "like", f"%{token}%"], ["disabled", "=", 0]], fields):
                found[r["name"]] = r

    ranked = sorted(
        ({"name": r["name"], "display_name": r.get(name_field), "tax_id": r.get("tax_id"),
          "score": max(score(query, r.get(name_field) or ""), score(query, r["name"])), "match_by": "name"}
         for r in found.values()),
        key=lambda c: c["score"], reverse=True)
    return ranked[:limit]


def _has_india_compliance(client: ERPNextClient) -> bool:
    try:
        return "india_compliance" in (client.call_method("frappe.utils.change_log.get_versions") or {})
    except ERPNextError:
        return False


def _code_like(value) -> str | None:
    text = str(value or "").strip()
    return text if text and len(text) <= 40 and not re.search(r"\s", text) else None


def _get_in(client: ERPNextClient, doctype: str, field: str, values, fields, extra=(), parent=None) -> list[dict]:
    out: list[dict] = []
    for chunk in _chunks(sorted(set(values))):
        out += _get(client, doctype, [*extra, [field, "in", chunk]], fields, limit=len(chunk) * 2, parent=parent)
    return out


def _as_line(raw) -> dict:
    if isinstance(raw, str):
        return {"description": raw}
    if isinstance(raw, dict):
        return dict(raw)
    raise ToolError("Each line must be a string or an object with 'description'.")


@_tool
def match_items(lines: list, supplier: str | None = None, limit_per_line: int = 3) -> dict:
    """Match document lines to ERPNext Items.

    lines: strings, or objects {description, code?, supplier_part_no?, hsn?} (max 100).
    Tries in order: exact item code → barcode → supplier part number (when supplier is given) → name similarity.
    Returns per line {input, candidates, best, confident}. Ask the user about lines that are not confident;
    never invent item codes.
    """
    if not lines:
        return {"lines": [], "unmatched": 0}
    if len(lines) > MAX_LINES:
        raise ToolError(f"Too many lines ({len(lines)}); send at most {MAX_LINES} per call.")
    items = [_as_line(x) for x in lines]
    per_line = max(1, min(int(limit_per_line or 3), 10))
    hits: list[dict | None] = [None] * len(items)
    item_fields = ["name", "item_name", "stock_uom"]

    _, client = _connect()
    with client:
        codes = {i: _code_like(ln.get("code")) or _code_like(ln.get("description")) for i, ln in enumerate(items)}
        codes = {i: c for i, c in codes.items() if c}
        exact = {r["name"]: r for r in _get_in(client, "Item", "name", codes.values(), item_fields)}
        for i, code in codes.items():
            if code in exact:
                hits[i] = {"item": code, "match_by": "item_code"}

        barcodes = {i: c for i, c in codes.items() if hits[i] is None and c.isdigit() and 8 <= len(c) <= 14}
        if barcodes:
            rows = _get_in(client, "Item Barcode", "barcode", barcodes.values(), ["parent", "barcode"], parent="Item")
            by_code = {r["barcode"]: r["parent"] for r in rows}
            for i, c in barcodes.items():
                if c in by_code:
                    hits[i] = {"item": by_code[c], "match_by": "barcode"}

        if supplier:
            parts = {i: str(ln.get("supplier_part_no") or ln.get("code") or "").strip()
                     for i, ln in enumerate(items) if hits[i] is None}
            parts = {i: p for i, p in parts.items() if p}
            if parts:
                rows = _get_in(client, "Item Supplier", "supplier_part_no", parts.values(),
                               ["parent", "supplier_part_no"], extra=[["supplier", "=", supplier]], parent="Item")
                by_part = {r["supplier_part_no"]: r["parent"] for r in rows}
                for i, p in parts.items():
                    if p in by_part:
                        hits[i] = {"item": by_part[p], "match_by": "supplier_part_no"}

        need = {h["item"] for h in hits if h and h["item"] not in exact}
        if need:
            exact.update({r["name"]: r for r in _get_in(client, "Item", "name", need, item_fields)})

        fuzzy: dict[int, list[dict]] = {}
        for i, ln in enumerate(items):
            if hits[i] is not None:
                continue
            desc = str(ln.get("description") or ln.get("code") or "")
            found: dict[str, dict] = {}
            for token in _search_tokens(desc):
                for r in _get(client, "Item", [["item_name", "like", f"%{token}%"], ["disabled", "=", 0]], item_fields):
                    found[r["name"]] = r
            fuzzy[i] = sorted(
                ({"item_code": r["name"], "item_name": r.get("item_name"), "stock_uom": r.get("stock_uom"),
                  "score": max(score(desc, r.get("item_name") or ""), score(desc, r["name"])), "match_by": "name"}
                 for r in found.values()),
                key=lambda c: c["score"], reverse=True)[:per_line]

    out = []
    for i, ln in enumerate(items):
        if hits[i] is not None:
            row = exact.get(hits[i]["item"], {"name": hits[i]["item"]})
            cands = [{"item_code": row["name"], "item_name": row.get("item_name"), "stock_uom": row.get("stock_uom"),
                      "score": 1.0, "match_by": hits[i]["match_by"]}]
        else:
            cands = fuzzy.get(i, [])
        best = cands[0] if cands else None
        confident = bool(best and (best["match_by"] in _EXACT or best["score"] >= CONFIDENT_SCORE))
        out.append({"input": ln, "candidates": cands, "best": best, "confident": confident})
    return {"lines": out, "unmatched": sum(1 for x in out if not x["candidates"])}
