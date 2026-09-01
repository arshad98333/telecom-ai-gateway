# The workspace's front door. Every common action is one command, and the command is
# the same on a laptop, a Linux CI runner and a container - nothing here is PowerShell.
#
#     make            list the targets
#     make install    both services, from their lock files
#     make check      exactly what CI runs, across both services
#
# Each service keeps its own Makefile and remains usable on its own; this delegates
# rather than duplicating, so there is one definition of `test` per service and one
# place to change it. A service directory that is not checked out is skipped with a
# note rather than failing the run, which is what makes a partial clone workable.
#
# CURDIR is quoted everywhere so a path containing spaces still works.

SHELL := /bin/sh

#: Discovered, not hardcoded, so adding a third service is one directory.
SERVICES := $(patsubst %/Makefile,%,$(wildcard */Makefile))

PYTHON ?= python
UV ?= uv

.DEFAULT_GOAL := help

.PHONY: help setup setup-local local demo dev seed tooling install test test-fast test-int test-mongo \
        lint format typecheck cov check audit build clean docker-build docker-smoke \
        up down logs docker-mongo docker-middleware docker-mcp \
        run-middleware run-mcp token health client-tools client-call \
        testable testsprite-preflight testsprite-setup testsprite-create testsprite-smoke \
        stamp validate wire-auth0 wire-auth0-activate adr

# --- the fan-out ---------------------------------------------------------------------
# One recipe, one meaning. $(1) is the target to run in each service.
define for_each_service
@for service in $(SERVICES); do \
	if [ -f "$(CURDIR)/$$service/Makefile" ]; then \
		echo ""; echo "==> $$service: $(1)"; \
		$(MAKE) -C "$(CURDIR)/$$service" $(1) || exit $$?; \
	else \
		echo "--- $$service: not checked out, skipping"; \
	fi; \
done
endef

help: ## Show this help
	@echo "Services: $(SERVICES)"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-22s %s\n", $$1, $$2}'

tooling: ## Fail early if a required tool is missing
	@missing=""; \
	for tool in $(UV) $(PYTHON) git; do \
		command -v $$tool >/dev/null 2>&1 || missing="$$missing $$tool"; \
	done; \
	if [ -n "$$missing" ]; then echo "not on PATH:$$missing"; exit 1; fi; \
	echo "uv     $$($(UV) --version)"; \
	echo "python $$($(PYTHON) --version 2>&1)"

# --- getting started --------------------------------------------------------------------
setup: ## First run: .env files, one shared dev secret, dependencies, a config check
	@$(PYTHON) "$(CURDIR)/scripts/setup.py"

setup-local: ## Re-apply the local profile (local verifier, fake backend, no Auth0)
	@$(PYTHON) "$(CURDIR)/scripts/local_env.py"

local: setup-local ## Alias for setup-local

demo: ## Everything in Docker, seeded, on http://localhost:9000
	cd "$(CURDIR)" && docker compose up -d --wait --wait-timeout 240
	cd "$(CURDIR)" && docker compose exec -T middleware telecom-middleware seed
	@echo ""
	@echo "  middleware   http://localhost:9000/readyz"
	@echo "  tool server  http://localhost:8080/readyz"
	@echo "  stop it      make down"

dev: ## Print the two commands that run the services on your machine
	@echo "Two terminals:"
	@echo "  make run-middleware    # the API,  :9000"
	@echo "  make run-mcp           # the tools, :8080"
	@echo ""
	@echo "Then: make token && make client-tools"

run-middleware: ## Run the API locally with reload (reads telecom-middleware/.env)
	$(MAKE) -C "$(CURDIR)/telecom-middleware" dev

run-mcp: ## Run the MCP tool server over HTTP (reads telecom-mcp/.env)
	$(MAKE) -C "$(CURDIR)/telecom-mcp" serve-http

token: ## Print a local development bearer token for MCP calls
	@cd "$(CURDIR)/telecom-mcp" && $(UV) run --env-file .env python scripts/mint_dev_token.py

health: ## Probe readiness on :9000 and :8080
	@command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }
	@echo "middleware:"; curl -fsS "http://127.0.0.1:9000/readyz" | $(PYTHON) -m json.tool
	@echo "mcp:"; curl -fsS "http://127.0.0.1:8080/readyz" | $(PYTHON) -m json.tool

client-tools: ## List MCP tools (needs TELECOM_MCP_ACCESS_TOKEN or: eval $$(make token)) 
	@test -n "$$TELECOM_MCP_ACCESS_TOKEN" || { echo "run: export TELECOM_MCP_ACCESS_TOKEN=$$(make -s token)"; exit 2; }
	@cd "$(CURDIR)/telecom-mcp-client" && TELECOM_MCP_URL="$${TELECOM_MCP_URL:-http://127.0.0.1:8080}" \
	  $(UV) run telecom-mcp-client list-tools

client-call: ## Call get_customer_account for CX-1234 (override TOOL and JSON)
	@test -n "$$TELECOM_MCP_ACCESS_TOKEN" || { echo "run: export TELECOM_MCP_ACCESS_TOKEN=$$(make -s token)"; exit 2; }
	@cd "$(CURDIR)/telecom-mcp-client" && TELECOM_MCP_URL="$${TELECOM_MCP_URL:-http://127.0.0.1:8080}" \
	  $(UV) run telecom-mcp-client call "$${TOOL:-get_customer_account}" \
	  --json "$${JSON:-{\"cx_id\": \"CX-1234\"}}"

seed: ## Load the demo dataset into whatever the middleware is configured to use
	$(MAKE) -C "$(CURDIR)/telecom-middleware" seed

# --- the per-service targets ----------------------------------------------------------
install: tooling ## Install both services from their lock files
	$(call for_each_service,install)

test: ## Run both full test suites
	$(call for_each_service,test)

test-fast: ## Run only the fast tests (no containers)
	$(call for_each_service,test-fast)

test-int: ## Run the integration tests (needs the services' dependencies)
	$(call for_each_service,test-int)

lint: ## Check style and common mistakes
	$(call for_each_service,lint)

format: ## Fix style automatically
	$(call for_each_service,format)

typecheck: ## Check types
	$(call for_each_service,typecheck)

cov: ## Run tests with each service's coverage gate
	$(call for_each_service,cov)

audit: ## Fail on known-vulnerable dependencies
	$(call for_each_service,audit)

build: ## Produce both deployable artifacts
	$(call for_each_service,build)

docker-build: ## Build both container images
	$(call for_each_service,docker-build)

docker-smoke: ## Start each built image and prove it answers readiness
	$(call for_each_service,docker-smoke)

clean: ## Remove build output and caches
	$(call for_each_service,clean)

check: tooling ## Exactly what CI runs, across both services
	$(call for_each_service,check)

# The MongoDB suite needs a real replica set, so it is not part of `check`: it runs in
# CI against an ephemeral one (.github/workflows/mongo.yml), and locally against
# whatever `docker compose up -d mongo` gives you.
test-mongo: ## Run the MongoDB-backed tests (needs a replica set on TELECOM_MW_MONGO_URI)
	@if [ ! -f "$(CURDIR)/telecom-middleware/Makefile" ]; then \
		echo "telecom-middleware is not checked out."; exit 1; fi
	$(MAKE) -C "$(CURDIR)/telecom-middleware" test-int

# --- the local stack -------------------------------------------------------------------
up: ## Start the local stack (MongoDB and friends) in the background
	cd "$(CURDIR)" && docker compose up -d

down: ## Stop the local stack
	cd "$(CURDIR)" && docker compose down

logs: ## Follow the local stack's logs
	cd "$(CURDIR)" && docker compose logs -f

docker-mongo: ## Start only MongoDB (replica set on :27017)
	cd "$(CURDIR)" && docker compose up -d mongo

docker-middleware: ## Start MongoDB and the API image (:9000)
	cd "$(CURDIR)" && docker compose up -d mongo middleware

docker-mcp: ## Start MongoDB, API, and tool server (:8080)
	cd "$(CURDIR)" && docker compose up -d mongo middleware tools

# --- external testing ------------------------------------------------------------------
testable: ## Bring both services up in the external-test profile and mint a token
	cd "$(CURDIR)" && $(PYTHON) testsprite/start_testable.py

testsprite-preflight: ## Is the TestSprite CLI installed and authenticated
	cd "$(CURDIR)" && $(PYTHON) testsprite/run_testsprite.py preflight

testsprite-setup: ## Create both TestSprite projects (needs MCP_URL and MIDDLEWARE_URL)
	@test -n "$(MCP_URL)" || { echo "set MCP_URL=https://..."; exit 2; }
	@test -n "$(MIDDLEWARE_URL)" || { echo "set MIDDLEWARE_URL=https://..."; exit 2; }
	cd "$(CURDIR)" && $(PYTHON) testsprite/run_testsprite.py setup \
		--mcp-url "$(MCP_URL)" --middleware-url "$(MIDDLEWARE_URL)"

testsprite-create: ## Upload the test files (add FROM_BUILD=1 for the stamped copies)
	cd "$(CURDIR)" && $(PYTHON) testsprite/run_testsprite.py create $(if $(FROM_BUILD),--from-build,)

testsprite-smoke: ## Run three tests, not eighteen
	cd "$(CURDIR)" && $(PYTHON) testsprite/run_testsprite.py smoke

stamp: ## Resolve TARGET_URL to a literal in build/, for a TestSprite upload only
	@test -n "$(MCP_URL)" || { echo "set MCP_URL=https://..."; exit 2; }
	@test -n "$(MIDDLEWARE_URL)" || { echo "set MIDDLEWARE_URL=https://..."; exit 2; }
	cd "$(CURDIR)/testsprite" && $(PYTHON) stamp_target_url.py "$(MCP_URL)" "$(MIDDLEWARE_URL)"

validate: ## Dry-run the TestSprite suites locally, before spending a credit
	cd "$(CURDIR)/testsprite" && $(UV) run --project ../e2e python validate_locally.py

# --- identity ---------------------------------------------------------------------------
wire-auth0: ## Write the Terraform outputs into both .env files
	cd "$(CURDIR)" && $(PYTHON) infra/auth0/scripts/wire_env.py

wire-auth0-activate: ## ...and switch both services from the local verifier onto Auth0
	cd "$(CURDIR)" && $(PYTHON) infra/auth0/scripts/wire_env.py --activate

# --- documentation ------------------------------------------------------------------------
adr: ## Print the command that starts the next architecture decision record
	@highest=$$(ls docs/decisions/[0-9][0-9][0-9][0-9]-*.md 2>/dev/null \
	  | sed 's|.*/||; s|-.*||' | sort -n | tail -1); \
	highest=$$(echo "$${highest:-0}" | sed 's/^0*//'); \
	next=$$(printf '%04d' $$(( $${highest:-0} + 1 ))); \
	echo "cp docs/decisions/0000-template.md docs/decisions/$$next-a-short-title.md"
