# telecom-mcp-client

A reference/ops client for `telecom-mcp`'s streamable-HTTP MCP endpoint. It exists so
there is one place that has already solved the boring, easy-to-get-wrong parts of
talking to that server — the MCP handshake, the trailing-slash footgun, and which
failures are safe to retry — rather than every script or notebook re-deriving them.

It is not a UI and not a general-purpose MCP client: it is scoped to exactly what it
takes to call `telecom-mcp`'s eight tools correctly from a terminal, a script, or
another service's ops tooling.

## Where this fits

```
telecom-mcp-client ──POST /mcp/──► telecom-mcp ──HTTP──► telecom-middleware ──► MongoDB
   (this package)                  (the gateway)          (business logic + data)
```

`telecom-mcp` is the security-enforcing gateway; `telecom-middleware` is the system of
record behind it. This package is a *caller* of `telecom-mcp`, the same relationship a
voice agent or any other MCP host has to it — it holds no business logic and talks to
nothing but the one HTTP endpoint. See `../GUIDE.md`'s architecture section for how all
three pieces relate.

## Install

```bash
uv sync
```

## Run

```bash
export TELECOM_MCP_URL=http://127.0.0.1:8080
export TELECOM_MCP_ACCESS_TOKEN="$(cd ../telecom-mcp && uv run python scripts/mint_dev_token.py)"

uv run telecom-mcp-client list-tools
uv run telecom-mcp-client call get_customer_account --json '{"cx_id": "CX-2001"}'
```

`--base-url` and `--token` flags override the environment variables above; see
`telecom-mcp-client --help`.

## Library use

```python
from telecom_mcp_client import MCPClient, Outcome

async with MCPClient(base_url="http://127.0.0.1:8080", token=token) as client:
    await client.initialize()
    result = await client.call_tool("get_customer_account", {"cx_id": "CX-2001"})

if result.outcome is Outcome.OK:
    ...
elif result.outcome is Outcome.REFUSED:
    # authz/validation failure, or the tool itself said no — an expected outcome,
    # not a crash. result.error.message explains it.
    ...
elif result.outcome is Outcome.TIMEOUT:
    ...
elif result.outcome is Outcome.TRANSPORT_ERROR:
    ...
else:  # Outcome.MALFORMED_RESPONSE
    ...
```

`call_tool` never raises for anything the server or the network did — see
`models.py` for the full `Outcome` reasoning. It only raises `MCPClientError` /
`MCPHandshakeError` for a broken handshake or a programmer error (calling before
`initialize()`).

## What it actually handles

- **The MCP handshake**: `initialize` → `notifications/initialized` → `tools/list` /
  `tools/call`, over streamable HTTP — not hand-waved as "POST some JSON".
- **The trailing slash.** `telecom-mcp`'s README calls out that `POST /mcp` (no slash)
  gets a `307` and most clients silently lose the request body re-POSTing to the
  redirect. This client always POSTs `/mcp/`, so that redirect is never triggered; if
  one comes back anyway (a proxy rewrote the URL), it is treated as a protocol error,
  never followed silently — `httpx.AsyncClient` is built with `follow_redirects=False`.
- **Bearer auth**, from `TELECOM_MCP_ACCESS_TOKEN` / `--token`, matching the auth model
  `telecom-mcp`'s README describes.
- **Connect and read timeouts**, set independently (`--connect-timeout`,
  `--read-timeout`), not one blended number.
- **Retry with exponential backoff and full jitter — but only where retrying is
  safe.** A 4xx (auth, validation) and a tool-level refusal (`isError: true`) fail
  fast and are never retried: the answer will not change. A transport failure (the
  request never reached the server) is retried for any tool. A **timeout is retried
  only for the five read-only tools** (`get_customer_account`, `get_active_services`,
  `get_order_status`, `get_invoice_summary`, `get_network_status`); the three writes
  (`create_support_ticket`, `schedule_callback`, `request_refund_approval`) are never
  auto-retried on a timeout, because the first attempt may still land on the server
  and a second attempt risks a duplicate. See `tools.py` for where that table comes
  from and its limits.
- **A typed outcome, not an exception, for every call.** `ToolCallResult.outcome` is
  one of `OK`, `REFUSED`, `TRANSPORT_ERROR`, `TIMEOUT`, `MALFORMED_RESPONSE` — see
  `models.py`. A caller can branch on what actually happened instead of parsing an
  exception message.

## What it does not do

- No session persistence across process restarts, no connection pooling tuning beyond
  what `httpx` gives for free, no streaming/progress-notification handling — `tools/
  call` is a single request/response as far as this client is concerned.
- No knowledge of `telecom-middleware` at all. It only ever talks to `telecom-mcp`.
- Retrying a write with the *same idempotency key* across attempts (which telecom-mcp's
  three mutating tools support and would make even a timeout-retry safe) is left to the
  caller: this client does not inspect or generate `arguments`, so it cannot thread a
  key through on its own without either guessing at tool-specific argument names or
  adding configuration this package's scope does not call for.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing
```

Tests run against a scripted fake HTTP server (`httpx.MockTransport`, see
`tests/conftest.py`) — no live `telecom-mcp` instance is needed or used.
