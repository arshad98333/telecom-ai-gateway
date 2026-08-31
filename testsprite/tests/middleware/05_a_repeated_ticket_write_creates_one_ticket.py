"""Idempotency at the API, not only at the tool server."""

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
BASE_URL = os.environ.get("TARGET_URL", "http://127.0.0.1:9101").rstrip("/")

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

IDEMPOTENCY_KEY = "testsprite-mw-ticket-0001"


def test_the_same_idempotency_key_returns_the_same_ticket() -> None:
    mine = subject()
    body = {
        "cx_id": mine,
        "category": "billing",
        "subject": "Duplicate charge",
        "description": "Billed twice for the same month; please check.",
    }
    headers = {"Idempotency-Key": IDEMPOTENCY_KEY}

    first = post(f"/customers/{mine}/tickets", body, headers)
    assert first.status_code in (200, 201), f"HTTP {first.status_code}: {first.text[:300]}"
    created = first.json()
    assert created.get("ticket_id"), created
    assert created["cx_id"] == mine, created
    assert created["state"] in ("open", "queued"), created

    second = post(f"/customers/{mine}/tickets", body, headers)
    assert second.status_code in (200, 201), f"HTTP {second.status_code}: {second.text[:300]}"
    replayed = second.json()
    assert replayed["ticket_id"] == created["ticket_id"], (
        f"a retry created a second ticket: {created['ticket_id']} then {replayed['ticket_id']}"
    )
    if "deduplicated" in replayed:
        assert replayed["deduplicated"] is True, replayed

    # And the store agrees: exactly one ticket carries that id.
    listing = get(f"/customers/{mine}/tickets", params={"limit": 20})
    assert listing.status_code == 200, listing.text
    matching = [t for t in listing.json()["tickets"] if t["ticket_id"] == created["ticket_id"]]
    assert len(matching) == 1, f"{len(matching)} tickets share one id: {matching}"

    # Hand the id to the audit test, which runs in a later wave.
    print(f"ticket_id={created['ticket_id']}")


def test_a_different_key_with_the_same_body_creates_a_second_ticket() -> None:
    # Idempotency must key on the key, not on the body - otherwise a customer who
    # genuinely has two identical complaints can only ever raise one.
    mine = subject()
    body = {
        "cx_id": mine,
        "category": "billing",
        "subject": "Duplicate charge",
        "description": "Billed twice for the same month; please check.",
    }
    other = post(f"/customers/{mine}/tickets", body, {"Idempotency-Key": "testsprite-mw-ticket-0002"})
    assert other.status_code in (200, 201), other.text
    assert other.json()["ticket_id"], other.json()


test_the_same_idempotency_key_returns_the_same_ticket()
test_a_different_key_with_the_same_body_creates_a_second_ticket()
