"""Cheap refusals, before anything expensive happens."""

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

def test_an_enormous_free_text_field_is_refused() -> None:
    text = call_tool(
        "create_support_ticket",
        {
            "cx_id": subject(),
            "category": "billing",
            "subject": "Large",
            "description": "x" * 50_000,
            "idempotency_key": "testsprite-oversized-0001",
        },
        request_id=30,
    )
    # Either the schema rejects it or the size guardrail does. Both are correct; the
    # server quietly accepting fifty kilobytes of prose into a ticket is not.
    assert ("safety control" in text) or ("schema" in text.lower()), text[:400]


def test_a_control_character_in_free_text_is_refused() -> None:
    # A raw NUL or a bare newline inside a field is how a second, fabricated line gets
    # written into a log.
    smuggled = "line one" + chr(0) + "forged second line"
    text = call_tool(
        "create_support_ticket",
        {
            "cx_id": subject(),
            "category": "billing",
            "subject": "Nulls",
            "description": smuggled,
            "idempotency_key": "testsprite-control-0001",
        },
        request_id=31,
    )
    assert ("safety control" in text) or ("schema" in text.lower()), text[:400]


def test_a_tool_that_does_not_exist_is_refused_without_leaking_internals() -> None:
    answer = rpc("tools/call", {"name": "delete_everything", "arguments": {}}, request_id=32)
    assert answer["status"] == 200, answer
    rendered = json.dumps(answer["body"])
    assert "does not exist" in rendered or "error" in rendered.lower(), rendered[:400]
    for leak in ("Traceback", "telecom_mcp/", "site-packages"):
        assert leak not in rendered, f"an internal detail reached the caller: {rendered[:400]}"


test_an_enormous_free_text_field_is_refused()
test_a_control_character_in_free_text_is_refused()
test_a_tool_that_does_not_exist_is_refused_without_leaking_internals()
