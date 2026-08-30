# Every common action is one command. CI calls these targets and nothing else.
# Run from the project root, or from anywhere with:  make -C /path/to/repo <target>
# CURDIR is quoted everywhere so a path containing spaces still works.
UV := uv
# Override to keep the virtualenv off a slow network mount:
#   export UV_PROJECT_ENVIRONMENT=$HOME/.venvs/telecom-mcp
UV_PROJECT_ENVIRONMENT ?= $(CURDIR)/.venv
export UV_PROJECT_ENVIRONMENT

.DEFAULT_GOAL := help
.PHONY: help install dev test test-fast test-int lint format typecheck check cov build clean audit docker-build docker-smoke

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-13s %s\n", $$1, $$2}'

install: ## Install everything needed to work on this, from the lock file
	cd "$(CURDIR)" && $(UV) sync --frozen --all-extras

dev: ## Run the MCP server locally over stdio
	cd "$(CURDIR)" && $(UV) run telecom-mcp serve --transport stdio

serve-http: ## Run the MCP server locally over streamable HTTP
	cd "$(CURDIR)" && $(UV) run telecom-mcp serve --transport http

test: ## Run the full test suite
	cd "$(CURDIR)" && $(UV) run pytest

test-fast: ## Run only the fast tests (no containers)
	cd "$(CURDIR)" && $(UV) run pytest tests/unit tests/contract

test-int: ## Run the integration tests
	cd "$(CURDIR)" && $(UV) run pytest tests/integration -m integration

lint: ## Check style and common mistakes
	cd "$(CURDIR)" && $(UV) run ruff check . && $(UV) run ruff format --check .

format: ## Fix style automatically
	cd "$(CURDIR)" && $(UV) run ruff format . && $(UV) run ruff check --fix .

typecheck: ## Check types
	cd "$(CURDIR)" && $(UV) run mypy

cov: ## Run tests with the coverage gate
	cd "$(CURDIR)" && $(UV) run pytest --cov --cov-report=term-missing --cov-report=xml

audit: ## Fail on known-vulnerable dependencies
	cd "$(CURDIR)" && $(UV) run --with pip-audit pip-audit --strict

check: lint typecheck cov ## Exactly what CI runs

build: ## Produce the deployable artifact
	cd "$(CURDIR)" && $(UV) build

docker-build: ## Build the container image
	cd "$(CURDIR)" && docker build -t telecom-mcp-tools:local .
	mkdir -p "$(CURDIR)/dist"
	docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}' > "$(CURDIR)/dist/base-image-digest.txt"
	@echo "base image digest recorded in dist/base-image-digest.txt"

docker-smoke: docker-build ## Start the built image and prove it answers readiness
	cd "$(CURDIR)" && ./scripts/docker_smoke.sh

clean: ## Remove build output and caches
	cd "$(CURDIR)" && rm -rf dist build .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find "$(CURDIR)" -name __pycache__ -type d -prune -exec rm -rf {} +
