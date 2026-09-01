UV := uv
NPM := npm

.PHONY: help install dev build lint preview test-e2e

help: ## Show targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n", $$1, $$2}'

install: ## npm ci and build
	$(NPM) ci
	$(NPM) run build

dev: ## Vite dev server on :5173
	$(NPM) run dev

build: ## Production bundle
	$(NPM) ci
	$(NPM) run build

lint: ## Typecheck
	$(NPM) run lint

preview: ## Preview production build
	$(NPM) run preview

test-e2e: ## Playwright smoke test (console UI loads)
	$(NPM) run test:e2e
