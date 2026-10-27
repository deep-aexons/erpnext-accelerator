# Testing

## Unit tests (no ERPNext needed)

```bash
cd server
uv run --extra test pytest -q
```

HTTP is mocked with `respx`. Covered: config resolution and file permissions, retry/backoff (429, 5xx,
`Retry-After`, network errors, no retry of writes on 5xx), Frappe error parsing (HTML, tracebacks, auth hints),
every safety rule, every tool's request and result shape, the setup/doctor CLI, and the plugin files
(manifests, skills, commands, docs referencing real tools).

## Plugin validation (on your machine)

```bash
claude plugin validate /Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator
```

## MCP Inspector (optional)

```bash
npx @modelcontextprotocol/inspector uv run --directory server erpnext-mcp
```

Call `ping` and `get_stock_levels` from the Inspector UI.

## Live test against your local ERPNext

1. `cd frappe-bench && bench start`, open `http://localhost:8000`, log in.
2. Make sure there is sample data: at least one Company, Customer, Item with stock, Sales Invoice.
3. Create the API user and keys ([setup.md §1](setup.md#1-create-an-api-user-in-erpnext)).
4. Health check from a terminal:
   ```bash
   uv run --directory server erpnext-mcp setup     # profile dev, url http://localhost:8000, site erpnext.localhost
   uv run --directory server erpnext-mcp doctor    # every check "ok": true
   ```
5. In Claude Code (plugin installed), try each prompt and tick the expected result:

| # | Prompt | Expected |
|---|---|---|
| 1 | give me available stock for today | Table from `get_stock_levels`; numbers match ERPNext Stock Balance |
| 2 | how many units of `<item>` in `<warehouse>`? | Single figure, correct |
| 3 | which customers owe money? | Accounts Receivable based list with totals |
| 4 | make a draft sales order for `<customer>`: 2 × `<item>` | Claude confirms first, then a draft SO exists in ERPNext (docstatus 0) |
| 5 | submit it | Refused: submit disabled for profile |
| 6 | run `erpnext-mcp setup` in a terminal, answer `y` to allow submit, then ask "submit it" | Claude asks for confirmation, then submits |
| 7 | delete that order | Claude says it cannot delete |
| 8 | create a new user / a client script | Refused by blocklist |
| 9 | stop the bench, ask anything | "Cannot reach ERPNext…" and a hint to run /erp-doctor |

6. Check `~/.config/erpnext-accelerator/audit.log` shows the write attempts from 4–8.
