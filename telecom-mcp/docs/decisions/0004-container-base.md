# 4. The container base image must be pinned by digest before production

## Context

The Dockerfile currently references `python:3.12-slim-bookworm` by tag. A tag is
mutable: the same Dockerfile can produce a different image tomorrow, which breaks the
promise that a released artifact can be rebuilt and inspected months later.

## Decision

Ship the repository with the tag, and require the digest form before the first
production release. `make docker-build` writes the resolved digest to
`dist/base-image-digest.txt` so the substitution is a copy rather than a hunt, and CI
records the digest with the build provenance.

## Alternatives considered

Pinning a digest now, in this repository. Rejected only because the digest must be
resolved against the registry the deployment actually pulls from; a digest copied from
elsewhere is worse than an honest tag, because it looks pinned and is not verifiable
here.

Rebuilding for each environment. Rejected: production would then run an image that was
never tested. The artifact is built once and promoted.

## Consequences

Until the substitution is made, the image is reproducible only to the tag. This is
recorded in the README's known gaps so it cannot be forgotten, and the release
checklist blocks on it.

## Status

Accepted, with the substitution outstanding.
