# ERPNext Accelerator — Claude Plugin — Design Spec

- Date: 2026-10-27
- Author: Deep (Aexons) with Claude
- Status: Draft for review
- Scope: Approach 2 of the ERPNext agentic-AI R&D (Claude accelerator plugin). Approach 1 (customer-facing agent inside ERPNext) is out of scope.

## 1. Goal

A plug-and-play Claude plugin that lets internal users work with any ERPNext site in plain language
("give me available stocks for today", "which customers owe more than 50k?", "make a draft order for Acme").

Primary users: business ops staff (sales, accounts, stock). Secondary: ERPNext developers (schema, customization).

### Success criteria

1. Install = add marketplace + install plugin + run `/erp-connect` once. No Python setup beyond having `uv`.
2. Daily use needs no slash commands. Natural language triggers skills and tools.
3. Works against ERPNext v14+ (tested target: local Frappe 17.0.0-dev, site `erpnext.localhost:8000`).
4. Claude can never delete records, never edit submitted documents, and can only submit/cancel when the
   site profile allows it AND the call carries `confirm=true`.
5. Transient failures (429, 5xx, network) are retried with backoff; errors reach Claude as short readable text.
6. Unit tests pass without a live ERPNext. A documented manual test runs against the live site.

### Non-goals (this build)

- Remote/HTTP MCP endpoint or a Frappe app (future: team-wide connector with OAuth).
- Customer-facing chat (Approach 1).
- Delete, file upload, bulk import, payment posting.
- Any LLM calls from the plugin itself (Claude is the model; no fallback logic needed here).

## 2. Architecture

```
Claude (Code / Desktop)
   │  natural language
   ▼
Skills (SKILL.md, auto-triggered by description)  +  setup commands (/erp-connect, /erp-doctor)
   │  tool calls (MCP, stdio)
   ▼
erpnext-mcp server (Python, launched by uv from the plugin)
   ├── tools.py    MCP tool definitions (thin)
   ├── guards.py   safety rules (blocklist, docstatus, submit gate, audit)
   ├── client.py   ERPNext HTTP client (auth, Host header, timeouts, retries, error mapping)
   └── config.py   profile resolution (env vars > config file)
   │  HTTPS/HTTP, token auth
   ▼
ERPNext site  (/api/resource/*, /api/method/*)
```

Each unit has one job and is tested alone. `tools.py` never calls httpx directly; it goes through `client.py`
and `guards.py`.

## 3. Repository layout

Location: `/Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator/` (sibling of `erpnext/` and `frappe-bench/`;
neither is modified).

```
erpnext-accelerator/
├── .claude-plugin/
│   ├── plugin.json           # plugin manifest
│   └── marketplace.json      # lets this repo be added as a marketplace
├── .mcp.json                 # starts the server via uv
├── server/
│   ├── pyproject.toml        # package erpnext_mcp, script `erpnext-mcp`
│   ├── src/erpnext_mcp/
│   │   ├── __init__.py
│   │   ├── __main__.py       # `python -m erpnext_mcp` (server) / `setup` / `doctor` CLI
│   │   ├── config.py
│   │   ├── client.py
│   │   ├── guards.py
│   │   ├── errors.py
│   │   └── tools.py
│   └── tests/
├── skills/
│   ├── erpnext-stock/SKILL.md
│   ├── erpnext-order-to-cash/SKILL.md
│   ├── erpnext-ar-collections/SKILL.md
│   └── erpnext-customization/SKILL.md
├── commands/
│   ├── erp-connect.md
│   └── erp-doctor.md
├── docs/
│   ├── setup.md  tools.md  architecture.md  testing.md  security.md
│   └── superpowers/specs/2026-10-27-erpnext-accelerator-design.md
├── README.md
└── CHANGELOG.md
```

## 4. Configuration

Resolution order (first hit wins, per key):

1. Environment variables: `ERPNEXT_URL`, `ERPNEXT_SITE`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`,
   `ERPNEXT_PROFILE`, `ERPNEXT_ALLOW_SUBMIT`, `ERPNEXT_TIMEOUT`, `ERPNEXT_MAX_RETRIES`.
2. Config file `~/.config/erpnext-accelerator/config.json` (override dir with `ERPNEXT_ACCELERATOR_HOME`),
   written with mode 0600:

```json
{
  "default_profile": "dev",
  "profiles": {
    "dev": {
      "url": "http://localhost:8000",
      "site": "erpnext.localhost",
      "api_key": "xxxx",
      "api_secret": "xxxx",
      "allow_submit": false
    }
  }
}
```

- `site` is optional; when set it is sent as the `Host` header so multi-site benches resolve correctly.
- Config is read at call time (not cached forever), so `/erp-connect` takes effect without restarting.
- No profile configured → every tool returns: "ERPNext not connected. Run /erp-connect."

### One-time setup paths

- `/erp-connect` (in Claude): asks for profile name, URL, site, key, secret; calls the `connect` tool, which
  pings the site with those credentials and saves only on success. Note: values typed into chat are in the
  chat transcript; docs recommend the terminal path for shared machines.
- Terminal: `uv run --directory <plugin>/server erpnext-mcp setup` (prompts, secret hidden via getpass).
- `/erp-doctor`: calls `diagnose`; only needed when something breaks.

## 5. Tools

All tools return JSON-serialisable data. List results are capped (`limit` default 20, max 500) and include
`truncated: true` when more rows exist.

| Tool | Kind | Purpose |
|---|---|---|
| `connect(profile, url, api_key, api_secret, site=None, make_default=True)` | setup | Validate and save a profile |
| `use_profile(profile)` | setup | Switch default profile |
| `diagnose(profile=None)` | setup | Reachability, auth, versions, read-permission probe on key DocTypes |
| `ping()` | read | Logged-in user + site URL |
| `list_docs(doctype, filters=None, fields=None, order_by=None, limit=20)` | read | Wraps `GET /api/resource/{doctype}` |
| `get_doc(doctype, name)` | read | Wraps `GET /api/resource/{doctype}/{name}` |
| `count_docs(doctype, filters=None)` | read | `frappe.client.get_count` |
| `get_doctype_schema(doctype)` | read | Compact field list from `frappe.desk.form.load.getdoctype` (incl. child tables) |
| `run_report(report_name, filters, limit=100)` | read | `frappe.desk.query_report.run`; returns columns + rows |
| `get_stock_levels(item_code=None, warehouse=None, include_zero=False, limit=100)` | read | `Bin`: actual, reserved, ordered, projected qty |
| `create_draft(doctype, data)` | write | `POST /api/resource/{doctype}`, forces `docstatus=0` |
| `update_draft(doctype, name, data)` | write | `PUT`, only if current `docstatus == 0` |
| `submit_doc(doctype, name, confirm=False)` | gated | `frappe.client.submit`; needs `confirm=true` and `allow_submit` |
| `cancel_doc(doctype, name, confirm=False)` | gated | `frappe.client.cancel`; same gate |

No delete tool exists.

## 6. Safety rules (`guards.py`)

1. **Blocklist** — writes refused for: User, Role, Role Profile, Has Role, DocType, Custom DocPerm, DocPerm,
   System Settings, OAuth Client, OAuth Bearer Token, Server Script, Scheduled Job Type, User Permission,
   Module Def, Website Settings. Reads are governed by ERPNext permissions of the API user. List is in code and documented.
2. **Draft only** — `create_draft` forces `docstatus=0`. A caller-supplied `name` is passed through (some DocTypes use manual naming).
   `update_draft` fetches the doc first and refuses if `docstatus != 0`; also refuses to change `docstatus`.
3. **Submit gate** — `submit_doc`/`cancel_doc` refuse unless `confirm is True` and the profile's
   `allow_submit` is true. Refusal message tells Claude to show the user a summary and ask first.
4. **Audit log** — every write/gated attempt (allowed or refused) appended as JSONL to
   `~/.config/erpnext-accelerator/audit.log`: timestamp, profile, tool, doctype, name, outcome. No secrets,
   no full payloads.
5. **Least privilege** — docs instruct creating a dedicated API user with only needed roles. ERPNext
   permissions remain the final authority.

## 7. HTTP client (`client.py`)

- `httpx.Client`, auth header `Authorization: token <key>:<secret>`, optional `Host: <site>`.
- Timeouts: connect 5 s, total default 30 s (`ERPNEXT_TIMEOUT`).
- Retries (default 3, `ERPNEXT_MAX_RETRIES`), exponential backoff base 0.5 s, cap 8 s, full jitter,
  honours `Retry-After`.
  - GET / read methods: retry on 429, 500, 502, 503, 504 and network errors.
  - POST/PUT (writes): retry only on 429 and connect errors (request never reached the server) to avoid
    duplicate documents.
  - Never retry 400/401/403/404/409/417.
- Error mapping (`errors.py`): parse Frappe `exc_type`, `exception`, `_server_messages`, `message`; return
  `ERPNextError(status, kind, message)` with a one-line message. 401/403 → hint "check key / role, run /erp-doctor".
  Stack traces never returned to Claude.
- Logs go to stderr (stdio MCP keeps stdout for protocol). Never log secrets.

## 8. Skills and commands

Skill descriptions are written in everyday phrasing so plain-language requests trigger them.

- `erpnext-stock` — "available stock", "inventory", "how many X in warehouse", reorder suggestions.
  Uses `get_stock_levels`, `run_report("Stock Balance")`, Item reorder levels.
- `erpnext-order-to-cash` — quotations, sales orders, delivery, invoice status; creating draft orders
  (look up customer/items/prices first, confirm with user, create draft, never auto-submit).
- `erpnext-ar-collections` — outstanding/overdue invoices, who owes what, reminder drafts. Uses
  `run_report("Accounts Receivable")`.
- `erpnext-customization` — schema questions, custom fields, property setters, server/client script
  drafts. Reads schema via tool; writes customization as draft guidance/code, applies `Custom Field`
  only via `create_draft` when user confirms (Custom Field is not blocklisted; Server Script is).

Commands (setup only): `/erp-connect`, `/erp-doctor`.

## 9. Testing

- Unit (pytest + respx, no live site): config resolution, retry/backoff (429 then 200, 503 exhaustion,
  Retry-After honoured, no retry on 400/401, write not retried on 500), error mapping, every guard rule,
  each tool's request shape and response shaping, audit log lines.
- MCP smoke test: start server in-process, list tools, call `ping` against mocked HTTP.
- Manual live test (docs/testing.md): on the Mac, `bench start`, create API user, `/erp-connect`, then
  scripted prompts (stock, AR, draft order, submit refused, submit allowed with flag).

## 10. Documentation deliverables

README (what/why, 5-minute quick start), docs/setup.md (API user + role + keys + install in Claude Code and
Claude Desktop), docs/tools.md (reference), docs/architecture.md, docs/security.md (guards, audit, least
privilege, transcript note), docs/testing.md, CHANGELOG.md.

## 11. Risks and open points

- Plugin `userConfig` prompting varies by Claude version; config file + `/erp-connect` is the portable path.
- Report names/filters differ slightly across ERPNext versions; skills tell Claude to read report filters
  from errors and retry once.
- Claude Desktop needs `uv` on PATH; docs cover the absolute-path fallback.
- Cloud-only Claude surfaces cannot reach `localhost`; local Claude Code/Desktop is required for local sites.
