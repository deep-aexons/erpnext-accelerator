# ERPNext Accelerator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a plug-and-play Claude plugin (MCP server + skills + setup commands) that lets users work with any ERPNext site in plain language.

**Architecture:** A Python stdio MCP server (`erpnext_mcp`) launched by `uv` from the plugin. Four focused modules: `config` (profiles), `client` (HTTP + retries + error mapping), `guards` (safety + audit), `tools` (thin MCP tools). Skills and two setup commands sit on top.

**Tech Stack:** Python >=3.10, `mcp` (FastMCP), `httpx`; tests: `pytest`, `respx`; runner: `uv`.

**Spec:** `docs/superpowers/specs/2026-10-27-erpnext-accelerator-design.md`

## Global Constraints

- Root: `/Users/deepchavhan/Aexons/Projects/erp/erpnext-accelerator/`. Do not modify `erp/erpnext` or `erp/frappe-bench`.
- Python `>=3.10`; package `erpnext_mcp` under `server/src/`; console script `erpnext-mcp`.
- Config file `~/.config/erpnext-accelerator/config.json` (dir override `ERPNEXT_ACCELERATOR_HOME`), mode 0600; env vars win over file.
- Env vars: `ERPNEXT_URL`, `ERPNEXT_SITE`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`, `ERPNEXT_PROFILE`, `ERPNEXT_ALLOW_SUBMIT`, `ERPNEXT_TIMEOUT`, `ERPNEXT_MAX_RETRIES`.
- Timeouts: connect 5 s, total 30 s default. Retries default 3, backoff base 0.5 s, cap 8 s, full jitter, honour `Retry-After`.
- Reads retry on 429/500/502/503/504 + network errors; writes retry only on 429 + connect errors; never retry 400/401/403/404/409/417.
- List limit default 20, max 500; `run_report`/`get_stock_levels` default 100. Results carry `truncated`.
- No delete tool. Submit/cancel need `confirm=True` AND profile `allow_submit`.
- Logs to stderr only; never log secrets or full payloads.
- Not-configured message exactly: `ERPNext not connected. Run /erp-connect.`
- No git repo yet (session cannot remove git lock files): commit steps are skipped until the user initialises git.

## Review Focus

1. URL typed as `localhost:8000/` (no scheme, trailing slash) → normalised to `http://localhost:8000`. Test in Task 1.
2. `filters` given as dict or as list-of-lists `[["status","=","Overdue"]]` → both sent JSON-encoded. Test in Task 4.
3. DocType with spaces (`Sales Invoice`) and names with `/` or spaces → path segments URL-encoded. Test in Task 2.
4. Non-JSON response (HTML 502 page, login redirect) → readable `ERPNextError`, no crash. Test in Task 2.
5. Very large list/report → capped rows with `truncated: true`. Test in Task 4.

---

### Task 1: Package scaffold + config

**Files:**
- Create: `server/pyproject.toml`, `server/src/erpnext_mcp/__init__.py`, `server/src/erpnext_mcp/errors.py`, `server/src/erpnext_mcp/config.py`
- Test: `server/tests/test_config.py`, `server/tests/conftest.py`

**Interfaces:**
- Produces (`errors.py`): `class ERPNextError(Exception)` with attrs `status: int | None`, `kind: str`, `message: str`, `__str__ -> message`; subclasses `NotConfiguredError`, `GuardError`.
- Produces (`config.py`): `@dataclass(frozen=True) Profile(name: str, url: str, api_key: str, api_secret: str, site: str | None = None, allow_submit: bool = False, timeout: float = 30.0, max_retries: int = 3)`; `config_home() -> Path`; `normalize_url(url: str) -> str`; `load_profile(name: str | None = None) -> Profile` (raises `NotConfiguredError`); `save_profile(profile: Profile, make_default: bool = True) -> Path`; `set_default_profile(name: str) -> None`; `list_profiles() -> list[str]`.

- [ ] **Step 1:** `pyproject.toml`: deps `mcp>=1.2`, `httpx>=0.27`; optional `test = ["pytest>=8", "respx>=0.21"]`; script `erpnext-mcp = "erpnext_mcp.__main__:main"`; hatchling src layout. `conftest.py` fixture `home(tmp_path, monkeypatch)` sets `ERPNEXT_ACCELERATOR_HOME` and clears all `ERPNEXT_*` env vars.
- [ ] **Step 2: Write failing tests** in `test_config.py`:
  - `test_not_configured_raises` — empty home → `NotConfiguredError`, `str(e) == "ERPNext not connected. Run /erp-connect."`
  - `test_save_then_load_roundtrip` — save `dev`, load → equal; file mode `0o600`.
  - `test_env_overrides_file` — file url A, `ERPNEXT_URL=B` → `B`.
  - `test_env_only_profile` — only env vars set → profile named `env`.
  - `test_profile_selection` — `ERPNEXT_PROFILE=prod` and explicit name both pick correct profile; unknown name → `NotConfiguredError`.
  - `test_allow_submit_env_parsing` — `"1"`, `"true"` → True; `"0"`, unset → False.
  - `test_normalize_url` — `"localhost:8000/"` → `"http://localhost:8000"`; `"https://x.com"` unchanged.
- [ ] **Step 3: Run** `cd server && uv run --extra test pytest tests/test_config.py -q` → FAIL (import errors).
- [ ] **Step 4: Implement** `errors.py` and `config.py` per Interfaces. Atomic write (temp file + `os.replace`), `os.chmod(0o600)`.
- [ ] **Step 5: Run** same command → all PASS.

### Task 2: HTTP client with retries and error mapping

**Files:**
- Create: `server/src/erpnext_mcp/client.py`; Modify: `errors.py` (add `parse_frappe_error`)
- Test: `server/tests/test_client.py`

**Interfaces:**
- Consumes: `Profile`, `ERPNextError`.
- Produces: `RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})`; `backoff_delay(attempt: int, retry_after: str | None, base: float = 0.5, cap: float = 8.0, rand: Callable[[], float] = random.random) -> float`; `parse_frappe_error(resp: httpx.Response) -> ERPNextError`; `class ERPNextClient(profile: Profile, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep)` with `resource_path(doctype: str, name: str | None = None) -> str`, `get(path: str, params: dict | None = None) -> Any`, `post(path: str, json: dict | None = None) -> Any`, `put(path: str, json: dict | None = None) -> Any`, `call_method(method: str, http: str = "GET", **params) -> Any` (returns `message`), `close()`.

- [ ] **Step 1: Write failing tests** (respx, `sleep` recorder):
  - `test_auth_and_host_headers` — `Authorization == "token k:s"`, `Host == "erpnext.localhost"` when `site` set.
  - `test_get_retries_429_then_ok` — 429 then 200 → result returned, 1 sleep.
  - `test_get_honours_retry_after` — 429 with `Retry-After: 2` → slept 2.0.
  - `test_get_gives_up_after_max_retries` — 503 ×4 with `max_retries=3` → `ERPNextError(status=503)`, 3 sleeps.
  - `test_no_retry_on_4xx` — 400, 401, 403, 404 → raised immediately, 0 sleeps.
  - `test_post_not_retried_on_500` — 500 → raised, 0 sleeps; `test_post_retried_on_429` → 1 sleep.
  - `test_network_error_retried_for_get` — `httpx.ConnectError` then 200 → ok.
  - `test_backoff_delay_bounds` — `rand=lambda: 1.0`: attempt 0 → 0.5, attempt 10 → 8.0; retry_after `"3"` → 3.0.
  - `test_parse_frappe_error_server_messages` — body `{"exc_type":"ValidationError","_server_messages":"[\"{\\\"message\\\": \\\"Qty required\\\"}\"]"}` → kind `ValidationError`, message contains `Qty required`, no traceback text.
  - `test_auth_error_hint` — 401 → message contains `/erp-doctor`.
  - `test_non_json_response` (Review Focus 4) — 502 HTML body → `ERPNextError` with readable message; 200 HTML (login page) → `ERPNextError(kind="InvalidResponse")`.
  - `test_resource_path_encoding` (Review Focus 3) — `resource_path("Sales Invoice", "SINV/26 01")` → `/api/resource/Sales%20Invoice/SINV%2F26%2001`.
- [ ] **Step 2: Run** `uv run --extra test pytest tests/test_client.py -q` → FAIL.
- [ ] **Step 3: Implement** `client.py`: `httpx.Client(base_url=profile.url, timeout=httpx.Timeout(profile.timeout, connect=5.0))`; loop over attempts; for writes, retry only 429 and `httpx.ConnectError`; for reads, retry `RETRYABLE_STATUS` and `httpx.TransportError`. Log retries to stderr via `logging`. `parse_frappe_error` uses `exc_type`, `_server_messages` (JSON list of JSON strings), `message`, `exception` (last line only); strip HTML tags.
- [ ] **Step 4: Run** → PASS.

### Task 3: Guards and audit log

**Files:**
- Create: `server/src/erpnext_mcp/guards.py`
- Test: `server/tests/test_guards.py`

**Interfaces:**
- Consumes: `Profile`, `GuardError`, `config_home()`.
- Produces: `WRITE_BLOCKLIST: frozenset[str]` (exact list from spec §6); `check_writable(doctype: str) -> None`; `sanitize_new_doc(data: dict) -> dict`; `check_draft_update(current: dict, changes: dict) -> None`; `check_submit_allowed(profile: Profile, confirm: bool) -> None`; `audit(profile: str, tool: str, doctype: str, name: str | None, outcome: str) -> None`.

- [ ] **Step 1: Write failing tests:**
  - `test_blocklisted_doctype_refused` — each of `User`, `Role`, `Server Script`, `System Settings` → `GuardError`; `Sales Order`, `Custom Field` → ok.
  - `test_sanitize_forces_draft` — `{"docstatus": 1, "customer": "A"}` → `docstatus == 0`, input dict not mutated.
  - `test_update_refused_when_submitted` — current `docstatus=1` → `GuardError`; current 0 with changes `{"docstatus":1}` → `GuardError`; current 0 normal change → ok.
  - `test_submit_gate` — four combos of `confirm`/`allow_submit`: only both True passes; refusal message contains `ask the user`.
  - `test_audit_writes_jsonl` — two calls → two JSON lines with keys `ts, profile, tool, doctype, name, outcome`; no `api_key`/`api_secret` in file.
- [ ] **Step 2: Run** `uv run --extra test pytest tests/test_guards.py -q` → FAIL.
- [ ] **Step 3: Implement** `guards.py`. Audit failures are logged to stderr, never raised.
- [ ] **Step 4: Run** → PASS.

### Task 4: Read tools + MCP server entry

**Files:**
- Create: `server/src/erpnext_mcp/tools.py`, `server/src/erpnext_mcp/__main__.py`
- Test: `server/tests/test_tools_read.py`, `server/tests/test_mcp_smoke.py`

**Interfaces:**
- Consumes: `load_profile`, `ERPNextClient`, `ERPNextError`.
- Produces: `mcp: FastMCP` named `"erpnext"`; `set_client_factory(fn: Callable[[Profile], ERPNextClient]) -> None` (test hook); tools `ping() -> dict`, `list_docs(doctype: str, filters: dict | list | None = None, fields: list[str] | None = None, order_by: str | None = None, limit: int = 20) -> dict`, `get_doc(doctype: str, name: str) -> dict`, `count_docs(doctype: str, filters: dict | list | None = None) -> int`, `get_doctype_schema(doctype: str) -> dict`, `run_report(report_name: str, filters: dict | None = None, limit: int = 100) -> dict`, `get_stock_levels(item_code: str | None = None, warehouse: str | None = None, include_zero: bool = False, limit: int = 100) -> dict`. List-shaped results: `{"rows": [...], "count": n, "truncated": bool}`. Errors raised as `mcp.server.fastmcp.exceptions.ToolError(str(err))`. `main() -> None` in `__main__.py`: no args → `mcp.run()`; `setup` / `doctor` added in Task 5.

- [ ] **Step 1: Write failing tests** (respx-backed client via `set_client_factory`, profile from env):
  - `test_ping` — `frappe.auth.get_logged_user` → `{"user": "...", "url": "..."}`.
  - `test_list_docs_filters_encoding` (Review Focus 2) — dict and list-of-lists both appear JSON-encoded in `filters` query param; `fields` default `["name"]`.
  - `test_list_docs_truncation` (Review Focus 5) — requests `limit_page_length = limit + 1`; 21 rows back for limit 20 → 20 rows, `truncated True`; `limit=9999` clamps to 500.
  - `test_get_doc` — returns `data`.
  - `test_count_docs` — calls `frappe.client.get_count`.
  - `test_schema_compact` — `docs[0].fields` mapped to `{fieldname, label, fieldtype, options, reqd}`; layout fieldtypes (`Section Break`, `Column Break`, `Tab Break`) dropped; child tables listed under `child_tables`.
  - `test_run_report_shapes_rows` — `message.columns` → list of fieldnames; `message.result` capped at `limit`.
  - `test_stock_levels` — queries `Bin` with fields `item_code, warehouse, actual_qty, reserved_qty, ordered_qty, projected_qty`; default filter `actual_qty != 0`; `include_zero=True` removes it.
  - `test_not_configured_tool_error` — no config → `ToolError` with exact not-configured text.
  - `test_mcp_smoke.py::test_tools_listed` — `await mcp.list_tools()` names include all read tools.
- [ ] **Step 2: Run** `uv run --extra test pytest tests/test_tools_read.py tests/test_mcp_smoke.py -q` → FAIL.
- [ ] **Step 3: Implement** tools. Client built per call from `load_profile()` (config read at call time). Tool docstrings written for the model: say when to use the tool and give one example call.
- [ ] **Step 4: Run** → PASS.

### Task 5: Write, gated and setup tools + CLI

**Files:**
- Modify: `tools.py`, `__main__.py`
- Test: `server/tests/test_tools_write.py`, `server/tests/test_setup.py`

**Interfaces:**
- Consumes: guards (Task 3), `save_profile`, `set_default_profile`, `list_profiles`.
- Produces: tools `create_draft(doctype: str, data: dict) -> dict` (`{"name", "doctype", "docstatus": 0, "url"}`), `update_draft(doctype: str, name: str, data: dict) -> dict`, `submit_doc(doctype: str, name: str, confirm: bool = False) -> dict`, `cancel_doc(doctype: str, name: str, confirm: bool = False) -> dict`, `connect(profile: str, url: str, api_key: str, api_secret: str, site: str | None = None, make_default: bool = True) -> dict`, `use_profile(profile: str) -> dict`, `diagnose(profile: str | None = None) -> dict` (`{"checks": [{"name", "ok", "detail"}], "ok": bool}`). CLI: `erpnext-mcp setup` (interactive, `getpass` for secret), `erpnext-mcp doctor` (prints diagnose JSON, exit 1 if not ok).

- [ ] **Step 1: Write failing tests:**
  - `test_create_draft_forces_docstatus_zero` — POST body `docstatus == 0`; audit line `outcome="ok"`; returned `url` = `<site url>/app/<slug>/<name>`.
  - `test_create_draft_blocklisted` — `User` → `ToolError`, no HTTP call, audit `outcome="refused"`.
  - `test_update_draft_checks_status` — GET returns docstatus 1 → `ToolError`, no PUT.
  - `test_submit_refused_without_confirm` / `test_submit_refused_without_allow` / `test_submit_ok` — ok path calls `frappe.client.submit` via POST with full doc fetched first.
  - `test_cancel_ok` — calls `frappe.client.cancel` with `doctype`, `name`.
  - `test_no_delete_tool` — no tool name contains `delete`.
  - `test_connect_validates_before_save` — ping 401 → `ToolError`, no config file; ping ok → file saved, default set.
  - `test_use_profile_unknown` — `ToolError` listing known profiles.
  - `test_diagnose_reports_checks` — checks named `reachable`, `auth`, `versions` (`frappe.utils.change_log.get_versions`), `read:Item`, `read:Customer`, `read:Sales Order`, `read:Bin`; one 403 → that check `ok False`, overall `ok False`.
- [ ] **Step 2: Run** `uv run --extra test pytest tests/test_tools_write.py tests/test_setup.py -q` → FAIL.
- [ ] **Step 3: Implement.** Every write/gated tool: guard → HTTP → `audit(...)` with `ok`/`refused`/`error`.
- [ ] **Step 4: Run** full suite `uv run --extra test pytest -q` → all PASS.

### Task 6: Plugin packaging, commands, skills

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.mcp.json`, `commands/erp-connect.md`, `commands/erp-doctor.md`, `skills/erpnext-stock/SKILL.md`, `skills/erpnext-order-to-cash/SKILL.md`, `skills/erpnext-ar-collections/SKILL.md`, `skills/erpnext-customization/SKILL.md`
- Test: `server/tests/test_plugin_files.py`

**Interfaces:**
- Consumes: tool names from Tasks 4–5 exactly.
- Produces: plugin `erpnext-accelerator` v`0.1.0`; MCP server key `erpnext`; command `uv` with args `["run", "--quiet", "--directory", "${CLAUDE_PLUGIN_ROOT}/server", "erpnext-mcp"]`.

- [ ] **Step 1: Write failing test** `test_plugin_files.py`: all JSON files parse; `plugin.json.name == "erpnext-accelerator"`; marketplace lists that plugin with `source: "./"`; every SKILL.md has frontmatter `name` + `description`; every tool name mentioned in skills/commands (regex on backticked `snake_case(`) exists in `mcp.list_tools()`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Write files.** Skill descriptions use everyday phrasing (spec §8). Order-to-cash skill: look up customer/items/prices, show summary, confirm, `create_draft`, never submit unless user asks and confirms. Customization skill: `Server Script` writes are blocked, so give code for the user to paste. Commands: `erp-connect` asks for values (warn secrets land in transcript; suggest `erpnext-mcp setup` alternative) then calls `connect`; `erp-doctor` calls `diagnose` and explains each failed check with a fix.
- [ ] **Step 4: Run** → PASS. If `claude` CLI is available, also `claude plugin validate .` → no errors.

### Task 7: Documentation

**Files:**
- Create: `README.md`, `CHANGELOG.md`, `docs/setup.md`, `docs/tools.md`, `docs/architecture.md`, `docs/security.md`, `docs/testing.md`

- [ ] **Step 1: Write docs** per spec §10. `setup.md`: create API user + role in ERPNext (v17 UI path), generate keys, install in Claude Code (`/plugin marketplace add <path>`, `/plugin install erpnext-accelerator@erpnext-accelerator`) and Claude Desktop, `/erp-connect`, multi-site `site` note, `uv` PATH fallback. `testing.md`: unit command + live checklist (stock, AR, draft order, submit refused, submit allowed with `allow_submit`). `tools.md` generated from tool docstrings (each tool: purpose, params, example, errors).
- [ ] **Step 2: Verify** every command in README quick start appears in `setup.md`; every tool in `mcp.list_tools()` has a section in `tools.md` (add assertion to `test_plugin_files.py`).
- [ ] **Step 3: Run** full suite → PASS.

### Task 8: Final verification

- [ ] **Step 1:** `cd server && uv run --extra test pytest -q` → all pass; record count.
- [ ] **Step 2:** Start server over stdio with env pointing to a respx-free dummy URL, send MCP `initialize` + `tools/list` via a short script → 14 tools listed.
- [ ] **Step 3:** Hand off live test (docs/testing.md) to user — the session cannot reach the Mac's localhost.
