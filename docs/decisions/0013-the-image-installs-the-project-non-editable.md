# 13. The container installs the project non-editable, and a smoke test proves it

## Context

Both Dockerfiles build in two stages: a builder that runs `uv sync`, and a runtime stage
that copies `/app/.venv` and nothing else. That is the standard uv-in-Docker shape and it
produces a small image with no build tooling in it.

`uv sync` installs the workspace project as an **editable** install by default. Editable
means site-packages holds a `.pth` file pointing at `/app/src` rather than the package
itself. In the builder that is invisible, because `/app/src` is right there. In the runtime
stage `/app/src` does not exist, so the image starts, finds its console script on `PATH`,
and dies at the first import:

    File "/app/.venv/bin/telecom-mcp", line 4, in <module>
        from telecom_mcp.api.cli import main
    ModuleNotFoundError: No module named 'telecom_mcp'

This shipped. It was found by the release workflow's own smoke step, after the image had
been built, signed and pushed to the registry, which cost version 1.1.0.

## Decision

Both Dockerfiles pass `--no-editable` to `uv sync`, which installs the built package into
the virtual environment. The runtime stage's copy of `/app/.venv` is then self-contained.

Every image that is published is started and asked to validate its own configuration
before the job is allowed to pass, in the release workflow and in `make docker-smoke`.

## Alternatives considered

Copying `/app/src` into the runtime stage as well. Rejected: it makes the image carry a
second copy of the code that nothing imports once this is fixed properly, and it leaves the
`.pth` indirection in a production image for no reason.

Installing the wheel with `pip install dist/*.whl` in the runtime stage. Rejected: it
duplicates dependency resolution the lock file already settled, and loses `--frozen`.

Trusting the build. Rejected on evidence. The build succeeded, the layers pushed, the
provenance was signed, and the image could not import itself. Nothing before the smoke step
would have caught it.

## Consequences

The failure mode this closes is the expensive shape: everything green until the artifact is
already published. The smoke step is cheap (`docker run ... check-config`) and it is the
only thing between a broken image and a `:latest` tag.

`--no-editable` is load-bearing rather than cosmetic, so it is commented as such in both
Dockerfiles. A future edit that drops it will pass every test that does not run the image.

A version number spent on this cannot be recovered: 1.1.0's image is broken, and the fix
ships as 1.1.1 with an identical wheel.

## Status

Accepted.
