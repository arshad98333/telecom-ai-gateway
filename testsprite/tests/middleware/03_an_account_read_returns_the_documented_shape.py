"""The response contract, asserted against the OpenAPI document."""

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

REQUIRED = ["cx_id", "account_status", "account_type", "display_name", "customer_since",
            "open_case_count"]

NEVER_DISCLOSED = ["passcode", "password", "pin", "card_number", "iban", "account_number"]


def test_the_account_read_matches_the_schema_and_hides_the_rest() -> None:
    mine = subject()
    response = get(f"/customers/{mine}")
    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text[:300]}"

    body = response.json()
    for field in REQUIRED:
        assert field in body, f"'{field}' is required by AccountResponse but missing: {sorted(body)}"
    assert body["cx_id"] == mine, f"the API answered about {body['cx_id']}, not {mine}"
    assert isinstance(body["open_case_count"], int) and body["open_case_count"] >= 0, body

    rendered = json.dumps(body).lower()
    for secret in NEVER_DISCLOSED:
        assert secret not in rendered, f"'{secret}' appears in an account response"


def test_the_invoice_read_carries_money_as_minor_units() -> None:
    response = get(f"/customers/{subject()}/invoices", params={"limit": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    for field in ("invoices", "total_count", "total_outstanding_minor", "currency"):
        assert field in body, f"'{field}' missing from InvoicesResponse: {sorted(body)}"
    # Minor units are integers on purpose: a float cannot hold 0.10 exactly, and this is
    # where the tool server converts to a decimal on the way out.
    assert isinstance(body["total_outstanding_minor"], int), body["total_outstanding_minor"]
    assert len(body["invoices"]) <= 3, "the limit was ignored"


test_the_account_read_matches_the_schema_and_hides_the_rest()
test_the_invoice_read_carries_money_as_minor_units()
