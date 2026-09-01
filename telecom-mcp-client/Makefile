# Same one-command-per-action convention as ../telecom-mcp and ../telecom-middleware.
# Run from this directory, or from anywhere with: make -C /path/to/telecom-mcp-client <target>
UV := uv

UV_PROJECT_ENVIRONMENT ?= $(CURDIR)/.venv
export UV_PROJECT_ENVIRONMENT

.DEFAULT_GOAL := help

.PHONY: help install lock-check test lint format typecheck cov check build clean

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

check: lock-check lint typecheck cov ## Exactly what CI runs

build: ## Produce the deployable artifact
	cd "$(CURDIR)" && $(UV) build

clean: ## Remove build and cache artifacts
	cd "$(CURDIR)" && rm -rf dist .venv .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage
