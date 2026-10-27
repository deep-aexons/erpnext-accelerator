import httpx
import pytest
import respx

from erpnext_mcp.client import ERPNextClient, backoff_delay
from erpnext_mcp.config import Profile
from erpnext_mcp.errors import ERPNextError, parse_frappe_error

BASE = "http://erp.test"


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(sleeps):
    prof = Profile(name="t", url=BASE, api_key="k", api_secret="s", site="erpnext.localhost", max_retries=3)
    c = ERPNextClient(prof, sleep=sleeps.append)
    yield c
    c.close()


@respx.mock
def test_auth_and_host_headers(client):
    route = respx.get(f"{BASE}/api/method/ping").respond(json={"message": "pong"})
    assert client.call_method("ping") == "pong"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "token k:s"
    assert req.headers["Host"] == "erpnext.localhost"


@respx.mock
def test_get_retries_429_then_ok(client, sleeps):
    respx.get(f"{BASE}/api/resource/Item").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"data": [1]})]
    )
    assert client.get("/api/resource/Item") == {"data": [1]}
    assert len(sleeps) == 1


@respx.mock
def test_get_honours_retry_after(client, sleeps):
    respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={})]
    )
    client.get("/x")
    assert sleeps == [2.0]


@respx.mock
def test_get_gives_up_after_max_retries(client, sleeps):
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(503))
    with pytest.raises(ERPNextError) as e:
        client.get("/x")
    assert e.value.status == 503
    assert len(sleeps) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 417])
@respx.mock
def test_no_retry_on_4xx(client, sleeps, status):
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(ERPNextError) as e:
        client.get("/x")
    assert e.value.status == status
    assert sleeps == []


@respx.mock
def test_post_not_retried_on_500(client, sleeps):
    respx.post(f"{BASE}/x").mock(return_value=httpx.Response(500))
    with pytest.raises(ERPNextError):
        client.post("/x", json={})
    assert sleeps == []


@respx.mock
def test_post_retried_on_429(client, sleeps):
    respx.post(f"{BASE}/x").mock(side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    assert client.post("/x", json={}) == {"ok": 1}
    assert len(sleeps) == 1


@respx.mock
def test_post_retried_on_connect_error(client, sleeps):
    respx.post(f"{BASE}/x").mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200, json={})])
    client.post("/x", json={})
    assert len(sleeps) == 1


@respx.mock
def test_post_not_retried_on_read_timeout(client, sleeps):
    respx.post(f"{BASE}/x").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ERPNextError):
        client.post("/x", json={})
    assert sleeps == []


@respx.mock
def test_network_error_retried_for_get(client, sleeps):
    respx.get(f"{BASE}/x").mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200, json={"a": 1})])
    assert client.get("/x") == {"a": 1}


@respx.mock
def test_network_error_exhausted_is_readable(client):
    respx.get(f"{BASE}/x").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ERPNextError) as e:
        client.get("/x")
    assert "Cannot reach ERPNext" in str(e.value)


def test_backoff_delay_bounds():
    one = lambda: 1.0
    assert backoff_delay(0, None, rand=one) == 0.5
    assert backoff_delay(10, None, rand=one) == 8.0
    assert backoff_delay(0, "3", rand=one) == 3.0
    assert backoff_delay(0, "garbage", rand=lambda: 0.0) == 0.0


def test_parse_frappe_error_server_messages():
    body = {
        "exc_type": "ValidationError",
        "exception": "Traceback (most recent call last):\n  File x\nfrappe.exceptions.ValidationError: Qty required",
        "_server_messages": '["{\\"message\\": \\"Row 1: <b>Qty</b> required\\"}"]',
    }
    err = parse_frappe_error(httpx.Response(417, json=body))
    assert err.kind == "ValidationError"
    assert "Qty required" in err.message
    assert "Traceback" not in err.message
    assert "<b>" not in err.message


@respx.mock
def test_auth_error_hint(client):
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(401, json={"exc_type": "AuthenticationError"}))
    with pytest.raises(ERPNextError) as e:
        client.get("/x")
    assert "/erp-doctor" in str(e.value)


@respx.mock
def test_non_json_error_response(client):
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(404, text="<html><h1>Not Found</h1></html>"))
    with pytest.raises(ERPNextError) as e:
        client.get("/x")
    assert e.value.status == 404
    assert "<" not in str(e.value)


@respx.mock
def test_non_json_success_response(client):
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    with pytest.raises(ERPNextError) as e:
        client.get("/x")
    assert e.value.kind == "InvalidResponse"


def test_resource_path_encoding(client):
    assert client.resource_path("Sales Invoice") == "/api/resource/Sales%20Invoice"
    assert client.resource_path("Sales Invoice", "SINV/26 01") == "/api/resource/Sales%20Invoice/SINV%2F26%2001"


# ---- final review fixes ------------------------------------------------------


@respx.mock
def test_write_timeout_is_uncertain_not_unreachable(client):
    respx.post(f"{BASE}/x").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ERPNextError) as e:
        client.post("/x", json={})
    assert e.value.kind == "UncertainWrite"
    assert "may have been applied" in str(e.value)
    assert "Cannot reach" not in str(e.value)
