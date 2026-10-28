# Setup guide

Time needed: about 10 minutes. Steps 1–3 are one-time.

## Prerequisites

- **ERPNext** v14 or newer, running and reachable from your computer (local: `bench start` in `frappe-bench`).
- **Claude Code** (full plugin: tools + skills + commands) or **Claude Desktop** (tools only, see §5).
- **uv** — `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`. Check with `uv --version`.
  uv downloads the server's Python dependencies automatically on first run.

## 1. Create an API user in ERPNext

Use a dedicated user so Claude's access is limited and auditable.

1. In ERPNext open **User → + Add User**. Email e.g. `claude-api@yourcompany.com`, User Type **System User**.
2. On the **Roles** section give only what you need:

   | Use | Roles |
   |---|---|
   | Stock questions | Stock User |
   | Orders, customers | Sales User |
   | Receivables | Accounts User |
   | Custom fields (customization skill) | System Manager — only on dev/test sites |

3. Save. Open the user's **Settings** tab → **API Access** → **Generate Keys**.
   Copy the **API Key** and the **API Secret** (the secret is shown only once).

## 2. Install the plugin in Claude Code

```
/plugin marketplace add /Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator
/plugin install erpnext-accelerator@erpnext-accelerator
```

Restart Claude Code if the commands do not show up. Commands may also appear namespaced as
`/erpnext-accelerator:erp-connect`.

To share with your team, push this folder to a Git repository and use its URL instead of the local path
in `/plugin marketplace add`.

## 3. Connect a site (one time)

```
/erp-connect
```

Claude asks for:

| Value | Local example |
|---|---|
| Profile name | `dev` |
| URL | `http://localhost:8000` |
| Site name | `erpnext.localhost` (only needed when the bench hosts several sites; harmless otherwise) |
| API key / secret | from step 1 |

Submit/cancel and e-invoice/e-way generation start **off**. Only you can turn them on, with the terminal setup
below (answer `y` to "Allow submit/cancel" or "Allow e-invoice/e-way generation") or with
`ERPNEXT_ALLOW_SUBMIT=1` / `ERPNEXT_ALLOW_GST_ACTIONS=1`; Claude cannot.

Claude checks the credentials **before** saving. The profile is stored in
`~/.config/erpnext-accelerator/config.json` with file mode 0600.

**Terminal alternative** (secret is hidden, nothing goes into the chat transcript):

```bash
uv run --directory /Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator/server erpnext-mcp setup
```

### Several sites

Run `/erp-connect` once per site with a different profile name (`dev`, `staging`, `prod`).
Switch by asking *"use the prod profile"* (Claude calls `use_profile`).

### Environment variables (CI, scripts)

Environment variables override the config file. If `ERPNEXT_URL`, `ERPNEXT_API_KEY` and `ERPNEXT_API_SECRET`
are all set, they form a separate profile called `env` and nothing (including `allow_submit`) is taken from
the file. If `ERPNEXT_PROFILE` is set, Claude cannot switch profiles until you unset it.

| Variable | Meaning |
|---|---|
| `ERPNEXT_URL` | Base URL |
| `ERPNEXT_SITE` | Host header / site name |
| `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` | Credentials |
| `ERPNEXT_PROFILE` | Profile to use |
| `ERPNEXT_ALLOW_SUBMIT` | `1`/`true` to allow submit/cancel |
| `ERPNEXT_ALLOW_GST_ACTIONS` | `1`/`true` to allow e-invoice / e-way bill generation (India Compliance) |
| `ERPNEXT_TIMEOUT` | Request timeout in seconds (default 30) |
| `ERPNEXT_MAX_RETRIES` | Retries for transient errors (default 3) |
| `ERPNEXT_ACCELERATOR_HOME` | Config directory (default `~/.config/erpnext-accelerator`) |

## 4. Use it

Just ask in plain language:

- "give me available stock for today"
- "how many units of ITEM-001 are in Stores?"
- "which customers owe more than 50,000? draft reminder emails for the top 3"
- "make a draft sales order for Acme: 10 × ITEM-001, delivery next Friday"
- "what fields does Sales Order have? add a Delivery Slot select field"
- "match last month's purchase orders with receipts and bills"
- *(attach a PDF or WhatsApp photo)* "make a draft sales order from this"
- "give me my daily briefing" or `/erp-briefing`
- "check September sales invoices for GST mistakes"
- "reconcile this GSTR-2B with our purchase invoices: ~/Downloads/2B_092026.json"

If something fails, run:

```
/erp-doctor
```

## 5. India GST

- **Without India Compliance** (core ERPNext): put the GSTIN in **Tax ID** on Company, Customer and Supplier, and the
  HSN/SAC code in **Customs Tariff Number** on each Item. Use account names that contain CGST, SGST/UTGST, IGST or
  Cess for GST tax accounts so the checks can classify tax rows.
- **With India Compliance:** nothing extra; its GST fields are used automatically. e-Invoice / e-Way Bill need GSP
  credentials configured in India Compliance's GST Settings (the plugin never reads or writes them), and the API user
  needs permission to submit Sales Invoices.
- **GSTR-2B file:** GST portal → Returns → GSTR-2B → *Download (JSON)*; give Claude the file path.

## 6. Daily briefing on a schedule

The briefing has to run on a machine that can reach your ERPNext.

- **Claude scheduled task** (Claude Desktop / Cowork on the same computer as a local ERPNext, or any computer for a
  public site): create a daily task with the prompt *"Give me my ERPNext daily briefing."* The computer must be awake
  and the Claude app open at that time. Cloud-run tasks cannot reach `localhost`.
- **Without Claude** (macOS launchd, every day at 08:31): save as
  `~/Library/LaunchAgents/com.aexons.erp-briefing.plist`, then `launchctl load` it.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.aexons.erp-briefing</string>
  <key>ProgramArguments</key><array>
    <string>/opt/homebrew/bin/uv</string><string>run</string><string>--quiet</string><string>--directory</string>
    <string>/Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator/server</string>
    <string>erpnext-mcp</string><string>briefing</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>31</integer></dict>
  <key>StandardOutPath</key><string>/Users/deepchavhan/erp-briefing.md</string>
</dict></plist>
```

  On Linux: `31 8 * * * uv run --quiet --directory <plugin>/server erpnext-mcp briefing > ~/erp-briefing.md`.

## 7. Claude Desktop (tools only)

Claude Desktop can use the MCP server (no skills/commands). Edit
`~/Library/Application Support/Claude/claude_desktop_config.json`:

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

Use the full path from `which uv` — Desktop does not always see your shell `PATH`.
Run the terminal setup (§3) first, then restart Claude Desktop.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "ERPNext not connected. Run /erp-connect." | No profile yet, or env vars incomplete |
| "Config file … is unreadable" | Run `/erp-connect` again; the bad file is kept as `config.json.bad-<time>` |
| "request was sent but no reply arrived" | The write may have happened; check ERPNext before retrying |
| "prepared (background) report" | Open that report in ERPNext; Claude falls back to list queries |
| "Cannot reach ERPNext at …" | Start the bench; check URL and port |
| 401 / 403 errors | Regenerate keys; check the API user's roles |
| "non-JSON response" | URL points to the wrong site or a proxy/login page; set the site name |
| Tools missing in Claude | `uv` not on PATH; restart Claude; run `uv run --directory <plugin>/server erpnext-mcp doctor` |
| Cloud-only Claude cannot reach `localhost` | Use Claude Code or Desktop on the same machine as the site |
