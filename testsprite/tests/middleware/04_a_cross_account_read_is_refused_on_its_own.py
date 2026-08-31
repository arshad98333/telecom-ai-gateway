"""The middleware does not trust the tool server to have checked."""

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

def test_reading_another_customer_is_refused_here_too() -> None:
    mine = subject()
    other = "CX-000000-NOT-MINE" if mine != "CX-000000-NOT-MINE" else "CX-111111-NOT-MINE"

    response = get(f"/customers/{other}")
    assert response.status_code in (403, 404), (
        f"a cross-account read returned HTTP {response.status_code}: {response.text[:300]}"
    )
    rendered = response.text.lower()
    for leak in ("account_status", "display_name", "customer_since"):
        assert leak not in rendered, f"the refusal leaked '{leak}': {response.text[:300]}"


def test_the_owner_can_still_read_their_own_account() -> None:
    # Without this, an API that refused everything would pass the test above.
    assert get(f"/customers/{subject()}").status_code == 200


test_reading_another_customer_is_refused_here_too()
test_the_owner_can_still_read_their_own_account()
