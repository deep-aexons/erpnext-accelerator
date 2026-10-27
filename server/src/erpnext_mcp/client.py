"""HTTP client for ERPNext with timeouts, retries and readable errors."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from .config import Profile
from .errors import ERPNextError, parse_frappe_error

log = logging.getLogger("erpnext_mcp.client")

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_WRITE_RETRYABLE_STATUS = frozenset({429})


def backoff_delay(
    attempt: int,
    retry_after: str | None,
    base: float = 0.5,
    cap: float = 8.0,
    rand: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based).

    ``Retry-After`` (seconds) wins when present and valid; otherwise full-jitter
    exponential backoff: ``rand() * min(cap, base * 2**attempt)``.
    """
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return rand() * min(cap, base * (2**attempt))


class ERPNextClient:
    def __init__(
        self,
        profile: Profile,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.profile = profile
        self._sleep = sleep
        headers = {
            "Authorization": f"token {profile.api_key}:{profile.api_secret}",
            "Accept": "application/json",
        }
        if profile.site:
            headers["Host"] = profile.site
        self._http = httpx.Client(
            base_url=profile.url,
            headers=headers,
            timeout=httpx.Timeout(profile.timeout, connect=5.0),
            transport=transport,
        )

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def resource_path(doctype: str, name: str | None = None) -> str:
        path = "/api/resource/" + quote(doctype, safe="")
        if name is not None:
            path += "/" + quote(str(name), safe="")
        return path

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ERPNextClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- verbs -----------------------------------------------------------
    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict | None = None) -> Any:
        return self._request("POST", path, json=json)

    def put(self, path: str, json: dict | None = None) -> Any:
        return self._request("PUT", path, json=json)

    def call_method(self, method: str, http: str = "GET", **params) -> Any:
        path = f"/api/method/{method}"
        data = self.get(path, params=params) if http == "GET" else self.post(path, json=params)
        return data.get("message") if isinstance(data, dict) else data

    # -- core ------------------------------------------------------------
    def _request(self, verb: str, path: str, **kw) -> Any:
        is_write = verb != "GET"
        retry_status = _WRITE_RETRYABLE_STATUS if is_write else RETRYABLE_STATUS
        attempts = self.profile.max_retries + 1

        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                resp = self._http.request(verb, path, **kw)
            except httpx.TransportError as exc:
                # Writes: only retry when the request never reached the server.
                retryable = isinstance(exc, httpx.ConnectError) if is_write else True
                if retryable and not last:
                    delay = backoff_delay(attempt, None)
                    log.warning("ERPNext %s %s network error (%s); retry in %.1fs", verb, path, type(exc).__name__, delay)
                    self._sleep(delay)
                    continue
                if is_write and not isinstance(exc, httpx.ConnectError):
                    raise ERPNextError(
                        f"The {verb} request to {path} was sent but no reply arrived ({type(exc).__name__}). "
                        "It may have been applied. Check with list_docs before trying again.",
                        kind="UncertainWrite",
                    ) from None
                raise ERPNextError(
                    f"Cannot reach ERPNext at {self.profile.url} ({type(exc).__name__}). "
                    "Is the site running? Run /erp-doctor.",
                    kind="NetworkError",
                ) from None

            if resp.status_code in retry_status and not last:
                delay = backoff_delay(attempt, resp.headers.get("Retry-After"))
                log.warning("ERPNext %s %s returned %s; retry in %.1fs", verb, path, resp.status_code, delay)
                self._sleep(delay)
                continue

            if resp.status_code >= 400:
                raise parse_frappe_error(resp)

            try:
                return resp.json()
            except ValueError:
                raise ERPNextError(
                    f"ERPNext returned a non-JSON response for {path} (status {resp.status_code}). "
                    "The URL may point to the wrong site or a login page. Run /erp-doctor.",
                    status=resp.status_code,
                    kind="InvalidResponse",
                ) from None

        raise AssertionError("unreachable")  # pragma: no cover
