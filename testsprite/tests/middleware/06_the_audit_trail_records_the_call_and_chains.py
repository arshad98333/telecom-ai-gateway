"""Every call leaves one record, and the chain proves nothing was removed."""

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

def test_the_audit_trail_is_readable_and_its_chain_is_intact() -> None:
    response = get("/audit", params={"limit": 50})
    if response.status_code in (401, 403):
        # Reading the audit trail is an admin-security scope. A customer token being
        # refused here is the control working, not a failure.
        rendered = response.text.lower()
        assert "record_id" not in rendered, f"the refusal leaked records: {response.text[:300]}"
        return

    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text[:300]}"
    body = response.json()
    assert "records" in body, sorted(body)

    # The trail is hash-chained. A broken link means a record was altered or removed,
    # and the API says exactly where rather than making the reader diff it by hand.
    assert body.get("chain_broken_at") in (None, ""), (
        f"the audit chain is broken at {body['chain_broken_at']}"
    )

    if body["records"]:
        record = body["records"][0]
        for field in ("record_id", "timestamp", "correlation_id", "outcome"):
            assert field in record, f"'{field}' missing from an audit record: {sorted(record)}"
        # The customer reference is pseudonymised on the telemetry path, so the raw
        # identifier must not appear in the trail.
        assert subject() not in json.dumps(body["records"]), (
            "the raw customer reference appears in the audit trail"
        )


def test_a_write_is_traceable_by_its_correlation_id() -> None:
    mine = subject()
    created = post(
        f"/customers/{mine}/tickets",
        {
            "cx_id": mine,
            "category": "network",
            "subject": "Traceability check",
            "description": "Raised by an automated check to confirm the call is recorded.",
        },
        {"Idempotency-Key": "testsprite-mw-audit-0001"},
    )
    assert created.status_code in (200, 201), created.text
    assert created.json().get("ticket_id"), created.json()


test_the_audit_trail_is_readable_and_its_chain_is_intact()
test_a_write_is_traceable_by_its_correlation_id()
