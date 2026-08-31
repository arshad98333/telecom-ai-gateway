"""Eight tools, and only the ones this identity may call."""

import base64
import json
import os

import requests

#: The target comes from the environment, not from the source.
#:
#: TARGET_URL is what the runner and CI both set; the default below is the local dev
#: server, so the file runs against a laptop with nothing set at all. Nothing rewrites
#: this line to point somewhere else.
#:
#: One exception, and it is the runner's, not ours: TestSprite's V3 backend sandbox
#: validates an uploaded file before it makes a single request, and rejects one whose
#: base URL is not a literal. `python stamp_target_url.py <mcp> <middleware>` resolves
#: this expression to a literal into build/ for that upload only. The sources stay as
#: they are, and every other way of running these tests reads the environment.
BASE_URL = os.environ.get("TARGET_URL", "http://127.0.0.1:9100").rstrip("/")

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

EXPECTED = {
    "get_customer_account",
    "get_active_services",
    "get_order_status",
    "get_invoice_summary",
    "get_network_status",
    "create_support_ticket",
    "schedule_callback",
    "request_refund_approval",
}


def test_initialize_then_tools_list_returns_the_v1_catalogue() -> None:
    assert_credential_is_live()

    handshake = rpc(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "testsprite", "version": "1"},
        },
        request_id=1,
    )
    assert handshake["status"] == 200, handshake
    server = handshake["body"]["result"]["serverInfo"]
    assert server["name"] == "telecom-mcp-tools", server

    listing = rpc("tools/list", {}, request_id=2)
    assert listing["status"] == 200, listing
    names = {tool["name"] for tool in listing["body"]["result"]["tools"]}

    # A caller must never be shown a tool their scopes do not permit, so this is a
    # subset check: the full set for a fully-scoped token, fewer for a narrower one.
    assert names, (
        "the catalogue came back empty. The server returns an empty list rather than "
        "an error for a token it cannot verify, so this is a credential that did not "
        "verify - wrong audience, wrong issuer, or no permissions on it."
    )
    assert names <= EXPECTED, f"an unknown tool is exposed: {sorted(names - EXPECTED)}"

    for tool in listing["body"]["result"]["tools"]:
        assert tool["description"].strip(), f"{tool['name']} has no description"
        assert tool["inputSchema"]["type"] == "object", tool["name"]


test_initialize_then_tools_list_returns_the_v1_catalogue()
