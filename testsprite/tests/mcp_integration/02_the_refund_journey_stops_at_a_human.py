"""Money is requested, queued, and does not move.

The single most important sequence in this system: a customer asks for money back, the
agent submits it, and nothing happens until a supervisor says so. Every step of that is
asserted here, including the one that must NOT happen."""

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


def expect_output(text: str, what: str) -> dict:
    """Parse a tool result, or fail with the reason it is not one.

    Without this a refusal surfaces as KeyError: 'ticket_id' three frames deep, which
    tells a reader nothing. The action budget is called out by name because it is the
    refusal a suite trips on rather than a person: it allows a handful of irreversible
    actions per case per hour, a whole regression suite is not one customer
    conversation, and the environment under test should raise
    TELECOM_MCP_GUARDRAIL_WRITE_ACTIONS_PER_CASE rather than the suite pretending the
    control is not there.
    """
    if "safety control" in text:
        raise AssertionError(
            f"{what} was refused by a guardrail. If the suite has already performed "
            "several writes as this customer, this is the per-case action budget - "
            "raise TELECOM_MCP_GUARDRAIL_WRITE_ACTIONS_PER_CASE on the target and "
            "restart it. Otherwise it is a real refusal worth reading."
        )
    if "not permitted" in text:
        raise AssertionError(f"{what} was refused by the authorization kernel: {text[:200]}")
    try:
        return json.loads(text)
    except ValueError as broken:
        raise AssertionError(f"{what} did not return a tool payload: {text[:200]}") from broken

KEY = "testsprite-journey-refund-0001"


def test_a_refund_is_requested_against_a_real_invoice_and_queues_a_human() -> None:
    me = subject()

    billing = expect_output(
        call_tool("get_invoice_summary", {"cx_id": me, "limit": 5}, request_id=1),
        "the invoice read",
    )
    assert billing["invoices"], "no invoices to request a refund against"
    invoice = billing["invoices"][0]

    arguments = {
        "cx_id": me,
        "invoice_id": invoice["invoice_id"],
        # Under the contract's 5.00 autonomous cap on purpose. Above it the schema
        # refuses, which is a different test.
        "amount": "2.50",
        "currency": billing["currency"],
        "reason": "billing_error",
        "justification": (
            f"Invoice {invoice['invoice_id']} appears to duplicate an earlier charge; "
            "requesting a partial refund pending review."
        ),
        "idempotency_key": KEY,
    }

    text = call_tool("request_refund_approval", arguments, request_id=2)
    if "not permitted" in text:
        # A token without refund:request is a legitimate configuration - the tool would
        # not have been listed either. Nothing further to assert.
        return

    request = expect_output(text, "the refund request")
    assert request["state"] == "pending_approval", request
    assert request["approver_role"] == "supervisor_approver", request
    # The assertion the whole design exists for.
    assert request["money_moved"] is False, f"the tool reported money moving: {request}"
    assert request["approval_request_id"], request

    replay = expect_output(
        call_tool("request_refund_approval", arguments, request_id=3), "the retried refund"
    )
    assert replay["approval_request_id"] == request["approval_request_id"], (
        "a retried refund request created a second approval"
    )
    assert replay["money_moved"] is False, replay

    # And the balance is untouched, because nothing was actually refunded.
    after = expect_output(
        call_tool("get_invoice_summary", {"cx_id": me, "limit": 5}, request_id=4),
        "the invoice re-read",
    )
    still = next(i for i in after["invoices"] if i["invoice_id"] == invoice["invoice_id"])
    assert still["outstanding"] == invoice["outstanding"], (
        f"the outstanding balance changed from {invoice['outstanding']} to "
        f"{still['outstanding']} while the refund was only requested"
    )


test_a_refund_is_requested_against_a_real_invoice_and_queues_a_human()
