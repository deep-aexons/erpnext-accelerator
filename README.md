# ERPNext Accelerator for Claude

Work with ERPNext in plain language from Claude. Ask *"give me available stock for today"*,
*"which customers owe more than 50k?"*, *"match this month's purchase orders with bills"*,
*"reconcile this GSTR-2B"* or attach an order photo and say *"make a draft sales order"* —
Claude reads live data from your ERPNext site and prepares **draft** documents for you to review.

It is a Claude plugin made of:

- an **MCP server** (`server/`, Python) that talks to the ERPNext REST API with retries and safety rules,
- eight **skills** that teach Claude ERP workflows: stock, order-to-cash, receivables, customization,
  procure-to-pay, document-to-draft, daily briefing and India GST,
- three **commands**: `/erp-connect` (one time per site), `/erp-doctor` (when something breaks) and
  `/erp-briefing` (today's summary).

Works with any ERPNext v14+ site (developed against Frappe/ERPNext 17.0.0-dev). Nothing is installed inside ERPNext.

## Quick start

Prerequisites: [Claude Code](https://docs.claude.com/en/docs/claude-code), [`uv`](https://docs.astral.sh/uv/),
a running ERPNext site, and an API key/secret for an ERPNext user ([how](docs/setup.md#1-create-an-api-user-in-erpnext)).

In Claude Code:

```
/plugin marketplace add /Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator
/plugin install erpnext-accelerator@erpnext-accelerator
/erp-connect
```

Restart Claude Code after installing if the commands do not appear. `/erp-connect` is a one-time step; after that just ask:

> give me available stock for today

Full guide: [docs/setup.md](docs/setup.md).

## What Claude can and cannot do

| Can | Cannot |
|---|---|
| Read any record your API user can read | Delete anything |
| Run ERPNext reports (Accounts Receivable, Stock Balance, …) | Edit submitted or cancelled documents |
| Check GST data and reconcile GSTR-2B | Change GST Settings or GSP credentials |
| Generate e-invoice / e-way bill — only if enabled **and** you confirm (India Compliance) | Cancel e-invoices or e-way bills |
| Create and edit **draft** documents | Write to users, roles, permissions, DocTypes, Server Scripts, system settings |
| Submit/cancel — only if enabled for the profile **and** you confirm each time | Act beyond the ERPNext permissions of the API user |

Details: [docs/security.md](docs/security.md).

## Documentation

| Doc | For |
|---|---|
| [docs/setup.md](docs/setup.md) | Install, API user, connecting sites, Claude Desktop |
| [docs/configuration-setup.md](docs/configuration-setup.md) | Admin: prerequisites, install, profiles, env vars, verify |
| [docs/functional-setup.md](docs/functional-setup.md) | Business: ERPNext masters, workflows, go-live checklist |
| [docs/tools.md](docs/tools.md) | Reference for every MCP tool |
| [docs/architecture.md](docs/architecture.md) | How it is built and why |
| [docs/security.md](docs/security.md) | Safety rules, audit log, least privilege |
| [docs/testing.md](docs/testing.md) | Unit tests and the live test checklist |
| [docs/superpowers/specs/](docs/superpowers/specs/) | Design spec |

## Project layout

```
.claude-plugin/   plugin + marketplace manifests
.mcp.json         starts the MCP server with uv
server/           Python MCP server (erpnext_mcp) + tests
skills/           workflow skills, triggered by plain-language requests
commands/         /erp-connect, /erp-doctor
docs/             documentation
```

## Development

```bash
cd server
uv run --extra test pytest -q
```

See [CHANGELOG.md](CHANGELOG.md) for versions.
