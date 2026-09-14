# agent-evidence MCP server — Makefile
# Standalone container, streamable-http on port 8092.

REPO_ROOT    := $(abspath $(CURDIR)/../../..)
IMAGE        := agent-evidence:latest
ENV_FILE     ?= /data/agents/detection/.env

.PHONY: build run stop restart logs shell health

build:
	sudo docker build -t $(IMAGE) -f $(CURDIR)/Dockerfile $(REPO_ROOT)

run: build stop
	sudo docker run -d --name agent-evidence --network host \
		--env-file $(ENV_FILE) \
		-e AGENT_EVIDENCE_PORT=8092 \
		-e MCP_HOST=127.0.0.1 \
		-e EVIDENCE_DB_URL=$(shell grep EVIDENCE_DB_URL $(ENV_FILE) | cut -d= -f2-) \
		$(IMAGE)

stop:
	-sudo docker stop agent-evidence
	-sudo docker rm agent-evidence

restart: stop run

logs:
	sudo docker logs -f agent-evidence

shell:
	sudo docker exec -it agent-evidence bash

health:
	curl -sS http://127.0.0.1:8092/mcp -X POST \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"health","version":"1.0"}}}' \
		2>/dev/null | grep -o '"name":"agent-evidence"' && echo "OK" || echo "FAIL"
