# Every common action is one command. CI calls these targets and nothing else.
# Run from the project root, or from anywhere with:  make -C /path/to/repo <target>
# CURDIR is quoted everywhere so a path containing spaces still works.
UV := uv

# Runtime commands read .env when it exists; the test targets deliberately do not, so a
# suite never picks up a real cluster and starts writing to it.
# Resolved in the shell rather than with $(wildcard ...), which splits a path on spaces.
define run_with_env
cd "$(CURDIR)" && if [ -f .env ]; then $(UV) run --env-file .env $(1); else $(UV) run $(1); fi
endef
# Override to keep the virtualenv off a slow network mount:
#   export UV_PROJECT_ENVIRONMENT=$HOME/.venvs/telecom-mcp
UV_PROJECT_ENVIRONMENT ?= $(CURDIR)/.venv
export UV_PROJECT_ENVIRONMENT

#: The base image the Dockerfile actually builds from. Read from it rather than
#: repeated here, because a digest recorded for one image and a container built from
#: another is worse than no record at all. See docs/decisions/0004-container-base.md.
BASE_IMAGE := $(shell awk '/^FROM /{print $$2; exit}' "$(CURDIR)/Dockerfile")

.DEFAULT_GOAL := help
.PHONY: help install lock-check dev serve check-store migrate seed export-seed test test-fast test-int lint format typecheck check cov cov-mongo build clean audit docker-build docker-smoke

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-13s %s\n", $$1, $$2}'

install: ## Install everything needed to work on this, from the lock file
	cd "$(CURDIR)" && $(UV) sync --frozen --all-extras

lock-check: ## Fail if uv.lock no longer matches pyproject.toml
	cd "$(CURDIR)" && $(UV) lock --check

dev: ## Run the API locally with reload (reads .env)
	@$(call run_with_env,telecom-middleware serve --reload)

serve: ## Run the API without reload, the way the container does (reads .env)
	@$(call run_with_env,telecom-middleware serve)

test: ## Run the full test suite
	cd "$(CURDIR)" && $(UV) run pytest

test-fast: ## Run only the fast tests (no containers)
	cd "$(CURDIR)" && $(UV) run pytest tests/unit tests/contract

test-int: ## Run the tests that need a real MongoDB replica set
	cd "$(CURDIR)" && $(UV) run pytest tests -m mongo

check-store: ## Is the configured database reachable, a replica set, and indexed
	@$(call run_with_env,telecom-middleware check-store)

migrate: ## Create collections, validators and indexes
	@$(call run_with_env,telecom-middleware migrate)

seed: ## Load the demo dataset into the configured MongoDB
	@$(call run_with_env,telecom-middleware seed)

export-seed: ## Regenerate the mongosh seed script from the code
	cd "$(CURDIR)" && $(UV) run python scripts/export_seed.py

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

cov-mongo: ## Coverage including the MongoDB adapter; needs a real replica set
	cd "$(CURDIR)" && $(UV) run pytest -m "mongo or not mongo" --cov \
		--cov-config=coverage-mongo.toml --cov-report=term-missing --cov-report=xml

check: lock-check lint typecheck cov ## Exactly what CI runs

build: ## Produce the deployable artifact
	cd "$(CURDIR)" && $(UV) build

docker-build: ## Build the container image
	cd "$(CURDIR)" && docker build -t telecom-middleware:local .
	mkdir -p "$(CURDIR)/dist"
	# BuildKit, which is the default builder, keeps base layers in its own cache rather
	# than the image store. So after a successful build `python:3.12-slim-bookworm` is
	# not in `docker images` at all, and inspecting it fails with "No such image". The
	# pull is what puts it there, with the RepoDigests the next line reads.
	docker pull -q $(BASE_IMAGE)
	docker image inspect $(BASE_IMAGE) --format '{{index .RepoDigests 0}}' > "$(CURDIR)/dist/base-image-digest.txt"
	@echo "base image digest recorded in dist/base-image-digest.txt"

docker-smoke: docker-build ## Start the built image and prove it answers readiness
	cd "$(CURDIR)" && ./scripts/docker_smoke.sh

clean: ## Remove build output and caches
	cd "$(CURDIR)" && rm -rf dist build .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find "$(CURDIR)" -name __pycache__ -type d -prune -exec rm -rf {} +
