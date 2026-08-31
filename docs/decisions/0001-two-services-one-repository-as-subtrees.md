# 1. The two services live in one repository, as subtrees

## Context

`telecom-mcp` and `telecom-middleware` began as separate repositories with separate
histories. They are released separately and could stay apart, but almost every change
that matters touches both: a tool contract, an authorization stage, a claim name. Two
repositories meant two pull requests that had to land in an order nobody could enforce,
and a bisect that could not cross the boundary.

## Decision

Both services live in this repository as git subtrees, keeping the commits they arrived
with. `git log -- telecom-mcp` is still the real story of that service.

## Alternatives considered

Submodules. Rejected: a submodule pointer is a second thing to update, and a clone that
forgets `--recursive` looks like an empty directory rather than an error.

A monorepo built by copying the files in. Rejected: it throws away the history, which is
where the reasons live.

Leaving them apart and coordinating by release. Rejected: it makes a cross-cutting
change a scheduling problem, and the schedule is what slipped.

## Consequences

One clone, one `make check`, one CI run that can assert across the pair. Each service
keeps its own Makefile, lock file, Dockerfile and CI, so it is still usable alone and
can still be pushed back to its own remote with `git subtree push`.

The cost is that the root is not a Python project and never becomes one: the root
Makefile delegates and holds nothing itself, and a change to a service is made in that
service's directory, not at the top.

## Status

Accepted.
