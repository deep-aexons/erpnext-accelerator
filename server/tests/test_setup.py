
import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from erpnext_mcp.config import list_profiles, load_profile

BASE = "http://erp.test"


@pytest.fixture
def tools_mod():
    from erpnext_mcp import tools
    from erpnext_mcp.client import ERPNextClient

    tools.set_client_factory(lambda prof: ERPNextClient(prof, sleep=lambda s: None))
    yield tools
    tools.set_client_factory(ERPNextClient)


@respx.mock
def test_connect_validates_before_save(tools_mod, home):
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(401, json={})
    with pytest.raises(ToolError):
        tools_mod.connect("dev", "erp.test/", "k", "bad")
    assert not (home / "config.json").exists()


@respx.mock
def test_connect_saves_profile(tools_mod, home):
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    out = tools_mod.connect("dev", "erp.test/", "k", "s", site="erpnext.localhost")
    assert out["user"] == "api@x" and out["profile"] == "dev"
    prof = load_profile()
    assert prof.url == BASE and prof.site == "erpnext.localhost" and prof.allow_submit is False
    assert "api_secret" not in out and "api_key" not in out


def test_use_profile_unknown(tools_mod):
    with pytest.raises(ToolError) as e:
        tools_mod.use_profile("prod")
    assert "Known profiles" in str(e.value)


@respx.mock
def test_use_profile_switches(tools_mod):
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    tools_mod.connect("dev", BASE, "k", "s")
    tools_mod.connect("prod", BASE, "k", "s", make_default=False)
    assert load_profile().name == "dev"
    out = tools_mod.use_profile("prod")
    assert out["profile"] == "prod" and load_profile().name == "prod"
    assert sorted(list_profiles()) == ["dev", "prod"]


def _mock_healthy(read_status=200):
    respx.get(f"{BASE}/api/method/ping").respond(json={"message": "pong"})
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    respx.get(f"{BASE}/api/method/frappe.utils.change_log.get_versions").respond(
        json={"message": {"frappe": {"version": "17.0.0-dev"}, "erpnext": {"version": "17.0.0-dev"}}})
    for dt in ["Item", "Customer", "Bin"]:
        respx.get(f"{BASE}/api/resource/{dt}").respond(json={"data": []})
    respx.get(f"{BASE}/api/resource/Sales%20Order").respond(read_status, json={"data": []})


@respx.mock
def test_diagnose_reports_checks(tools_env):
    _mock_healthy()
    out = tools_env.diagnose()
    names = {c["name"] for c in out["checks"]}
    assert {"reachable", "auth", "versions", "read:Item", "read:Customer", "read:Sales Order", "read:Bin"} <= names
    assert out["ok"] is True
    versions = next(c for c in out["checks"] if c["name"] == "versions")
    assert "frappe 17.0.0-dev" in versions["detail"]


@respx.mock
def test_diagnose_flags_failed_read(tools_env):
    _mock_healthy(read_status=403)
    out = tools_env.diagnose()
    so = next(c for c in out["checks"] if c["name"] == "read:Sales Order")
    assert so["ok"] is False and out["ok"] is False


def test_diagnose_not_configured(tools_mod):
    out = tools_mod.diagnose()
    assert out["ok"] is False
    assert out["checks"][0]["name"] == "config"


@respx.mock
def test_diagnose_unreachable_is_fast(tools_env):
    respx.get(f"{BASE}/api/method/ping").mock(side_effect=httpx.ConnectError("refused"))
    out = tools_env.diagnose()
    reach = next(c for c in out["checks"] if c["name"] == "reachable")
    assert reach["ok"] is False and out["ok"] is False
    assert len(respx.calls) == 1  # no retries, no later checks


@respx.mock
def test_cli_doctor_exit_code(tools_env, capsys):
    from erpnext_mcp.__main__ import main

    _mock_healthy(read_status=403)
    assert main(["doctor"]) == 1
    assert '"ok": false' in capsys.readouterr().out


@respx.mock
def test_cli_setup_saves(tools_mod, monkeypatch, home):
    from erpnext_mcp import __main__ as cli

    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    answers = iter(["dev", BASE, "", "key1", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "getpass", lambda prompt="": "sec1")
    assert cli.main(["setup"]) == 0
    prof = load_profile()
    assert (prof.api_key, prof.api_secret, prof.site) == ("key1", "sec1", None)


# ---- final review fixes ------------------------------------------------------

import inspect


def test_connect_tool_cannot_enable_submit(tools_mod):
    assert "allow_submit" not in inspect.signature(tools_mod.connect).parameters


@respx.mock
def test_connect_preserves_existing_allow_submit(tools_mod):
    from erpnext_mcp.config import Profile, save_profile

    save_profile(Profile(name="dev", url=BASE, api_key="k", api_secret="s", allow_submit=True))
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    tools_mod.connect("dev", BASE, "k", "s-new")
    prof = load_profile()
    assert prof.allow_submit is True and prof.api_secret == "s-new"


@respx.mock
def test_use_profile_refuses_when_env_profile_set(tools_mod, monkeypatch):
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    tools_mod.connect("dev", BASE, "k", "s")
    tools_mod.connect("prod", BASE, "k", "s", make_default=False)
    monkeypatch.setenv("ERPNEXT_PROFILE", "prod")
    with pytest.raises(ToolError) as e:
        tools_mod.use_profile("dev")
    assert "ERPNEXT_PROFILE" in str(e.value)


@respx.mock
def test_cli_setup_can_enable_submit(tools_mod, monkeypatch):
    from erpnext_mcp import __main__ as cli

    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    answers = iter(["dev", BASE, "", "key1", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "getpass", lambda prompt="": "sec1")
    assert cli.main(["setup"]) == 0
    assert load_profile().allow_submit is True
