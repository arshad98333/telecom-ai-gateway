# 11. Test targets come from `TARGET_URL`, and only an upload gets a literal

## Context

The TestSprite suites took their target from a `BASE_URL` string literal in each file,
and `stamp_target_url.py` rewrote that literal across all twenty-three before an upload.
Rotating a tunnel meant rewriting source files. A test harness that edits code to choose
an environment is a harness that can ship the wrong environment.

The reason it was built that way is real and has not gone away: TestSprite's V3 backend
sandbox validates an uploaded file before it makes a single request, and rejects one
whose base URL is not a literal. The failure bundle says so in as many words.

## Decision

The sources read the target from the environment:

    BASE_URL = os.environ.get("TARGET_URL", "http://127.0.0.1:9100").rstrip("/")

That is the path for a laptop, for CI, and for the local dry run — set `TARGET_URL`, or
set nothing and get the local dev server. `stamp_target_url.py` survives with one job
only: resolving that expression to a literal into `build/` for a TestSprite upload. It
writes to `build/`; it never edits a source.

## Alternatives considered

Removing the stamping step entirely, as first proposed. Rejected on evidence: the
uploaded file would fail the runner's own validation, and discovering that costs
credits.

Keeping the literals and stamping for every runner. Rejected: it is the current problem.

Passing the target as a pytest fixture or CLI argument. Rejected: these files are
executed top to bottom by a sandbox that injects only a credential block. They are not
pytest tests and cannot take a fixture.

## Consequences

`validate_locally.py` now sets `TARGET_URL` and executes each file unmodified, so the
dry run exercises the same bytes a developer runs. The one place the bytes differ is the
upload, and that difference is a single generated line in `build/`.

The stamping regex matches both the environment-driven assignment and an already-resolved
literal, so re-stamping a build directory is idempotent.

## Status

Accepted.
