# End-to-end test report

**31 August 2026 · telecom agent workspace · arshad98333**

> **1,041 tests green. TestSprite is loaded but not fired.**
>
> Every suite that can run without leaving this machine passes, including a new 18-test
> TestSprite suite validated against both services running for real. What has *not*
> happened is a TestSprite cloud run — their API is blocked from this sandbox, and
> backend tests need a publicly reachable URL. Both are yours to unblock.

| | |
|---|---|
| **1,041** | automated tests passing across three suites |
| **95.16%** | tool-server coverage, against a 95% gate |
| **18 / 18** | TestSprite files passing in the local dry run |
| **0** | TestSprite cloud runs — blocked, see below |

---

## 1. What ran

A unit test proves a function is right. It cannot prove the two services agree about a
contract, and neither can prove that what you publish to PyPI works when a stranger
installs it. Each row exists because the row above it leaves a specific question open.

| Layer | Suite | Runs against | Tests | Result |
|---|---|---|---|---|
| unit + integration | telecom-mcp-tools | In-process, fake backend | 648 | **pass** |
| unit + integration | telecom-middleware | In-process, memory store | 373 | **pass** |
| cross-service | e2e | Both services, real HTTP between them | 20 | **pass** |
| packaging | `consumer_check.sh` | The built wheel in an empty venv | 10 checks | **pass** |
| black-box | TestSprite (local dry run) | Both services under uvicorn, over the wire | 18 files | **pass** |
| black-box | TestSprite (cloud) | A deployed, public URL | 18 files | **not run** |

The middleware suite also deselects **43 tests** marked `mongo`. They need a real MongoDB
replica set, which this environment cannot start — skipped by design, not silently
failing. CI runs them separately with `-m mongo` against Atlas.

### The path a request takes

```
TestSprite test  ──►  tool server  ──►  middleware  ──►  store
  HTTP client,        8-stage kernel,    verifies the      memory locally,
  no source           guardrails in      same token        MongoDB in a
  imports, injected   and out, audit     again, on its     deployment
  credential          trail              own account
```

Nothing is stubbed between those boxes. The token is minted once with both audiences —
exactly what Auth0 issues — the tool server verifies it, forwards it, and the middleware
verifies it again rather than trusting the caller to have checked. That second
verification is the point: a tool server bug must not become a data breach.

---

## 2. TestSprite

### Why no cloud run happened

`www.testsprite.com` and `api.testsprite.com` both return `403 from proxy after CONNECT`
— they are outside this sandbox's egress allow-list, from the cloud container and from
the bridge to your machine alike. The CLI cannot reach its own backend from here. Your
PowerShell has no such restriction.

Second constraint, and it applies on your machine too: TestSprite runs **backend** tests
from its cloud against a URL it can reach. The CLI rejects `localhost` and private
addresses for `--target-url`, and `--local <port>` — which tunnels — is frontend-only.
Both services need a public URL first.

So I did everything up to the run, then proved the tests work rather than handing you
eighteen guesses.

### Specs — generated, not written

| File | Contents |
|---|---|
| `specs/telecom-middleware.openapi.json` | 22 paths, 23 operations, taken from FastAPI's own document, so it cannot drift from the routes |
| `specs/telecom-mcp-tools.openapi.json` | 5 paths and all 8 tools, generated from the frozen `TOOL_SPECS` catalogue, with real argument examples per tool |

### Tool server — 12 tests

- Liveness consults nothing external; readiness names every dependency it probed.
- Metrics carry no customer identifier; the KPI endpoint carries meaning, not just numbers.
- An unauthenticated call reaches no tool. The catalogue is the frozen eight.
- Cross-account refused **and** the owner's own read still works.
- Three injection shapes refused with one byte-identical message.
- Oversized, control-character and unknown-tool calls refused without a stack trace.
- A repeated write creates one ticket. A refund queues a human and moves no money.

### Middleware — 6 tests

- Liveness and readiness answer different questions, and readiness 503s honestly.
- An anonymous read reaches no customer data, and the refusal leaks no field names.
- Account and invoice contracts, money as integer minor units.
- Cross-account refused on its own account, not on the tool server's word.
- Idempotency keys on the key, not the body — a different key creates a second ticket.
- The audit trail reads back with an unbroken hash chain and no raw customer reference.

Every assertion names a status code, a field, a count or a specific string. None says
"verify it works" — that phrasing is how a false pass gets past an AI judge, and the
official guidance is explicit about it.

---

## 3. Findings

I wrote a local harness that reproduces TestSprite's runner exactly — same three injected
globals (`TARGET_URL`, `__AUTH_CREDENTIAL__`, `__AUTH_HEADERS__`), same top-to-bottom
execution — and pointed it at both services running under uvicorn. First pass: 15 of 18.
**All three failures were bugs in my tests, not in the product.** Found in eight seconds
here rather than in a remote run you paid for.

**01 — Wrong field name in the invoice contract.**
The test asserted `amount`. The v1 contract carries `total` and `outstanding` separately.
Now asserts all seven documented fields, both money values as decimal strings.

**02 — Comparing envelopes instead of messages.**
The "every refusal is worded identically" test compared whole error envelopes, which each
carry a unique correlation id — so it could never pass. It now compares `error.message`,
which is what the promise is actually about.

**03 — A ticket category that does not exist.**
The audit test raised a ticket with `category: "technical"`; the enum is
`billing / network / device / account / order / other`. The middleware returned a correct
422 and the test was wrong.

Worth saying plainly: **no product defect was found by any layer in this report.** That is
a statement about the tests as much as the code — 1,041 tests that have all passed for a
while are 1,041 tests that have stopped telling you anything new, which is exactly why an
outside-in tool like TestSprite is worth adding.

---

## 4. Four steps to a real TestSprite verdict

### 1. Give both services a public URL

The honest option is your own pipeline: merge `Arshad` → `staging` and `cd-staging.yml`
builds, deploys and hands you the Container Apps URL. For a first look, a tunnel is fine.

```powershell
cloudflared tunnel --url http://localhost:8080   # tool server
cloudflared tunnel --url http://localhost:9000   # middleware
```

### 2. Mint a token for each project

Use the repo's own script so the audience and claim namespace match what the services
expect. The tests read the token from `__AUTH_HEADERS__` and never hardcode it, so this is
the only place a credential is entered.

```powershell
cd "C:\Users\HI\Desktop\ai agent\telecom-mcp"
uv run python scripts\mint_dev_token.py
```

### 3. Run the staged script

It creates two projects (the two services need different URLs and different tokens, and
one project holds one of each), uploads both specs, and loads the eighteen tests.

```powershell
cd "C:\Users\HI\Desktop\ai agent\testsprite"
./run-testsprite.ps1 -Stage preflight
./run-testsprite.ps1 -Stage setup -TargetUrlMcp https://... -TargetUrlMiddleware https://...
./run-testsprite.ps1 -Stage credentials
./run-testsprite.ps1 -Stage create
```

### 4. Smoke three, then decide about the rest

Three tests, not eighteen. A free account has 150 credits and a full backend suite is real
money — running everything should be your choice, made knowing the cost, which is why it
is a separate stage.

```powershell
./run-testsprite.ps1 -Stage smoke
./run-testsprite.ps1 -Stage all      # only when you mean it
```

### Re-run the dry run whenever you touch a test

Eight seconds, no credits, both services live. The cheapest possible way to find out that
an assertion can never hold.

```powershell
cd "C:\Users\HI\Desktop\ai agent\e2e"
uv run --with uvicorn --with requests python ..\testsprite\validate_locally.py
```

---

## 5. What none of this covers

**Not exercised anywhere**

- **Real MongoDB.** 43 tests are written and waiting on an Atlas replica set.
- **A real Auth0 tenant.** Every suite here uses the local HS256 verifier; JWKS fetching
  and key rotation are covered by unit tests with a fake fetcher, not against a tenant.
- **Redis idempotency.** Production requires it; the suites use the in-memory store.
- **Load and concurrency.** Shedding and the circuit breaker are unit-tested; nothing has
  put sustained traffic through either service.

**Where each gap closes**

- Mongo and Redis close on the **first staging deploy** — the readiness probe reports
  both, and the posture check in the pipeline asserts it.
- Auth0 closes on the same deploy: the identity-provider probe appears in `/readyz` only
  when a tenant is configured.
- Load stays open. It needs a decision about what traffic shape matters, not another test
  file.

---

*Artifacts under `ai agent/testsprite/`. Suite commands in each repo's `Makefile`.*
