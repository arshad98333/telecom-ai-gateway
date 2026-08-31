# Notes for anyone editing these tests

The TestSprite backend runner is a locked-down sandbox: **standard library +
`requests` + `pytest` + `numpy` + `scipy`, and nothing else.** In particular there is no
`pyjwt`, so a test cannot mint a token — it uses the one TestSprite injects.

Three rules these files follow, all of them from the official skill:

1. **Never hardcode a credential.** TestSprite prepends `__AUTH_CREDENTIAL__`,
   `__AUTH_TYPE__` and `__AUTH_HEADERS__` from the project's Authentication settings.
   Every authenticated request spreads `{**__AUTH_HEADERS__}`.
2. **Call the test function at the end of the file.** The runner executes top to bottom
   and does not collect `test_*` the way pytest does. A test that is only *defined*
   passes silently no matter what it asserts — which is worse than no test.
3. **Assert something concrete.** A status code, a named field, a count, a specific
   string. Never "verify it works".

## Why the tests read the subject out of the token

The tool server refuses cross-account access, so a read has to name the customer the
token is for — and the test does not know who that is. So it base64-decodes the JWT
payload and reads `sub`.

It **decodes, it does not verify**. Verification is the server's job and the whole point
of the test; doing it here as well would only prove that two libraries agree.

## Why a refused call is still HTTP 200

The MCP transport succeeded; the tool call did not. A refusal comes back as a JSON-RPC
result carrying an error envelope. Assert on the envelope, never on the status code —
asserting `!= 200` would pass for a network error and prove nothing.
