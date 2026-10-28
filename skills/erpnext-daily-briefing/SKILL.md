---
name: erpnext-daily-briefing
description: Use when the user asks for a daily briefing, morning summary, business snapshot, how today is going, or a summary of sales, cash, overdue invoices, low stock and pending approvals in ERPNext - including when a scheduled task asks for the daily briefing.
---

# ERPNext daily briefing

1. Call `daily_briefing()` (add `company=` if the site has several companies, `date=` for another day).
2. Reply with:
   - a 2–3 sentence headline: the most important thing first (e.g. cash low vs payables due, big overdue customer,
     stock-outs on fast movers);
   - then the briefing's `markdown`, trimmed of empty sections;
   - then up to 3 suggested actions (chase customer X, reorder item Y, approve Z). Suggest only — do not create,
     submit or send anything from a briefing.
3. If a section has an `error`, say it was unavailable and why in one line; still give the rest.

## Scheduling it

If the user wants it every morning, explain the options (details in the plugin's docs/setup.md):

- **Claude scheduled task** on the computer that can reach ERPNext, prompt: *"Give me my ERPNext daily briefing."*
  The computer must be awake and the Claude app open at that time.
- **Without Claude:** `erpnext-mcp briefing` from cron/launchd prints the same briefing as Markdown.
- A cloud scheduled task cannot reach an ERPNext that only runs on `localhost`.
