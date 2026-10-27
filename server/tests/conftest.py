import os

import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Isolated config home and a clean ERPNEXT_* environment for every test."""
    for key in list(os.environ):
        if key.startswith("ERPNEXT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ERPNEXT_ACCELERATOR_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


@pytest.fixture
def env_profile(monkeypatch):
    """A complete profile supplied through environment variables."""
    monkeypatch.setenv("ERPNEXT_URL", "http://erp.test")
    monkeypatch.setenv("ERPNEXT_API_KEY", "k")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "s")


@pytest.fixture
def tools_env(env_profile):
    """Tools wired to a client that never really sleeps between retries."""
    from erpnext_mcp import tools
    from erpnext_mcp.client import ERPNextClient

    tools.set_client_factory(lambda prof: ERPNextClient(prof, sleep=lambda s: None))
    yield tools
    tools.set_client_factory(ERPNextClient)
