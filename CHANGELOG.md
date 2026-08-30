# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
