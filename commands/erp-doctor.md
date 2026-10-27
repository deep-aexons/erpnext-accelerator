---
description: Check the ERPNext connection and explain how to fix anything that fails.
argument-hint: "[profile-name]"
---

Run `diagnose(profile)` (use `$ARGUMENTS` as profile if given) and report the result as a short checklist.

For each failed check, give the most likely fix:

- **config** – not connected → run `/erp-connect`.
- **reachable** – site down or wrong URL → start the bench (`bench start` in frappe-bench), check the URL/port,
  and set the site name if the bench serves several sites.
- **auth** – 401/403 → regenerate API key/secret for the API user, run `/erp-connect` again.
- **versions** – informational; if it fails but auth works, the API user may lack System access.
- **read:<DocType>** – permission missing → give the API user a role that can read that DocType
  (e.g. Stock User for Bin/Item, Sales User for Customer/Sales Order).
- **submit** – informational: tells whether submit/cancel is enabled for this profile.

If everything passes, say so in one line.
