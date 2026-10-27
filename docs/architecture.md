# Architecture

## Context and goals

Let ERPNext users and consultants work with ERP data through Claude in plain language, with zero changes
to the ERPNext installation, safe defaults, and a one-time setup. This is "Approach 2" of the Aexons ERP
agentic-AI R&D; "Approach 1" (a customer-facing agent inside ERPNext) is separate.

## High-level design

```
 You ──plain language──▶ Claude (Code / Desktop)
                            │  skills/*.md  (workflow know-how, auto-loaded by description)
                            │  commands/*.md (/erp-connect, /erp-doctor)
                            ▼  MCP over stdio
                 ┌──────────────────────────────┐
                 │ erpnext-mcp (Python, via uv) │
                 │  tools.py   14 MCP tools     │
                 │  guards.py  safety + audit   │
                 │  client.py  HTTP + retries   │
                 │  config.py  profiles         │
                 │  errors.py  readable errors  │
                 └──────────────┬───────────────┘
                                ▼  HTTP(S), Authorization: token key:secret
                      ERPNext /api/resource/*, /api/method/*
```

## Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Resolve a `Profile` (env vars > `config.json`), save profiles atomically with 0600 | — |
| `errors.py` | `ERPNextError` family; turn Frappe error bodies into one clean line | — |
| `client.py` | `ERPNextClient`: auth + Host headers, timeouts, retry policy, JSON decoding | config, errors |
| `guards.py` | Write blocklist, draft-only rules, submit gate, JSONL audit log | config, errors |
| `tools.py` | MCP tool definitions; shape results for the model | all of the above |
| `__main__.py` | `erpnext-mcp` (server), `setup`, `doctor` CLI | tools |

`tools.py` never uses `httpx` directly, so retries and error mapping are in one place and tested once.

## Data flow: "give me available stock for today"

1. Claude matches the request to the `erpnext-stock` skill description and loads it.
2. The skill tells Claude to call `get_stock_levels()`.
3. `tools.py` loads the profile, builds a client, sends `GET /api/resource/Bin?fields=…&filters=[["actual_qty","!=",0]]`.
4. ERPNext applies the API user's permissions and returns rows; the tool caps and returns them.
5. Claude formats a table (on hand, reserved, available).

## Key decisions

| Decision | Why | Trade-off |
|---|---|---|
| Standalone stdio MCP server over REST | Works with any ERPNext v14+, nothing to install on the site | Per-user API keys; needs local `uv` |
| API key auth (not OAuth) | Simplest one-time setup | Keys must be rotated manually |
| Config file + `/erp-connect` instead of plugin-level prompts | Portable across Claude versions and Desktop | Values typed in chat are in the transcript (terminal setup avoids this) |
| Drafts by default, double-gated submit | Mistakes stay reversible | Users finish submit in ERPNext unless they opt in |
| Generic tools + one specialised (`get_stock_levels`) | Small surface, covers all DocTypes; stock is the most common question | Some workflows need several calls |
| `mcp>=1.10,<2` | v1 FastMCP API is stable; v2 renamed it | Future migration when moving to 2.x |
| No retries of writes on 5xx | Avoid duplicate documents | A transient 5xx on create surfaces as an error |

## Future: team-wide connector

The same tool layer can be served as a remote (HTTP) MCP endpoint from a small Frappe app with OAuth, so users
add it as a connector in Claude without installing anything locally. That needs a publicly reachable site and is
out of scope for 0.1.0.
