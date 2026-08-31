# 10. Every entry point is `make` and Python, not PowerShell

## Context

The workspace grew a set of PowerShell scripts — `start-testable.ps1`,
`run-testsprite.ps1`, `wire_env.ps1` — plus VS Code workspace tasks. They worked, on one
operating system, for people who had that editor open. A Linux CI runner could not call
any of them, and neither could a container, so CI grew its own copies of the same steps.
Two definitions of "run the tests" is one too many, and the one that drifts is always
the one a human runs.

## Decision

The workspace has a root `Makefile` with the standard targets — `install`, `test`,
`check`, `clean` — and each is one command that works the same on a laptop, a CI runner
and a container. Anything with real logic behind it is Python, invoked by the Makefile.
The root Makefile delegates to each service's own Makefile rather than restating it.

## Alternatives considered

Keeping the `.ps1` scripts and adding shell equivalents. Rejected: two implementations
of each script is the drift problem with extra steps.

A task runner (just, task, invoke). Rejected: make is already installed everywhere this
runs, and the services already had Makefiles that CI called.

Porting the scripts to shell rather than Python. Rejected: they parse `.env` files, poll
health endpoints and shell out to a CLI with JSON output — all of which shell does
badly, and Python is a hard dependency of this project anyway.

## Consequences

`make check` is exactly what CI runs, across both services, and a service directory that
is not checked out is skipped with a note rather than failing the run. The old `.ps1`
files remain as one-line shims that forward to the Python and say they are deprecated,
so an old bookmark still works.

Service-internal helper scripts (`telecom-mcp/scripts/*.ps1`) are not covered by this
and remain platform-specific until each is next touched.

## Status

Accepted.
