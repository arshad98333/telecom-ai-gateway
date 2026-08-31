"""Walk the customer's whole surface in one pass.

Six reads, one identity, one journey. Individually each is an endpoint test; together
they answer the question a support agent actually has - can I see everything I need
about this person without being refused halfway through."""

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

def test_the_full_customer_surface_is_readable_and_consistent() -> None:
    me = subject()
    seen = {}

    for name, path, required in (
        ("account", f"/customers/{me}", ("cx_id", "account_status", "open_case_count")),
        ("services", f"/customers/{me}/services", ("services", "total_count")),
        ("orders", f"/customers/{me}/orders", ("orders", "total_count")),
        ("invoices", f"/customers/{me}/invoices", ("invoices", "total_count", "currency")),
        ("network", f"/customers/{me}/network", ("state",)),
        ("tickets", f"/customers/{me}/tickets", ("tickets", "total_count")),
    ):
        response = get(path, params={"limit": 5} if "limit" not in path else None)
        assert response.status_code == 200, (
            f"{name} refused a read the customer owns: HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        body = response.json()
        for field in required:
            assert field in body, f"{name}: '{field}' missing from the response: {sorted(body)}"
        seen[name] = body

    # Every collection agrees with its own count, which is the sort of thing that
    # silently drifts when pagination is added later.
    for name, key in (("services", "services"), ("orders", "orders"),
                      ("invoices", "invoices"), ("tickets", "tickets")):
        listed, total = len(seen[name][key]), seen[name]["total_count"]
        assert listed <= total, f"{name} listed {listed} items but reports total {total}"

    # And nothing in any of them carries a secret.
    rendered = json.dumps(seen).lower()
    for secret in ("passcode", "password", "card_number", "iban", "cvv"):
        assert secret not in rendered, f"'{secret}' appears in a customer read"


test_the_full_customer_surface_is_readable_and_consistent()
