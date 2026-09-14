# agent-feedback MCP server — Makefile
# Standalone container, streamable-http on port 8091.
# Two roles: feedback_readonly (port 8091), feedback (port 8093).

REPO_ROOT    := $(abspath $(CURDIR)/../../..)
IMAGE        := agent-feedback:latest
SSH_AGENT_SOCK ?= /tmp/ssh-agent.sock
FEEDBACK_ENV ?= /data/agents/feedback/.env

.PHONY: build run run-write stop stop-all restart logs shell

build:
	sudo docker build -t $(IMAGE) -f $(CURDIR)/Dockerfile $(REPO_ROOT)

# ── Readonly role (port 8091) ───────────────────────────────────────────

run: build stop
	sudo docker run -d --name agent-feedback --network host \
		--env-file $(FEEDBACK_ENV) \
		-e AGENT_FEEDBACK_ROLE=feedback_readonly \
		-e AGENT_FEEDBACK_PORT=8091 \
		-e MCP_HOST=127.0.0.1 \
		-e MCP_REQUEST_TIMEOUT=300 \
		$(IMAGE)

stop:
	-sudo docker stop agent-feedback
	-sudo docker rm agent-feedback

# ── Write role (port 8093) ──────────────────────────────────────────────

run-write: build stop-write
	sudo docker run -d --name agent-feedback-write --network host \
		--env-file $(FEEDBACK_ENV) \
		-e AGENT_FEEDBACK_ROLE=feedback \
		-e AGENT_FEEDBACK_PORT=8093 \
		-e MCP_HOST=127.0.0.1 \
		-e MCP_REQUEST_TIMEOUT=300 \
		$(IMAGE)

stop-write:
	-sudo docker stop agent-feedback-write
	-sudo docker rm agent-feedback-write

stop-all: stop stop-write

restart: stop-all run

logs:
	sudo docker logs -f agent-feedback

shell:
	sudo docker exec -it agent-feedback bash

health:
	curl -sS http://127.0.0.1:8091/mcp -X POST \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"health","version":"1.0"}}}' \
		2>/dev/null | grep -o '"name":"agent-feedback"' && echo "OK" || echo "FAIL"
