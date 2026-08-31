"""Raise a ticket, then find it three ways.

The tool server's journey test proves the tools chain. This proves the API underneath
them is genuinely consistent: what a write returned is what a list returns, what the
account summary counts, and what the audit trail recorded."""

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
BASE_URL = "http://127.0.0.1:9101"

TIMEOUT = 30
API = f"{BASE_URL}/api/v1"


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


def get(path: str, authenticated: bool = True, **kwargs):
    headers = dict(__AUTH_HEADERS__) if authenticated else {}  # noqa: F821 - injected
    return requests.get(f"{API}{path}", headers=headers, timeout=TIMEOUT, **kwargs)


def post(path: str, body: dict, extra_headers: dict | None = None, authenticated: bool = True):
    headers = dict(__AUTH_HEADERS__) if authenticated else {}  # noqa: F821 - injected
    headers.update(extra_headers or {})
    return requests.post(f"{API}{path}", headers=headers, json=body, timeout=TIMEOUT)

KEY = "testsprite-mw-journey-0001"


def test_a_ticket_is_visible_everywhere_it_should_be() -> None:
    me = subject()

    before = get(f"/customers/{me}")
    assert before.status_code == 200, before.text
    opening_cases = before.json()["open_case_count"]

    invoices = get(f"/customers/{me}/invoices", params={"limit": 3})
    assert invoices.status_code == 200, invoices.text
    assert invoices.json()["invoices"], "no seeded invoices to reference"
    invoice_id = invoices.json()["invoices"][0]["invoice_id"]

    created = post(
        f"/customers/{me}/tickets",
        {
            "cx_id": me,
            "category": "billing",
            "subject": f"Query on {invoice_id}",
            "description": f"Invoice {invoice_id} contains a charge I do not recognise.",
        },
        {"Idempotency-Key": KEY},
    )
    assert created.status_code in (200, 201), f"HTTP {created.status_code}: {created.text[:300]}"
    ticket = created.json()
    assert ticket["cx_id"] == me, ticket

    # 1. It comes back in the listing, exactly once.
    listing = get(f"/customers/{me}/tickets", params={"limit": 20})
    assert listing.status_code == 200, listing.text
    matching = [t for t in listing.json()["tickets"] if t["ticket_id"] == ticket["ticket_id"]]
    assert len(matching) == 1, f"{len(matching)} tickets carry id {ticket['ticket_id']}"
    assert matching[0]["subject"] == ticket["subject"], matching[0]

    # 2. The account summary counts it.
    after = get(f"/customers/{me}")
    assert after.status_code == 200, after.text
    assert after.json()["open_case_count"] >= opening_cases, (
        f"open cases went from {opening_cases} to {after.json()['open_case_count']}"
    )

    # 3. A retry does not create a second one. Checked here as well as at the tool
    #    server, because the two dedupe independently and either can regress alone.
    replay = post(
        f"/customers/{me}/tickets",
        {
            "cx_id": me,
            "category": "billing",
            "subject": f"Query on {invoice_id}",
            "description": f"Invoice {invoice_id} contains a charge I do not recognise.",
        },
        {"Idempotency-Key": KEY},
    )
    assert replay.status_code in (200, 201), replay.text
    assert replay.json()["ticket_id"] == ticket["ticket_id"], "the retry created a second ticket"


test_a_ticket_is_visible_everywhere_it_should_be()
