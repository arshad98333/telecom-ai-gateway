# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.0] - 2026-09-01

### Packaging

- **This package is no longer distributed on PyPI or TestPyPI.** The only artifact that
  leaves this repository is a container image. A push to `production` (or a manual
  dispatch) builds `ghcr.io/arshad98333/telecom-mcp-tools` and
  `ghcr.io/arshad98333/telecom-middleware`, boots each image and checks `/readyz`,
  `/healthz` and `/metrics` against it, then publishes multi-architecture images with an
  SBOM and signed provenance and prints the digest to promote. Deployments pull that
  digest; they do not rebuild, and there is nothing to `pip install`.
- Removed the PyPI-only trove classifiers from `pyproject.toml`, the `pip install` and
  `uvx --from` instructions from the READMEs, the PyPI publishing runbook from
  `GUIDE.md`, and `scripts/consumer_check.sh`, whose only job was validating the
  published package.

### About this version number

There are no release tags any more — images are tagged by short commit SHA and
`production`, and nothing in CI reads or gates on `version` in `pyproject.toml`. The
field is kept because it is not decorative: it flows through `importlib.metadata` into
`telecom_mcp.__version__`, which is what `/healthz` and `/readyz` report, and the
equivalent field labels the middleware's new `telecom_middleware_info` gauge. So this
bump names the build a running container reports itself as. It does not name anything
published anywhere.

## [1.1.1] - 2026-09-01

### Fixed

- The container image started, found its console script, and died on
  `ModuleNotFoundError: No module named 'telecom_mcp'`. `uv sync` installs the project
  as an *editable* install by default, so site-packages held a `.pth` pointing at
  `/app/src` rather than the package itself; the runtime stage copies only `/app/.venv`,
  where `/app/src` does not exist. Both Dockerfiles now pass `--no-editable`, which puts
  the real package in the virtual environment and makes it self-contained.

  Nothing in the distribution changed: the wheel and the sdist for 1.1.0 and 1.1.1 are
  the same code. This version exists because 1.1.0's image is broken and a published
  version number cannot be reused.


## [1.1.0] - 2026-08-31

### Changed

- `tools/list` no longer answers an unverifiable token with an empty catalogue. No
  token at all still returns `[]` - an anonymous caller learns nothing, including
  whether any tool exists - but a token that fails verification now returns a
  `token_invalid` error naming the reason. The silence was defended on the grounds that
  the catalogue should not leak whether a name exists; it does not leak anything to
  tell a caller its own credential is bad, and the silence cost an external test run an
  afternoon and a full set of credits, filed as a broken v1 contract. Scope narrowing
  stays silent: a tool omitted for want of a permission is still not announced.

  **Upgrading:** a client that treats `[]` as "no tools for me" now sees an error
  instead when its token is the problem. That is the point, but it is an observable
  change to the error path.

### Packaging

- Licence declared as an SPDX expression with `license-files`, replacing the free-text
  field and the `License :: OSI Approved` classifier PyPI has deprecated.
- The sdist's file list is an allow-list with an unconditional `.env` exclusion on top,
  so a populated environment file in a working directory cannot reach a public index
  even if someone builds from one. The release workflow asserts this on the artifact
  itself rather than trusting the configuration.


## [1.0.0] - 2026-08-30

The first release: the version 1 tool contract, frozen.

### Added

- Eight MCP tools — five reads, two low-risk writes, and one restricted approval
  request — with frozen input and output schemas, per-tool timeouts, retry rules,
  idempotency requirements and audit fields.
- An eight-stage authorization kernel that no tool call can bypass: tool scope, token,
  tenant, CX ID, account ownership, role, permission, input schema.
- Token verification against a JWKS document (RS256, Auth0-shaped) with key caching, a
  single refresh on key rotation, and a bounded stale-if-error window; plus a local
  HS256 verifier so development and the test suite need no identity provider.
- Mandatory idempotency keys on every write, with a three-state store — new, in
  progress, completed — backed by memory for a single replica or Redis across replicas.
- Reliability layer: a total time budget per call, bounded exponential backoff with
  downward jitter, retries only on operations declared safe to repeat, and a circuit
  breaker per backend route.
- A tamper-evident audit trail: every accepted and rejected call, hash-chained, with
  the customer reference stored as a stable pseudonym. `telecom-mcp verify-audit`
  verifies a log in one pass.
- Redaction applied as a logging processor and to every tool result, removing
  passcodes, passwords, payment secrets and access tokens, including when they appear
  inside free text.
- Both transports: stdio and streamable HTTP, with `/healthz`, `/readyz` and `/metrics`.
- A fixture-driven fake backend that enforces tenant isolation and can be told to time
  out, fail, return an unparseable shape, or go unready — so failure paths are tested
  rather than imagined.
- Structured JSON logging with a correlation identifier on every line, and a metrics
  registry with a fixed low-cardinality label set.

### Security

- Restricted tools (`change_service_plan`, `cancel_service`) are declared but have no
  executable path in this version.
- Cross-account denials are byte-identical to plain permission denials, so the
  difference cannot be used to discover whether another account exists.
- Account ownership fails closed: with no checker configured, nothing is permitted.
- An over-broad scope in a token cannot exceed what the identity's role may hold.
- Production configuration refuses the fake backend, the local verifier and the
  in-memory idempotency store.

[1.0.0]: https://example.invalid/telecom-mcp-tools/releases/tag/v1.0.0
