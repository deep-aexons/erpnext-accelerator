"""Connection profiles.

Resolution per key: environment variable > config file profile.
Config file: ``$ERPNEXT_ACCELERATOR_HOME/config.json`` (default ``~/.config/erpnext-accelerator``).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import NotConfiguredError

CONFIG_FILE = "config.json"
_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Profile:
    name: str
    url: str
    api_key: str
    api_secret: str
    site: str | None = None
    allow_submit: bool = False
    timeout: float = 30.0
    max_retries: int = 3
    allow_gst_actions: bool = False


def config_home() -> Path:
    override = os.environ.get("ERPNEXT_ACCELERATOR_HOME")
    return Path(override).expanduser() if override else Path.home() / ".config" / "erpnext-accelerator"


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = "http://" + url
    return url


def _read() -> dict:
    path = config_home() / CONFIG_FILE
    if not path.exists():
        return {"default_profile": None, "profiles": {}}
    try:
        data = json.loads(path.read_text() or "{}")
        if not isinstance(data, dict):
            raise ValueError("top level is not an object")
        data.setdefault("profiles", {})
        data.setdefault("default_profile", None)
        if not isinstance(data["profiles"], dict):
            raise ValueError("'profiles' is not an object")
    except ValueError as exc:
        raise NotConfiguredError(
            f"Config file {path} is unreadable ({exc}). Run /erp-connect to rewrite it."
        ) from None
    return data


def _read_or_reset() -> dict:
    """Like _read, but move an unreadable file aside so setup can repair it."""
    try:
        return _read()
    except NotConfiguredError:
        path = config_home() / CONFIG_FILE
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        os.replace(path, path.with_name(f"{CONFIG_FILE}.bad-{stamp}"))
        return {"default_profile": None, "profiles": {}}


def _write(data: dict) -> Path:
    home = config_home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    path = home / CONFIG_FILE
    fd, tmp = tempfile.mkstemp(dir=home, prefix=".config-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def list_profiles() -> list[str]:
    return list(_read()["profiles"].keys())


def save_profile(profile: Profile, make_default: bool = True) -> Path:
    data = _read_or_reset()
    entry = asdict(profile)
    entry.pop("name")
    entry["url"] = normalize_url(entry["url"])
    data["profiles"][profile.name] = entry
    if make_default or not data.get("default_profile"):
        data["default_profile"] = profile.name
    return _write(data)


def set_default_profile(name: str) -> None:
    data = _read()
    if name not in data["profiles"]:
        raise NotConfiguredError(
            f"Unknown profile '{name}'. Known profiles: {', '.join(data['profiles']) or 'none'}."
        )
    data["default_profile"] = name
    _write(data)


def load_profile(name: str | None = None) -> Profile:
    env = os.environ
    explicit = name or env.get("ERPNEXT_PROFILE")
    env_complete = all(env.get(v) for v in ("ERPNEXT_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET"))
    # Full credentials in the environment form their own profile; nothing is inherited from the file.
    data = {"default_profile": None, "profiles": {}} if (env_complete and not explicit) else _read()
    chosen = explicit or data.get("default_profile")

    base: dict = {}
    if chosen and chosen in data["profiles"]:
        base = dict(data["profiles"][chosen])
    elif explicit:
        raise NotConfiguredError(
            f"Unknown profile '{explicit}'. Known profiles: {', '.join(data['profiles']) or 'none'}. Run /erp-connect."
        )

    for key, var in (("url", "ERPNEXT_URL"), ("site", "ERPNEXT_SITE"),
                     ("api_key", "ERPNEXT_API_KEY"), ("api_secret", "ERPNEXT_API_SECRET")):
        if env.get(var):
            base[key] = env[var]
    if env.get("ERPNEXT_ALLOW_SUBMIT") is not None:
        base["allow_submit"] = env["ERPNEXT_ALLOW_SUBMIT"].strip().lower() in _TRUE
    if env.get("ERPNEXT_ALLOW_GST_ACTIONS") is not None:
        base["allow_gst_actions"] = env["ERPNEXT_ALLOW_GST_ACTIONS"].strip().lower() in _TRUE
    if env.get("ERPNEXT_TIMEOUT"):
        base["timeout"] = float(env["ERPNEXT_TIMEOUT"])
    if env.get("ERPNEXT_MAX_RETRIES"):
        base["max_retries"] = int(env["ERPNEXT_MAX_RETRIES"])

    if not all(base.get(k) for k in ("url", "api_key", "api_secret")):
        raise NotConfiguredError()

    return Profile(
        name=chosen if chosen in data["profiles"] else "env",
        url=normalize_url(base["url"]),
        api_key=base["api_key"],
        api_secret=base["api_secret"],
        site=base.get("site") or None,
        allow_submit=bool(base.get("allow_submit", False)),
        timeout=float(base.get("timeout", 30.0)),
        max_retries=int(base.get("max_retries", 3)),
        allow_gst_actions=bool(base.get("allow_gst_actions", False)),
    )
