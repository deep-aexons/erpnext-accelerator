"""Entry point: `erpnext-mcp` (stdio MCP server), `erpnext-mcp setup`, `erpnext-mcp doctor`."""

from __future__ import annotations

import json
import logging
import sys
from getpass import getpass


def _ask(prompt: str, default: str = "") -> str:
    value = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return value or default


def _setup() -> int:
    from .errors import ERPNextError
    from .tools import save_connection

    print("ERPNext Accelerator setup (values are stored in your user config, secret hidden).")
    profile = _ask("Profile name", "dev")
    url = _ask("ERPNext URL", "http://localhost:8000")
    site = _ask("Site name for Host header (blank if not needed, e.g. erpnext.localhost)")
    api_key = _ask("API key")
    api_secret = getpass("API secret: ")
    allow = _ask("Allow submit/cancel (still asks every time)? y/N", "n").lower().startswith("y")
    allow_gst = _ask("Allow e-invoice/e-way generation (needs India Compliance; still asks every time)? y/N",
                     "n").lower().startswith("y")
    try:
        out = save_connection(profile, url, api_key, api_secret, site=site or None, allow_submit=allow,
                              allow_gst_actions=allow_gst)
    except ERPNextError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    print(f"Connected as {out['user']}. Saved profile '{out['profile']}' to {out['saved_to']}.")
    return 0


def _doctor() -> int:
    from .tools import diagnose

    out = diagnose()
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


def _briefing(args: list[str]) -> int:
    import argparse

    from mcp.server.fastmcp.exceptions import ToolError

    from .tools import daily_briefing

    parser = argparse.ArgumentParser(prog="erpnext-mcp briefing")
    parser.add_argument("--company")
    parser.add_argument("--date", help="YYYY-MM-DD, default today")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    opts = parser.parse_args(args)
    try:
        out = daily_briefing(company=opts.company, date=opts.date)
    except ToolError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2) if opts.json else out["markdown"], end="" if not opts.json else "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    if argv[:1] == ["setup"]:
        return _setup()
    if argv[:1] == ["doctor"]:
        return _doctor()
    if argv[:1] == ["briefing"]:
        return _briefing(argv[1:])
    if argv:
        print("usage: erpnext-mcp [setup|doctor|briefing]", file=sys.stderr)
        return 2
    from .tools import mcp

    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
