# ERPNext Accelerator Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add procure-to-pay, document-to-draft, daily briefing and India GST capabilities (11 tools, 4 skills, 1 command, 1 CLI subcommand) to the plugin as v0.2.0.

**Architecture:** New focused modules register tools on the existing FastMCP instance via `tools._tool`; pure logic (GST rules, reconciliation, match flags, scoring, markdown) is separated from HTTP so it is tested directly.

**Tech Stack:** Python >=3.10, mcp>=1.10,<2 (FastMCP), httpx; pytest, pytest-asyncio, respx; uv.

**Spec:** `docs/superpowers/specs/2026-10-27-accelerator-phase-2-design.md` (and v0.1.0 spec it builds on)

## Global Constraints

- All v0.1.0 constraints hold (no delete, drafts only, blocklist, double gates, audit, retry rules, 500 row cap, stderr logs, no secrets).
- New tools: `three_way_match`, `find_party`, `match_items`, `daily_briefing`, `gst_capabilities`, `gst_validate_gstin`, `gst_check_invoices`, `gst_reconcile_2b`, `gst_status`, `gst_generate_e_invoice`, `gst_generate_e_waybill`. Total 25.
- IC methods exactly: `india_compliance.gst_india.utils.e_invoice.generate_e_invoice` (docname), `india_compliance.gst_india.utils.e_waybill.generate_e_waybill` (doctype, docname, values).
- `allow_gst_actions` never settable through the `connect` tool. Env `ERPNEXT_ALLOW_GST_ACTIONS`.
- Blocklist adds `GST Settings`, `GST Credential`.
- Standard GST rates (IGST): {0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28}; CGST/SGST = half.
- Valid GSTIN state codes: 01–38, 97.
- Version 0.2.0 in `plugin.json`, `marketplace.json`, `pyproject.toml`, `__init__.py`.
- No git: commit steps skipped (ledger ruling from phase 1 carries over). Test venv `UV_PROJECT_ENVIRONMENT=$HOME/venvs/erpmcp`.

## Review Focus

1. GSTR-2B file with amendments (`b2ba`, `cdnra`) or unknown sections → ignored with a count, not a crash. Test in Task 2.
2. Bill numbers that differ only by case, spaces, `/`, `-`, leading zeros (`INV/001` vs `inv-1`) → matched. Test in Task 2.
3. Party or item with no `tax_id` / HSN at all on core ERPNext → warning/error issue, never an exception. Test in Task 3.
4. Briefing on a site with no reorder levels, no workflows, no bank accounts → empty sections, not errors. Test in Task 6.
5. `match_items` given 0 lines or >100 lines → empty result / clear refusal. Test in Task 5.

---

### Task 1: Config, guards, module registration

**Files:** Modify `config.py`, `guards.py`, `tools.py`, `__main__.py`; Test: `tests/test_phase2_config.py`

**Interfaces:**
- Produces: `Profile.allow_gst_actions: bool = False` (last field); `guards.check_gst_action_allowed(profile: Profile, confirm: bool) -> None`; `tools.save_connection(..., allow_gst_actions: bool | None = None)` (None = keep existing); at end of `tools.py`: `from . import briefing, documents, gst_tools, p2p  # noqa: E402,F401` (modules created as empty stubs in this task).

- [ ] Tests: `test_allow_gst_actions_env` ("1" → True, unset → False); `test_gst_gate` (4 combos; refusal text mentions `erpnext-mcp setup` and `confirm=true`); `test_gst_settings_blocked` (`GST Settings`, `gst credential`); `test_connect_preserves_allow_gst_actions`; `test_cli_setup_asks_gst_actions` (answers ..., "n", "y" → allow_gst_actions True); `test_profile_roundtrip_with_gst_flag`.
- [ ] Run `uv run --extra test pytest tests/test_phase2_config.py -q` → FAIL.
- [ ] Implement. CLI setup asks after submit question: `Allow e-invoice/e-way generation (needs India Compliance)? y/N`.
- [ ] Run full suite → PASS.

### Task 2: GST pure helpers

**Files:** Create `gst.py`, `tests/fixtures/gstr2b_sample.json` (hand-written: 3 suppliers, b2b v4-style totals, one invoice v3-style `items[]`, one cdnr credit note, one `b2ba` entry); Test: `tests/test_gst_rules.py`

**Interfaces (pure, no HTTP):**
- `validate_gstin(value: str) -> dict` keys `valid, gstin, state_code, pan, reason`.
- `gstin_checksum(first14: str) -> str`.
- `classify_tax_row(row: dict) -> str | None`.
- `hsn_issue(code: str | None) -> tuple[str, str] | None` → (check, message).
- `check_invoice(doc: dict, ctx: dict) -> list[dict]`; ctx keys: `company_gstin`, `party_gstin`, `item_hsn: dict[item_code, str|None]`, `place_of_supply_state: str | None`.
- `parse_gstr2b(data: dict) -> dict` → `{rows: [...], skipped: {section: count}}`.
- `norm_bill_no(s: str) -> str`.
- `reconcile(gstr_rows: list[dict], book_rows: list[dict], amount_tolerance: float = 1.0) -> dict` → `{summary: {matched, mismatch, suggested, missing_in_books, missing_in_2b, itc_at_risk}, matched: [...], mismatch: [...], suggested: [...], missing_in_books: [...], missing_in_2b: [...]}`. Book rows keys: `name, supplier, supplier_gstin, bill_no, bill_date, taxable_value, total_tax`.

- [ ] Tests (table-driven): valid GSTINs computed with `gstin_checksum` in-test for `27AAPFU0939F1Z?` and `29ABCDE1234F1Z?`; invalid length, bad state `40`, bad checksum, lowercase input normalised; `classify_tax_row` by `gst_tax_type` (`igst_rcm`→igst) and by account head ("Output Tax CGST - X"→cgst, "UTGST"→sgst); hsn: `None`→hsn_missing, `"8471"` ok, `"84713"` → hsn_format, `"998314"` ok, `"99"` → hsn_format; `check_invoice` intra-state with IGST → wrong_tax_type; inter-state with CGST+SGST → wrong_tax_type; CGST 9 vs SGST 6 → cgst_sgst_unequal; IGST 17 → rate_not_standard; no tax rows → no_gst_tax warning; party gstin missing → warning; Purchase Invoice without bill_no → bill_no_missing; `parse_gstr2b` fixture → expected row count, cdnr negative, v3 items summed, dates ISO, `skipped == {"b2ba": 1}`; `norm_bill_no("INV/001") == norm_bill_no("inv-1")`; `reconcile` produces each status once on a crafted set, `itc_at_risk` sum exact.
- [ ] Run `pytest tests/test_gst_rules.py` → FAIL; implement; → PASS.

### Task 3: GST tools

**Files:** Fill `gst_tools.py`; Test: `tests/test_gst_tools.py`

**Interfaces:** consumes Task 2 helpers, `_tool`, `_connect`, `_clamp`, guards. Produces tools per spec §4.6.
Detect IC via `frappe.utils.change_log.get_versions` containing `india_compliance` (cached per call).

- [ ] Tests: `test_capabilities_without_ic` (hsn_field `customs_tariff_number`); `test_capabilities_with_ic`; `test_check_invoices_core_fields` (PI list → get_doc → Supplier tax_id + Item customs_tariff_number + Company tax_id lookups; returns issues incl. `hsn_missing` for an item with no code — Review Focus 3); `test_check_invoices_uses_ic_fields` (supplier_gstin, gst_hsn_code on rows, place_of_supply); `test_reconcile_from_file` (fixture path, mocked PI + Supplier lists → statuses); `test_reconcile_file_missing` → ToolError; `test_reconcile_from_ic_inward_supply`; `test_status_requires_ic` → ToolError mentioning India Compliance; `test_status_lists_pending`; `test_generate_e_invoice_gated` (no flag / no confirm → refused, no POST, audit refused); `test_generate_e_invoice_ok` (POST body `{"docname": "SINV-1"}`, then returns irn); `test_generate_e_waybill_ok` (POST body doctype/docname/values JSON string).
- [ ] FAIL → implement → PASS.

### Task 4: Procure-to-pay match

**Files:** Fill `p2p.py`; Test: `tests/test_p2p.py`

**Interfaces:** `match_flags(line: dict, invoiced: list[dict], tolerance_pct: float) -> list[str]` (line keys `qty, received_qty, rate, amount, billed_amt`; invoiced: PI item dicts with `rate, qty`); tool `three_way_match(...)` per spec §4.1.

- [ ] Tests: `match_flags` table: fully received+billed → []; 0 received → not_received; half received → partially_received + (if billed) billed_more_than_received; over receipt; received not billed; billed_amt > amount → over_billed; PI rate 105 vs PO 100 → rate_mismatch, within 10% tolerance → none. Tool: `test_three_way_match_queries` (PO list filters, PI item child query uses `parent=Purchase Invoice` and `po_detail` in-list), `test_only_issues_filters_clean_orders`, `test_limit_clamped_200`.
- [ ] FAIL → implement → PASS.

### Task 5: Documents helpers

**Files:** Fill `documents.py`; Test: `tests/test_documents.py`

**Interfaces:** `score(a: str, b: str) -> float` (0..1, normalised lowercase alnum); `looks_like_gstin(s: str) -> bool`; tools `find_party`, `match_items` per spec §4.2–4.3.

- [ ] Tests: score("ACME Pvt. Ltd.", "Acme Private Limited") > 0.6; identical → 1.0; `find_party` GSTIN path queries `tax_id`; name path ranks best first; invalid party_type → ToolError; `match_items` exact code, barcode, supplier part no (only when supplier), fuzzy; `confident` rules; 0 lines → `{"lines": []}`; 101 lines → ToolError (Review Focus 5).
- [ ] FAIL → implement → PASS.

### Task 6: Daily briefing + CLI

**Files:** Fill `briefing.py`; Modify `__main__.py`; Test: `tests/test_briefing.py`

**Interfaces:** `collect(client, company: str, date: str) -> dict` (sections); `render_markdown(b: dict) -> str`; tool `daily_briefing(company=None, date=None)`; CLI `briefing [--company C] [--date D] [--json]`.

- [ ] Tests: `test_company_autodetect_single`; `test_company_required_when_many` (ToolError lists names); `test_sections_happy_path` (sales totals summed, cash from Account Balance report for Bank and Cash, overdue top 5, low stock compare, approvals counts); `test_section_error_isolated` (Account Balance 403 → `cash.error`, others fine); `test_empty_site` (Review Focus 4: no reorder/workflow/bank → empty lists, no errors); `test_render_markdown_has_headings`; `test_cli_briefing_prints_markdown` and `--json`.
- [ ] FAIL → implement → PASS.

### Task 7: Skills, command, manifests

**Files:** Create `skills/erpnext-procure-to-pay/SKILL.md`, `skills/erpnext-document-to-draft/SKILL.md`, `skills/erpnext-daily-briefing/SKILL.md`, `skills/erpnext-gst/SKILL.md`, `commands/erp-briefing.md`; Modify manifests, `pyproject.toml`, `__init__.py` (0.2.0), `tests/test_plugin_files.py` (expected sets), `tests/test_tools_write.py` (tool count 25).

- [ ] Update tests first (8 skills, 3 commands, version 0.2.0, 25 tools) → FAIL.
- [ ] Write files per spec §5 → PASS (referenced-tool check covers skills).

### Task 8: Docs

**Files:** Modify `README.md`, `CHANGELOG.md` (0.2.0), `docs/tools.md` (11 new sections), `docs/setup.md` (GST actions flag; scheduling the briefing: Cowork/desktop scheduled task prompt, launchd example with CLI), `docs/security.md` (GST gate, blocklist), `docs/architecture.md` (modules), `docs/testing.md` (new live checks).

- [ ] Write; `test_plugin_files.py` doc checks → PASS; full suite → PASS.

### Task 9: Verification + final review

- [ ] Full suite; ruff; stdio smoke → 25 tools.
- [ ] Fresh reviewer (opus) on phase 2 code; re-grade; fix Critical/Important with RED→GREEN.
- [ ] Update project doc.
