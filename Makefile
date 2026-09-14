PNPM ?= npx --yes pnpm@10.33.0
PYTHON ?= python3.12
VENV_PY := $(CURDIR)/.venv/bin/python
RUNTIME := $(CURDIR)/incidara_agents/agents/claude-agent
MCP := $(CURDIR)/incidara_agents/mcp_servers

.PHONY: install test build config services check verify up down

install:
	cd console && $(PNPM) install --frozen-lockfile
	cd $(RUNTIME) && npm ci
	test -x $(VENV_PY) || $(PYTHON) -m venv .venv
	$(VENV_PY) -m pip install -q --upgrade pip
	$(VENV_PY) -m pip install -q pytest psycopg2-binary pexpect requests 'fastmcp>=3.0.0' paramiko pyyaml pandas joblib sqlalchemy jinja2

test:
	cd console && $(PNPM) test
	cd $(RUNTIME) && npm test
	cd $(MCP)/agent-evidence && $(VENV_PY) -m pytest tests/test_db.py -q
	cd $(MCP)/agent-feedback && $(VENV_PY) -m pytest tests/test_db.py tests/test_reconciliation_db.py -q
	cd $(MCP)/node-operations && $(VENV_PY) -m pytest tests --ignore=tests/test_smoke.py --ignore=tests/integration -q
	cd $(MCP)/patrol-cron && $(VENV_PY) -m pytest tests --ignore=tests/test_smoke.py --ignore=tests/test_feedback_scenarios_integration.py --ignore=tests/test_feedback_workflow_integration.py -q
	$(VENV_PY) -m pytest tests/unit_tests -q
	$(PYTHON) -m compileall -q incidara_agents

build:
	cd console && $(PNPM) --filter client build && $(PNPM) --filter server build
	cd $(RUNTIME) && npm run build

config:
	test -f compose/config.yaml || cp compose/config.yaml.example compose/config.yaml
	$(VENV_PY) compose/render.py --check
	$(VENV_PY) compose/render.py
	cd compose/rendered && docker compose -f docker-compose.yml config -q

services: config
	@cd compose/rendered && docker compose -f docker-compose.yml config --services

check: config
	bash scripts/check-public.sh
	$(PYTHON) scripts/check-docs.py
	bash scripts/check-ci.sh

verify: check test build

up: config
	cd compose/rendered && docker compose up -d --build

down:
	cd compose/rendered && docker compose down
