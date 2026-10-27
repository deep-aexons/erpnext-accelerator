import stat

import pytest

from erpnext_mcp.config import (
    Profile,
    list_profiles,
    load_profile,
    normalize_url,
    save_profile,
    set_default_profile,
)
from erpnext_mcp.errors import NotConfiguredError


def _p(name="dev", url="http://localhost:8000", **kw):
    return Profile(name=name, url=url, api_key="k", api_secret="s", **kw)


def test_not_configured_raises():
    with pytest.raises(NotConfiguredError) as e:
        load_profile()
    assert str(e.value) == "ERPNext not connected. Run /erp-connect."


def test_save_then_load_roundtrip(home):
    prof = _p(site="erpnext.localhost")
    path = save_profile(prof)
    assert load_profile() == prof
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_overrides_file(monkeypatch):
    save_profile(_p(url="http://a.test"))
    monkeypatch.setenv("ERPNEXT_URL", "http://b.test")
    assert load_profile().url == "http://b.test"


def test_env_only_profile(env_profile):
    prof = load_profile()
    assert prof.name == "env"
    assert prof.url == "http://erp.test"
    assert (prof.api_key, prof.api_secret) == ("k", "s")


def test_profile_selection(monkeypatch):
    save_profile(_p("dev", "http://dev.test"))
    save_profile(_p("prod", "http://prod.test"), make_default=False)
    assert load_profile().name == "dev"
    assert load_profile("prod").url == "http://prod.test"
    monkeypatch.setenv("ERPNEXT_PROFILE", "prod")
    assert load_profile().url == "http://prod.test"
    with pytest.raises(NotConfiguredError):
        load_profile("nope")
    assert sorted(list_profiles()) == ["dev", "prod"]
    set_default_profile("prod")
    monkeypatch.delenv("ERPNEXT_PROFILE")
    assert load_profile().name == "prod"


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("0", False), (None, False)])
def test_allow_submit_env_parsing(env_profile, monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("ERPNEXT_ALLOW_SUBMIT", raw)
    assert load_profile().allow_submit is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("localhost:8000/", "http://localhost:8000"),
        ("https://x.com", "https://x.com"),
        ("  http://erp.test///  ", "http://erp.test"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


# ---- final review fixes ------------------------------------------------------


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"profiles": []}'])
def test_corrupt_config_is_readable_error(home, content):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(content)
    with pytest.raises(NotConfiguredError) as e:
        load_profile()
    assert "config.json" in str(e.value) and "/erp-connect" in str(e.value)


def test_save_recovers_from_corrupt_config(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text("{not json")
    save_profile(_p())
    assert load_profile().name == "dev"
    assert list(home.glob("config.json.bad-*"))


def test_full_env_credentials_do_not_inherit_file_profile(monkeypatch):
    save_profile(_p("dev", "http://dev.test", allow_submit=True, site="dev.site"))
    monkeypatch.setenv("ERPNEXT_URL", "http://prod.test")
    monkeypatch.setenv("ERPNEXT_API_KEY", "k2")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "s2")
    prof = load_profile()
    assert prof.name == "env"
    assert prof.allow_submit is False
    assert prof.site is None
    assert prof.url == "http://prod.test"
