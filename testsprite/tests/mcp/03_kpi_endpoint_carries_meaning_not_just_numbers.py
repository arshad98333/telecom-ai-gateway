"""The endpoint an on-call engineer opens first."""

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

def test_kpi_endpoint_reports_every_family_with_interpretations() -> None:
    response = requests.get(f"{BASE_URL}/kpi", timeout=TIMEOUT)
    # 200 even when an objective is breached: a probe pointed here by mistake must not
    # restart the container.
    assert response.status_code == 200, response.text
    body = response.json()

    keys = {item["key"] for item in body["kpis"]}
    for expected in (
        "success_ratio",
        "failure_ratio",
        "latency_p95_seconds",
        "authorization_denial_ratio",
        "guardrail_block_ratio",
        "output_guardrail_blocks",
    ):
        assert expected in keys, f"{expected} missing from the catalogue: {sorted(keys)}"

    assert set(body["families"]) == {"service", "safety", "business"}, body["families"]

    for item in body["kpis"]:
        assert item["question"].endswith("?"), f"{item['key']} has no question"
        assert len(item["interpretation"]) > 40, f"{item['key']} does not say what it means"
        assert item["direction"] in ("up", "down", "neutral"), item

    objectives = {item["kpi"] for item in body["objectives"]}
    assert "success_ratio" in objectives, body["objectives"]
    for objective in body["objectives"]:
        assert objective["severity"] in ("page", "ticket"), objective


test_kpi_endpoint_reports_every_family_with_interpretations()
