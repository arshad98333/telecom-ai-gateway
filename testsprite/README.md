# TestSprite for this workspace

Everything TestSprite needs, prepared and ready to run. **Run it from your own
PowerShell** — see "Why this is not already run" below.

```
testsprite/
  specs/                            OpenAPI, generated from the code (not hand-written)
    telecom-mcp-tools.openapi.json    5 paths, 8 tools, from the frozen TOOL_SPECS
    telecom-middleware.openapi.json   22 paths, 23 operations, from FastAPI itself
  tests/
    mcp/          12 backend tests   the tool server
    middleware/    6 backend tests   the backing API
    _shared_notes.md                 the three rules these files follow, and why
  profile/                          the external-test profile, layered over each .env
    middleware.env                    the four settings that let an outside runner in
    mcp.env                           the same, for the tool server
  start_testable.py                 boot both in that profile, mint a token, preflight
  preflight.py                      prove a credential works before a run spends it
  generate_mcp_openapi.py           regenerates the tool server's spec from the catalogue
  run_testsprite.py                 the whole flow, in stages
```

## Why this is not already run

TestSprite is a cloud service. `www.testsprite.com` and `api.testsprite.com` are both
**blocked by this sandbox's egress allow-list** — every request returns `403 from proxy
after CONNECT`, from both the container and the machine bridge. So the CLI cannot reach
its own backend from here, and no amount of retrying changes that.

Your machine has no such restriction. Everything below runs there.

## What the first two runs actually found

Nothing about the product. Both suites failed at the door and the reports read as
sixteen product defects:

| The report said | It was |
| --- | --- |
| six middleware tests **blocked**, `401 service_not_recognised` | The API wants two credentials — the caller's token in `Authorization` and the calling service's in `X-Service-Authorization`. The runner can only send the first, so every call was refused before any route ran. |
| ten tool-server tests **failed**, `token_invalid`, catalogue `{"tools":[]}` | The services were running with `IDENTITY_VERIFIER=jwks` (real Auth0, RS256) while the credential on the project was an HS256 token from `mint_dev_token.py`. Nothing signed that way can verify there. |

Both are configuration, both cost a full paid run to discover, and neither was visible
in the failure text. `start_testable.py` and `preflight.py` exist so that cannot happen
a third time.

## Getting to a runnable target

```bash
cd testsprite
make testable        # or: python testsprite/start_testable.py
```

That brings both services up with `profile/*.env` layered over each service's own
`.env`, mints one token both hops accept, and preflights the pair. It changes exactly
the settings that stop an outside runner getting in — nothing else — and every one of
them is refused in production by the settings validator, with a warning logged each
start while they are live on a non-loopback interface.

Then, with the tunnels up:

```bash
python preflight.py --token <token> --mcp https://<mcp-host> --middleware https://<mw-host>
```

Exit 0 means a run buys real signal. Exit 1 names the door that refused and what to
change. Run it before every paid suite; it is free and it takes four seconds.

## The one thing to sort out first

TestSprite runs **backend** tests from its own cloud against a URL it can reach. The CLI
rejects `localhost` and private addresses for `--target-url`, and `--local <port>` — which
tunnels — is **frontend-only**. A backend test's target is baked into its run.

So before anything else, both services need a public URL. Two ways:

1. **Deploy staging.** You already have the pipeline: merge `Arshad` → `staging` and
   `cd-staging.yml` builds, deploys and hands you the Container Apps URL. This is the
   honest option — the tests then run against the thing customers will hit.
2. **Tunnel, for a first look.** `cloudflared tunnel --url http://localhost:8080` (and a
   second one for the middleware on 9000) gives you throwaway https URLs in seconds.

## The flow

```bash
cd testsprite

# 1. Is the CLI there and authenticated?
python run_testsprite.py preflight

# 2. Two projects, both specs uploaded
python run_testsprite.py setup \
    --mcp-url        https://<your-mcp-url> \
    --middleware-url https://<your-middleware-url>

# 3. A bearer token per project (prompted; never written to a file)
python run_testsprite.py credentials

# 4. Upload the 18 tests
python run_testsprite.py create

# 5. Smoke three of them — not all eighteen
python run_testsprite.py smoke

# 6. Only when you mean it, and know the credit cost
python run_testsprite.py all
```

Mint the tokens with the repo's own script, so the audience and the claim namespace
match what the services expect:

```bash
cd ../telecom-mcp
uv run python scripts/mint_dev_token.py
```

## Two projects, not one

The two services have different URLs and different token audiences, and a TestSprite
project holds one of each. One project would mean one of the two suites permanently
red for a reason that has nothing to do with the code.

## Hand-authored, not generated

The official skill offers generation first (`test plan generate` → review → `accept`) and
hand-authoring as the fallback. These are hand-authored, for one reason: the interesting
behaviour here is **refusal** — cross-account denial, injection blocking, the identical
wording of every refusal — and a generator working from an OpenAPI document proposes
happy paths. The specs are uploaded anyway, so you can run generation later and accept
whatever it proposes on top.

If you do: read the proposals before accepting. `accept --only` **discards** everything
you do not name in that one call.

## What the 18 tests cover

**Tool server (12)** — liveness consults nothing external · readiness names every
dependency · the KPI catalogue carries meaning not just numbers · metrics carry no
customer identifier · an unauthenticated call reaches no tool · the frozen v1 catalogue ·
the read contract, money as a decimal string · cross-account refused, own account still
works · three injection shapes refused with one identical message · oversized and
control-character arguments refused, unknown tool refused without a stack trace · a
repeated write creates one ticket · a refund request queues a human and moves no money.

**Middleware (6)** — liveness and readiness answer different questions · an anonymous
read reaches no customer data · the account and invoice contracts, money as minor units ·
cross-account refused on its own account, not on the tool server's word · a repeated
ticket write creates one ticket and a different key creates a second · the audit trail
reads back with an unbroken hash chain.

Every one of them asserts a status code, a named field, a count or a specific string.
None says "verify it works".

## The three rules these tests follow

From the official skill, and they are not negotiable:

1. **The file calls its own test functions at the end.** The runner executes top to
   bottom and does not collect `test_*` the way pytest does. A test that is only
   *defined* passes silently whatever it asserts — worse than having no test.
2. **No credential is ever hardcoded.** TestSprite injects `__AUTH_HEADERS__` from the
   project's Authentication settings; every request spreads it.
3. **Standard library, `requests`, `pytest`, `numpy`, `scipy` — nothing else.** No
   `pyjwt`, so the tests read the subject out of the injected token by base64-decoding
   the payload. They decode; they do not verify. Verifying is the server's job and is
   the thing under test.

## After a run

```bash
testsprite test list --project <projectId>
testsprite test steps <testId> --output json          # what actually happened
testsprite test artifact get <runId> --out ./.testsprite/runs/<runId>/   # on failure
```

Read a failure before believing it. The official guidance is blunt about this: check
whether the *plan* is the problem before concluding the *product* is.
