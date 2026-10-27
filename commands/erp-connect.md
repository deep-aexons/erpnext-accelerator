---
description: One-time setup. Connect Claude to an ERPNext site and save it as a profile.
argument-hint: "[profile-name]"
---

Help the user connect this plugin to an ERPNext site. This is a one-time step per site.

1. Tell the user briefly what you need, and where to get the key:
   ERPNext → User list → open the API user → **Settings → API Access → Generate Keys**.
   Recommend a dedicated API user with only the roles they need (see the plugin's docs/setup.md).
2. Ask for these values (use `$ARGUMENTS` as the profile name if given, otherwise default `dev`):
   - profile name (e.g. `dev`, `prod`)
   - ERPNext URL (local default `http://localhost:8000`)
   - site name, only if the bench hosts several sites (e.g. `erpnext.localhost`); else leave blank
   - API key and API secret
3. Mention once: values typed into chat stay in the chat transcript. On a shared machine they can instead run
   `erpnext-mcp setup` in a terminal (secret is hidden): run `uv run --directory <folder where the plugin lives>/server erpnext-mcp setup`
   — for the local install that is `/Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator/server`.
4. Call `connect(profile, url, api_key, api_secret, site, make_default=true)`.
   Submit/cancel stays as it was (off for new profiles). Only the user can turn it on, in a terminal with
   `erpnext-mcp setup`; you cannot enable it.
5. On success: say who they are connected as and that they can now just ask in plain words, e.g.
   "give me available stock for today". Do not repeat the secret back.
   On failure: show the one-line reason and suggest `/erp-doctor`.
