# Changelog

## 0.2.0 — 2026-10-27

Phase 2: purchasing, documents, briefing, India GST. 11 new tools (25 total), 4 new skills, `/erp-briefing`.

- Procure to pay: `three_way_match` (PO ↔ receipt ↔ invoice flags); skill for supplier quotation comparison,
  purchase order drafts and payables ageing.
- Documents to drafts: `find_party`, `match_items`; skill that turns a PDF / photo into a draft Sales Order,
  Purchase Order or Purchase Invoice.
- Daily briefing: `daily_briefing` (sales, cash, overdue AR/AP, low stock, approvals), `/erp-briefing`,
  `erpnext-mcp briefing` CLI, scheduling guide.
- India GST: `gst_capabilities`, `gst_validate_gstin`, `gst_check_invoices`, `gst_reconcile_2b` (portal JSON or
  India Compliance data), `gst_status`, gated `gst_generate_e_invoice` / `gst_generate_e_waybill`.
  Works on core ERPNext (Tax ID, Customs Tariff Number) and uses India Compliance fields when installed.
- Safety: new `allow_gst_actions` switch (user-only), GST Settings / GST Credential blocklisted.
- Review fixes: 2B period from the return month with late-bill lookup; GST-only book tax (TDS/freight/RCM safe);
  SEZ/Overseas handling; 40% slab; India Compliance GSTIN lookup (`gstin`, not PAN in `tax_id`) and credit-note
  sign; briefing cash without group double counting and per currency; paging with explicit partial/refusal;
  chunked `in` lists for long item lists.

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
