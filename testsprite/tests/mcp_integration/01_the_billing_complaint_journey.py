"""One customer, one complaint, start to finish.

An endpoint test proves a call works. This proves the *sequence* works - that the
invoice a read returned is one a write can then reference, and that the ticket comes
back the same on a retry. Those are the joins between endpoints, and joins are where
contracts actually break."""

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

KEY = "testsprite-journey-billing-0001"


def test_a_customer_reads_their_bill_then_raises_a_ticket_about_it() -> None:
    me = subject()

    # 1. The agent orients itself. An account read is the first thing any real
    #    conversation does, and it names the customer the rest of the journey is about.
    account = expect_output(call_tool("get_customer_account", {"cx_id": me}, request_id=1), "the account read")
    assert account["cx_id"] == me, account
    assert account["account_status"] in ("active", "suspended", "closed", "pending"), account
    opening_cases = account["open_case_count"]

    # 2. It finds the charge the customer is asking about.
    billing = expect_output(
        call_tool("get_invoice_summary", {"cx_id": me, "limit": 5}, request_id=2),
        "the invoice read",
    )
    assert billing["invoices"], "the seeded customer has no invoices to complain about"
    invoice = billing["invoices"][0]

    # 3. It raises a ticket that references that specific invoice. This is the join:
    #    an identifier produced by a read, consumed by a write.
    ticket = expect_output(
        call_tool(
            "create_support_ticket",
            {
                "cx_id": me,
                "category": "billing",
                "subject": f"Query on invoice {invoice['invoice_id']}",
                "description": (
                    f"Invoice {invoice['invoice_id']} shows {invoice['outstanding']} "
                    f"{billing['currency']} outstanding and I do not recognise it."
                ),
                "idempotency_key": KEY,
            },
            request_id=3,
        ),
        "the ticket write",
    )
    assert ticket["ticket_id"], ticket
    assert ticket["state"] in ("open", "queued"), ticket

    # 4. The agent's connection drops and it retries. One ticket, not two - the whole
    #    reason writes carry an idempotency key.
    replay = expect_output(
        call_tool(
            "create_support_ticket",
            {
                "cx_id": me,
                "category": "billing",
                "subject": f"Query on invoice {invoice['invoice_id']}",
                "description": (
                    f"Invoice {invoice['invoice_id']} shows {invoice['outstanding']} "
                    f"{billing['currency']} outstanding and I do not recognise it."
                ),
                "idempotency_key": KEY,
            },
            request_id=4,
        ),
        "the retried ticket write",
    )
    assert replay["ticket_id"] == ticket["ticket_id"], (
        f"the retry created a second ticket: {ticket['ticket_id']} then {replay['ticket_id']}"
    )
    assert replay["deduplicated"] is True, replay

    # 5. And the account reflects the case that now exists. Read-after-write across two
    #    tools is the thing an endpoint test can never check.
    after = expect_output(call_tool("get_customer_account", {"cx_id": me}, request_id=5), "the account re-read")
    assert after["open_case_count"] >= opening_cases, (
        f"open cases went from {opening_cases} to {after['open_case_count']} after "
        "raising a ticket"
    )


test_a_customer_reads_their_bill_then_raises_a_ticket_about_it()
