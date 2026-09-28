COMPOSE ?= docker compose
PYTHON ?= python3
NPM ?= npm
NODE ?= node
API_URL ?= http://localhost:8000
WEB_URL ?= http://localhost:3000

.PHONY: up down test test-backend test-verifiers test-mcp test-chatops smoke tools build ci
up:
	$(COMPOSE) up --build -d --wait
down:
	$(COMPOSE) --profile ollama down
build:
	$(COMPOSE) build
tools:
	$(NPM) ci --prefix tools/mcp --ignore-scripts
test-verifiers:
	$(PYTHON) -m unittest discover -s tests -p 'test_verify*.py' -v
test-backend:
	$(COMPOSE) run --rm --no-deps -e RUN_DB_TESTS=1 -e TEST_SCHEMA_PATH=/tmp/init.sql -v "$(CURDIR)/infra/db/init.sql:/tmp/init.sql:ro" api python -m unittest discover -s tests -v
test-mcp:
	$(NPM) --prefix tools/mcp test
	$(NODE) tools/mcp/smoke.mjs
test-chatops:
	$(PYTHON) -m pytest chatops-bot/tests -q -p no:cacheprovider
	$(PYTHON) -B -m pytest -c /dev/null -p no:cacheprovider tests/milestones/day5 -q
test: test-verifiers test-backend test-mcp
smoke:
	$(PYTHON) scripts/verify.py smoke --api-url "$(API_URL)" --web-url "$(WEB_URL)"
ci:
	$(MAKE) up
	$(MAKE) test
	$(MAKE) smoke
# CI callers must always run down (also on failure). Volumes are preserved here.
