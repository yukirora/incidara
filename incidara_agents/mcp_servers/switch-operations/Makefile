# switch-operations MCP server — Makefile
# Standalone container, streamable-http on port 8090.

REPO_ROOT    := $(abspath $(CURDIR)/../../..)
IMAGE        := switch-operations:latest
SSH_AGENT_SOCK ?= /tmp/ssh-agent.sock
ENV_FILE     ?= /data/agents/detection/.env

.PHONY: build run stop restart logs shell

build:
	sudo docker build -t $(IMAGE) -f $(CURDIR)/Dockerfile $(REPO_ROOT)

run: build stop
	sudo docker run -d --name switch-operations --network host \
		--env-file $(ENV_FILE) \
		-e SWITCH_OPS_PORT=8090 \
		-e MCP_HOST=127.0.0.1 \
		-e MCP_REQUEST_TIMEOUT=300 \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		$(IMAGE)

stop:
	-sudo docker stop switch-operations
	-sudo docker rm switch-operations

restart: stop run

logs:
	sudo docker logs -f switch-operations

shell:
	sudo docker exec -it switch-operations bash

health:
	curl -sS http://127.0.0.1:8090/mcp -X POST \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"health","version":"1.0"}}}' \
		2>/dev/null | grep -o '"name":"switch-operations"' && echo "OK" || echo "FAIL"
