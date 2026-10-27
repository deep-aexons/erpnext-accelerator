# Security

ERPNext permissions of the API user are always the final authority. The plugin adds its own rules on top so
mistakes stay small and reversible.

## Rules enforced by the server

1. **No delete.** There is no delete tool.
2. **Drafts only by default.** `create_draft` forces `docstatus = 0`. `update_draft` refuses submitted (1) or
   cancelled (2) documents and refuses to change `docstatus`, `name`, `doctype`, `owner`, `creation`,
   `modified` or `amended_from`.
3. **Double-gated submit/cancel.** `submit_doc` / `cancel_doc` need both:
   - `allow_submit` on the profile — off by default, and only the user can turn it on (`erpnext-mcp setup` in a
     terminal, `ERPNEXT_ALLOW_SUBMIT=1`, or editing the config file). The `connect` tool cannot change it; and
   - `confirm=true` on the call, which Claude only sends after showing a summary and getting an explicit yes.
4. **Write blocklist** (matched ignoring case and surrounding spaces). Writes are refused for:
   - security and permissions: User, Role, Role Profile, Has Role, Custom Role, DocPerm, Custom DocPerm,
     User Permission, Role Permission for Page and Report, OAuth Client, OAuth Bearer Token, Social Login Key,
     LDAP Settings;
   - code execution: DocType, Module Def, Server Script, Client Script, Report, Print Format, Web Page, Web Form,
     Scheduled Job Type;
   - side effects and system config: System Settings, Website Settings, Webhook, Notification, Auto Email Report,
     Email Queue, Data Import.
5. **No code in customizations.** Custom Field and Property Setter are allowed (so the customization skill
   works), but the plugin refuses HTML fields and any value starting with `eval:` or containing `<script`.
   Give the API user System Manager only on dev/test sites.
6. **Audit log** (file mode 0600). Every write and gated attempt is appended to `~/.config/erpnext-accelerator/audit.log` as JSON:
   `ts, profile, tool, doctype, name, outcome (ok | refused | error)`. No payloads, no secrets.

## Secrets

- Stored in `~/.config/erpnext-accelerator/config.json`, directory 0700, file 0600, written atomically.
- Never returned by tools, never logged. Logs go to stderr only.
- `/erp-connect` asks for the secret in chat, so it is in the chat transcript. Use
  `erpnext-mcp setup` in a terminal on shared machines.
- Rotate: regenerate keys in ERPNext, re-run setup.

## Least privilege checklist

- [ ] Dedicated API user, not your own account.
- [ ] Only the roles needed (Stock User / Sales User / Accounts User).
- [ ] No System Manager on production.
- [ ] `allow_submit` off on production unless there is a clear need.
- [ ] Review `audit.log` periodically.

## Environment overrides

If `ERPNEXT_URL`, `ERPNEXT_API_KEY` and `ERPNEXT_API_SECRET` are all set, they form a separate `env` profile;
no setting (including `allow_submit`) is inherited from the config file, so env credentials for one site never
pick up another site's permissions.

## Known limits

- Prompt injection: text stored in ERPNext records (e.g. a customer note) is read by Claude. The gates above
  still apply — nothing is submitted or deleted without the user.
- Report access follows ERPNext role permissions; some reports need extra roles.
