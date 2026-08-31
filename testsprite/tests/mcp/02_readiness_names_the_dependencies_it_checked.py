"""Readiness is only useful if it says what it looked at."""

import base64
import json

import requests

#: The base URL is a LITERAL, not an injected global.
#:
#: TestSprite's V3 backend runner injects the credential block (__AUTH_CREDENTIAL__,
#: __AUTH_TYPE__, __AUTH_HEADERS__) and __VARS__ for captured values - and nothing
#: else. A test that reads an injected base-URL global fails automated validation
#: before a single request is made. The target is baked into the code, which is why
#: --target-url alone is not enough, and why rotating a tunnel means restamping every
#: test.
#:
#: Stamped for upload by stamp_target_url.py. The default below is the local dev
#: server, so the file stays runnable against a laptop without being edited.
BASE_URL = "http://127.0.0.1:9100"

TIMEOUT = 30
MCP = f"{BASE_URL}/mcp/"


#: Auth0 puts the customer reference in a namespaced claim. A locally-minted
#: development token puts it in `sub`. Read the claim first and fall back, so the same
#: test works against either without being edited.
CX_CLAIM = "https://telecom.example/cx_id"


def subject() -> str:
    """The customer the injected token is for.

    This decodes the JWT payload; it does not verify it. Verification is the server's
    job and is the thing under test - doing it here as well would only prove that two
    libraries agree with each other.
    """
    assert_credential_is_live()
    token = __AUTH_CREDENTIAL__.split()[-1]  # noqa: F821 - injected by the runner
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return claims.get(CX_CLAIM) or claims["sub"]


def assert_credential_is_live() -> None:
    """Fail with the real reason before the server's answer gets misread.

    The tool server returns an empty catalogue for a token it cannot verify, rather
    than an error - deliberately, so the catalogue cannot be used to probe which tools
    exist. That is correct, and it means an expired credential and a scope problem look
    identical from out here.

    So check the expiry directly and say so. "the credential expired 4 minutes before
    this run" is a fact somebody can act on; "no tools were listed" sends them reading
    the authorization kernel.
    """
    import time

    token = __AUTH_CREDENTIAL__.split()[-1]  # noqa: F821 - injected by the runner
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    expires_at = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    if expires_at is None:
        return
    overdue = time.time() - expires_at
    assert overdue < 0, (
        f"the injected credential expired {overdue / 60:.1f} minutes before this run. "
        "This is a credential problem, not a product one - refresh the token and reset "
        "it with `testsprite project credential`, or configure `project auto-auth`."
    )


def rpc(method: str, params: dict, request_id: int = 1, authenticated: bool = True) -> dict:
    """One JSON-RPC call. Follows the 307 from /mcp to /mcp/ the way a real client does."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if authenticated:
        headers.update(__AUTH_HEADERS__)  # noqa: F821 - injected by the runner
    response = requests.post(
        MCP,
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    return {"status": response.status_code, "body": _json_or_text(response)}


def _json_or_text(response):
    try:
        return response.json()
    except ValueError:
        return {"_raw": response.text[:500]}


def call_tool(name: str, arguments: dict, request_id: int = 9) -> str:
    """Call a tool and return the text payload, whether it is a result or a refusal."""
    answer = rpc("tools/call", {"name": name, "arguments": arguments}, request_id)
    assert answer["status"] == 200, f"transport failed: HTTP {answer['status']} {answer['body']}"
    content = answer["body"].get("result", {}).get("content")
    assert content, f"no content in the tool response: {answer['body']}"
    return content[0]["text"]

def test_readiness_reports_each_dependency_by_name() -> None:
    response = requests.get(f"{BASE_URL}/readyz", timeout=TIMEOUT)
    assert response.status_code in (200, 503), response.text
    body = response.json()
    assert body["status"] in ("healthy", "degraded", "unhealthy"), body

    names = {component["name"] for component in body["components"]}
    assert "telecom_middleware" in names, f"the backing API is not probed: {names}"
    assert "idempotency_store" in names, f"the dedup store is not probed: {names}"

    # A required dependency being down must take the instance out of rotation.
    required_down = [
        component
        for component in body["components"]
        if component["status"] == "unhealthy" and not component["optional"]
    ]
    if required_down:
        assert response.status_code == 503, (
            f"{[c['name'] for c in required_down]} is down but readiness still answered 200"
        )
        assert body["status"] == "unhealthy", body
    else:
        assert response.status_code == 200, body


test_readiness_reports_each_dependency_by_name()
