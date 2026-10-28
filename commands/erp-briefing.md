---
description: Get today's ERPNext briefing - sales, cash, overdue invoices, low stock and pending approvals.
argument-hint: "[company] [YYYY-MM-DD]"
---

Give the user their ERPNext daily briefing. Parse `$ARGUMENTS` for an optional company name and an optional date
(YYYY-MM-DD); call `daily_briefing(company, date)` with whichever were given, then follow the
erpnext-daily-briefing skill to present it (headline, briefing, up to 3 suggested actions).
