# Contributing

## Before you push

```bash
make check          # lint, types, coverage — exactly what CI runs
```

If that passes, CI passes, with one exception: the MongoDB suite needs a replica set
and runs in CI only. To run it locally first:

```bash
make up             # a single-node replica set in Docker
make test-mongo
```

## Where things go

| Change | Where |
|---|---|
| A tool the voice agent calls | `telecom-mcp/` |
| A business rule, an endpoint, anything touching data | `telecom-middleware/` |
| Both, or neither | the root: `docs/`, `infra/`, `e2e/`, `testsprite/` |

The two services are git subtrees with their own history, lock files and CI. Work in
the service directory; the root Makefile only delegates.

## Commits

One change per commit, with its test. The message says what changed and why in the
imperative — `serve: pass an import string when reloading, which is the only thing
uvicorn accepts` — not `fix bug`.

## Decisions

Any non-obvious choice gets a file in `docs/decisions/`, five headings, written when
you make it. `make adr` prints the next number. These are immutable: a decision that
turns out wrong is superseded by a later record, never edited.

## Branches

`development` is where work lands. `staging` and `production` are where it has got to.
`main` tracks production, so a visitor sees what is actually running.
