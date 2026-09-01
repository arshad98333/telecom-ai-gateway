# Same one-command-per-action convention as ../telecom-mcp and ../telecom-middleware.
# Run from this directory, or from anywhere with: make -C /path/to/telecom-mcp-client <target>
UV := uv

UV_PROJECT_ENVIRONMENT ?= $(CURDIR)/.venv
export UV_PROJECT_ENVIRONMENT

.DEFAULT_GOAL := help

.PHONY: help install lock-check test lint format typecheck cov check audit build clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-13s %s\n", $$1, $$2}'

install: ## Install everything needed to work on this, from the lock file
	cd "$(CURDIR)" && $(UV) sync --frozen --all-extras

lock-check: ## Fail if uv.lock no longer matches pyproject.toml
	cd "$(CURDIR)" && $(UV) lock --check

test: ## Run the test suite (a scripted fake HTTP server; no live telecom-mcp needed)
	cd "$(CURDIR)" && $(UV) run pytest

lint: ## Check style and common mistakes
	cd "$(CURDIR)" && $(UV) run ruff check . && $(UV) run ruff format --check .

format: ## Fix style automatically
	cd "$(CURDIR)" && $(UV) run ruff format . && $(UV) run ruff check --fix .

typecheck: ## Check types
	cd "$(CURDIR)" && $(UV) run mypy

cov: ## Run tests with the coverage gate
	cd "$(CURDIR)" && $(UV) run pytest --cov --cov-report=term-missing --cov-report=xml

audit: ## Fail on known-vulnerable dependencies
	# Audits the locked dependency set rather than the installed environment. The
	# project itself is an editable install and is not on the index, and both --strict
	# and --skip-editable turn that into an error, so a plain `pip-audit --strict`
	# fails for a reason that has nothing to do with a vulnerability.
	cd "$(CURDIR)" && $(UV) export --frozen --all-extras --no-dev --no-emit-project \
	    --format requirements-txt -q -o .audit-requirements.txt && \
	  { $(UV) run --with pip-audit pip-audit --strict -r .audit-requirements.txt; \
	    status=$$?; rm -f .audit-requirements.txt; exit $$status; }

check: lock-check lint typecheck cov ## Exactly what CI runs

build: ## Produce the deployable artifact
	cd "$(CURDIR)" && $(UV) build

clean: ## Remove build and cache artifacts
	cd "$(CURDIR)" && rm -rf dist .venv .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage
