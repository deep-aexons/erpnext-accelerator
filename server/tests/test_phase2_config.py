import pytest
import respx

from erpnext_mcp.config import Profile, load_profile, save_profile
from erpnext_mcp.errors import GuardError
from erpnext_mcp.guards import check_gst_action_allowed, check_writable

BASE = "http://erp.test"


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("0", False), (None, False)])
def test_allow_gst_actions_env(env_profile, monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("ERPNEXT_ALLOW_GST_ACTIONS", raw)
    assert load_profile().allow_gst_actions is expected


@pytest.mark.parametrize("confirm,allow,ok", [(False, False, False), (True, False, False),
                                              (False, True, False), (True, True, True)])
def test_gst_gate(confirm, allow, ok):
    prof = Profile(name="t", url="http://x", api_key="k", api_secret="s", allow_gst_actions=allow)
    if ok:
        check_gst_action_allowed(prof, confirm)
        return
    with pytest.raises(GuardError) as e:
        check_gst_action_allowed(prof, confirm)
    assert ("erpnext-mcp setup" in str(e.value)) if not allow else ("confirm=true" in str(e.value))


@pytest.mark.parametrize("dt", ["GST Settings", "gst credential"])
def test_gst_settings_blocked(dt):
    with pytest.raises(GuardError):
        check_writable(dt)


def test_profile_roundtrip_with_gst_flag():
    prof = Profile(name="dev", url=BASE, api_key="k", api_secret="s", allow_gst_actions=True)
    save_profile(prof)
    assert load_profile() == prof


@respx.mock
def test_connect_preserves_allow_gst_actions():
    from erpnext_mcp import tools
    from erpnext_mcp.client import ERPNextClient

    tools.set_client_factory(lambda p: ERPNextClient(p, sleep=lambda s: None))
    save_profile(Profile(name="dev", url=BASE, api_key="k", api_secret="s", allow_gst_actions=True))
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    tools.connect("dev", BASE, "k", "s2")
    assert load_profile().allow_gst_actions is True
    tools.set_client_factory(ERPNextClient)


@respx.mock
def test_cli_setup_asks_gst_actions(monkeypatch):
    from erpnext_mcp import __main__ as cli
    from erpnext_mcp import tools
    from erpnext_mcp.client import ERPNextClient

    tools.set_client_factory(lambda p: ERPNextClient(p, sleep=lambda s: None))
    respx.get(f"{BASE}/api/method/frappe.auth.get_logged_user").respond(json={"message": "api@x"})
    answers = iter(["dev", BASE, "", "key1", "n", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "getpass", lambda prompt="": "sec1")
    assert cli.main(["setup"]) == 0
    prof = load_profile()
    assert prof.allow_submit is False and prof.allow_gst_actions is True
    tools.set_client_factory(ERPNextClient)
