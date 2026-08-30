# syntax=docker/dockerfile:1.9

# A moving base tag means the image can change without the code changing, and that is a
# rollback nobody can reason about. Before the first production release, replace both
# python:3.12-slim-bookworm references with the digest form
# (python@sha256:...) and update them deliberately, in their own commit.
# `make docker-build` records the resolved digest in dist/base-image-digest.txt so the
# substitution is a copy, not a hunt. Tracked in docs/decisions/0004-container-base.md.

# --- builder ------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, from the lock file only, so a code change does not invalidate
# the dependency layer. --frozen fails if the lock file is out of date rather than
# quietly resolving something different from what was tested.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra redis --extra http

COPY src/ ./src/
COPY README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra redis --extra http

# --- runtime ------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# A non-privileged user with no shell and no home to write to.
RUN groupadd --system --gid 10001 telecom \
 && useradd --system --uid 10001 --gid telecom --no-create-home --shell /usr/sbin/nologin telecom \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder --chown=root:root /app/.venv /app/.venv

USER 10001:10001

EXPOSE 8080

# The health check calls readiness, which is the endpoint that consults dependencies.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=2).status==200 else 1)"]

ENTRYPOINT ["telecom-mcp"]
CMD ["serve", "--transport", "http"]
