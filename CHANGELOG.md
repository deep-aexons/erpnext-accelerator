# Changelog

## 0.1.0 — 2026-10-27

First release.

- MCP server `erpnext-mcp` with 14 tools: connection (`connect`, `use_profile`, `diagnose`, `ping`),
  read (`list_docs`, `get_doc`, `count_docs`, `get_doctype_schema`, `run_report`, `get_stock_levels`),
  write (`create_draft`, `update_draft`) and gated (`submit_doc`, `cancel_doc`).
- Profiles in `~/.config/erpnext-accelerator/config.json` (mode 0600); environment variables override.
- Retries with exponential backoff + jitter, `Retry-After` support, no retry of non-idempotent writes on 5xx.
- Safety rules: no delete, drafts only by default, case-insensitive DocType write blocklist (security, code
  execution, side effects), no `eval:`/script/HTML values in Custom Field or Property Setter, identity fields
  locked in `update_draft`, double-gated submit/cancel (Claude cannot enable submit), audit log (0600).
- Clear errors for corrupt config (auto-recovered on next setup), writes with unknown outcome
  (`UncertainWrite`), and prepared (background) reports.
- Skills: `erpnext-stock`, `erpnext-order-to-cash`, `erpnext-ar-collections`, `erpnext-customization`.
- Commands: `/erp-connect`, `/erp-doctor`. CLI: `erpnext-mcp setup`, `erpnext-mcp doctor`.
