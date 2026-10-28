"""Static checks on the plugin wrapper: manifests, skills, commands, docs."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL_CALL = re.compile(r"`([a-z][a-z0-9_]+)\(")


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


async def _tool_names() -> set[str]:
    from erpnext_mcp.tools import mcp

    return {t.name for t in await mcp.list_tools()}


def entry_version(market):
    return next(p for p in market["plugins"] if p["name"] == "erpnext-accelerator")["version"]


def test_package_version():
    from erpnext_mcp import __version__

    assert __version__ == "0.2.0"
    assert 'version = "0.2.0"' in (ROOT / "server/pyproject.toml").read_text()


def test_manifests_parse():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    mcp_cfg = json.loads((ROOT / ".mcp.json").read_text())
    assert plugin["name"] == "erpnext-accelerator"
    assert plugin["version"] == "0.2.0"
    assert entry_version(market) == "0.2.0"
    entry = next(p for p in market["plugins"] if p["name"] == "erpnext-accelerator")
    assert entry["source"] == "./"
    server = mcp_cfg["mcpServers"]["erpnext"]
    assert server["command"] == "uv"
    assert server["args"] == ["run", "--quiet", "--directory", "${CLAUDE_PLUGIN_ROOT}/server", "erpnext-mcp"]


SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
COMMANDS = sorted((ROOT / "commands").glob("*.md"))


def test_expected_skills_and_commands():
    assert {p.parent.name for p in SKILLS} == {
        "erpnext-stock", "erpnext-order-to-cash", "erpnext-ar-collections", "erpnext-customization",
        "erpnext-procure-to-pay", "erpnext-document-to-draft", "erpnext-daily-briefing", "erpnext-gst"}
    assert {p.stem for p in COMMANDS} == {"erp-connect", "erp-doctor", "erp-briefing"}


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_frontmatter(path):
    fm = _frontmatter(path)
    assert fm.get("name") == path.parent.name
    assert len(fm.get("description", "")) > 40


@pytest.mark.parametrize("path", COMMANDS, ids=lambda p: p.stem)
def test_command_frontmatter(path):
    assert _frontmatter(path).get("description")


async def test_referenced_tools_exist():
    names = await _tool_names()
    for path in SKILLS + COMMANDS:
        for ref in TOOL_CALL.findall(path.read_text()):
            assert ref in names, f"{path.name} mentions unknown tool {ref}()"


async def test_tools_doc_covers_every_tool():
    doc = (ROOT / "docs/tools.md").read_text()
    for name in await _tool_names():
        assert f"### `{name}`" in doc, f"docs/tools.md missing section for {name}"


def test_readme_quickstart_commands_in_setup_doc():
    readme = (ROOT / "README.md").read_text()
    setup = (ROOT / "docs/setup.md").read_text()
    quick = readme.split("## Quick start", 1)[1].split("\n## ", 1)[0]
    for cmd in re.findall(r"^\s*(/plugin [^\n]+|/erp-[a-z]+)", quick, re.MULTILINE):
        assert cmd.strip() in setup, f"quick-start command not in setup.md: {cmd}"
