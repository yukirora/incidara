# node-operations MCP server — Makefile
# Run from .23: cd ~/incidara/incidara && make -f incidara_agents/mcp_servers/node-operations/Makefile run-diagnosis
#
# Three instances (one per role), each on its own port, bound to 127.0.0.1.

REPO_ROOT    := $(abspath $(CURDIR)/../../..)
IMAGE        := node-operations:latest
SSH_AGENT_SOCK ?= /tmp/ssh-agent.sock

# diagnosis role → triage is primary user; ops role → repair is primary user
DIAG_ENV     := /data/agents/triage-unknown/.env
OPS_ENV      := /data/agents/repair/.env
FEEDBACK_ENV := /data/agents/feedback/.env

.PHONY: build run-diagnosis run-ops run-feedback stop-diagnosis stop-ops stop-feedback stop-all restart

build:
	sudo docker build -t $(IMAGE) -f $(CURDIR)/Dockerfile $(REPO_ROOT)

# ── Diagnosis role (port 8081) — for detection, triage ──────────────────

run-diagnosis: build stop-diagnosis
	sudo docker run -d --name node-ops-diagnosis --network host \
		--env-file $(DIAG_ENV) \
		-e MCP_SERVER_ROLE=diagnosis \
		-e NODE_OPS_PORT=8081 \
		-e MCP_HOST=127.0.0.1 \
		-e SSH_AUTH_SOCK=$(SSH_AGENT_SOCK) \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		$(IMAGE)

stop-diagnosis:
	-sudo docker stop node-ops-diagnosis
	-sudo docker rm node-ops-diagnosis

# ── Ops role (port 8083) — for repair, recycler ─────────────────────────

run-ops: build stop-ops
	sudo docker run -d --name node-ops-ops --network host \
		--env-file $(OPS_ENV) \
		-e MCP_SERVER_ROLE=ops \
		-e NODE_OPS_PORT=8083 \
		-e MCP_HOST=127.0.0.1 \
		-e SSH_AUTH_SOCK=$(SSH_AGENT_SOCK) \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		$(IMAGE)

stop-ops:
	-sudo docker stop node-ops-ops
	-sudo docker rm node-ops-ops

# ── Feedback role (port 8084) — for feedback agent ───────────────────────

run-feedback: build stop-feedback
	sudo docker run -d --name node-ops-feedback --network host \
		--env-file $(FEEDBACK_ENV) \
		-e MCP_SERVER_ROLE=feedback \
		-e NODE_OPS_PORT=8084 \
		-e MCP_HOST=127.0.0.1 \
		-e SSH_AUTH_SOCK=$(SSH_AGENT_SOCK) \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		$(IMAGE)

stop-feedback:
	-sudo docker stop node-ops-feedback
	-sudo docker rm node-ops-feedback

# ── All ──────────────────────────────────────────────────────────────────

stop-all: stop-diagnosis stop-ops stop-feedback

restart: stop-all run-diagnosis run-ops run-feedback

shell:
	sudo docker exec -it node-ops-diagnosis bash

logs:
	sudo docker logs -f node-ops-diagnosis
