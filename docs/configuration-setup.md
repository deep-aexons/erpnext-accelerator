# Configuration setup

For IT / admins. About 10 minutes per machine; steps 2–4 are one-time.
Functional (business) setup is in [functional-setup.md](functional-setup.md).

## 1. Prerequisites

| Requirement | Check |
|---|---|
| ERPNext v14+ running and reachable (local: `bench start` in `frappe-bench`) | Open `http://localhost:8000` |
| Claude Code (full plugin) or Claude Desktop (tools only) | `claude --version` |
| uv (runs the server; downloads dependencies on first run) | `brew install uv`, then `uv --version` |
| Git access to the repo (team installs) | `git clone git@github.com:deep-aexons/erpnext-accelerator.git` |

Claude must run on a machine that can reach the ERPNext URL. Cloud-only Claude sessions cannot reach `localhost`.

## 2. Create the API user in ERPNext

1. Setup → **Users** → **+ Add User**. Email e.g. `claude-api@aexons.com`, User Type **System User**. Save.
2. Add only the roles you need:

   | Use | Role |
   |---|---|
   | Stock questions, reorder | Stock User |
   | Customers, quotations, orders | Sales User |
   | Receivables, invoices | Accounts User |
   | Custom fields (customization skill) | System Manager — dev/test sites only |

3. Open the user → **Settings** tab → **API Access** → **Generate Keys**.
   Copy the **API Secret** immediately (shown once) and the **API Key**.
   If the button is not there, look in the form's **⋯** menu. Generating again replaces the old pair.

## 3. Install the plugin

### Option 1 — Claude Code (recommended: tools + skills + commands)

Start `claude` in a terminal (or use the Code tab in the Claude desktop app) and run:

```
/plugin marketplace add /Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator
/plugin install erpnext-accelerator@erpnext-accelerator
```

Teammates use the Git repo instead of a local path:

```
/plugin marketplace add git@github.com:deep-aexons/erpnext-accelerator.git
/plugin install erpnext-accelerator@erpnext-accelerator
```

Restart Claude Code. Commands may appear as `/erpnext-accelerator:erp-connect`.

### Option 2 — Claude Desktop chat (tools only)

Run the terminal setup from step 4 first, then edit
`~/Library/Application Support/Claude/claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "erpnext": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--quiet", "--directory",
               "/Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator/server", "erpnext-mcp"]
    }
  }
}
```

Use the full path from `which uv`. Quit Desktop fully (Cmd+Q) and reopen.

## 4. Connect a site (profile)

In Claude Code run `/erp-connect`, or in a terminal (secret hidden, nothing in chat history):

```bash
uv run --directory /Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator/server erpnext-mcp setup
```

| Field | Local example | Notes |
|---|---|---|
| Profile name | `dev` | One per site: `dev`, `staging`, `prod` |
| URL | `http://localhost:8000` | Base URL only |
| Site name | `erpnext.localhost` | Only if the bench hosts several sites |
| API key / secret | from step 2 | Verified before saving |
| Allow submit/cancel | `n` | Terminal setup only; Claude cannot turn it on |

Profiles are saved to `~/.config/erpnext-accelerator/config.json` (mode 0600).
Switch sites by asking *"use the prod profile"*.

## 5. Environment variables (CI, scripts)

If `ERPNEXT_URL`, `ERPNEXT_API_KEY` and `ERPNEXT_API_SECRET` are all set, they form a separate `env` profile and
nothing (including `allow_submit`) is read from the config file.

| Variable | Meaning | Default |
|---|---|---|
| `ERPNEXT_URL` | Base URL | — |
| `ERPNEXT_SITE` | Host header / site name | — |
| `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` | Credentials | — |
| `ERPNEXT_PROFILE` | Fixed profile; blocks switching | — |
| `ERPNEXT_ALLOW_SUBMIT` | `1`/`true` enables submit/cancel | off |
| `ERPNEXT_TIMEOUT` | Request timeout, seconds | 30 |
| `ERPNEXT_MAX_RETRIES` | Retries for transient errors | 3 |
| `ERPNEXT_ACCELERATOR_HOME` | Config directory | `~/.config/erpnext-accelerator` |

## 6. Verify

1. Run `/erp-doctor` (or `uv run --directory …/server erpnext-mcp doctor`).
   Checks: config, reachable, auth, versions, read access per DocType, submit status.
2. Ask *"give me available stock for today"*.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| "ERPNext not connected. Run /erp-connect." | No profile yet, or env vars incomplete |
| "Cannot reach ERPNext at …" | Start the bench; check URL and port |
| 401 / 403 | Regenerate keys; check the API user's roles |
| "non-JSON response" | Wrong site or a login/proxy page; set the site name |
| `read:<DocType>` fails in doctor | Give the API user a role that can read it |
| "request was sent but no reply arrived" | The write may have happened; check ERPNext before retrying |
| Tools missing in Claude | `uv` not on PATH (use the full path in Desktop); restart Claude |
| "Config file … is unreadable" | Re-run setup; old file kept as `config.json.bad-<time>` |

See also [security.md](security.md) for the safety rules and audit log.
