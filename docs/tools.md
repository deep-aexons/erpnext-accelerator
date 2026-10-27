# Tool reference

The MCP server is named `erpnext`. In Claude the tools appear as `mcp__…erpnext__<tool>`.
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

## Retry behaviour

| Request | Retried on | Not retried |
|---|---|---|
| Reads (GET) | 429, 500, 502, 503, 504, network errors | other 4xx |
| Writes (POST/PUT) | 429, connection refused/failed | 5xx, timeouts after sending (avoids duplicates) |

A write that times out after it was sent returns an `UncertainWrite` error: "it may have been applied —
check with list_docs before trying again".

Backoff: `random() × min(8 s, 0.5 s × 2^attempt)`, or the `Retry-After` header (max 60 s). Default 3 retries.
