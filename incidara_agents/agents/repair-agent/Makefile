.PHONY: build build-base run stop restart logs health sync-skills

# Repair agent — build + deploy via Makefile.
#
# Build (from repo):        cd incidara_agents/agents/repair-agent && make build
# Deploy (from repo):       cd incidara_agents/agents/repair-agent && sudo make run
# Restart:                  sudo make restart
#
# Runtime data lives in AGENT_DATA (/data/agents/repair/):
#   workspace/    → /app/workspace
#   claude-home/  → /root/.claude
#   .env          → secrets

# --- Paths ---
CLAUDE_AGENT_DIR ?= $(abspath $(CURDIR)/../claude-agent)
AGENT_DIR        := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT        ?= $(abspath $(CURDIR)/../..)

# --- Image vars ---
BASE_IMAGE       ?= claude-agent:latest
IMAGE            ?= claude-agent:repair
CONTAINER        ?= repair-agent

# --- Deploy vars ---
AGENT_DATA       ?= /data/agents/repair
SSH_AGENT_SOCK  ?= /tmp/ssh-agent.sock
MCP_ROLE         = ops

# --- Skills this agent needs — rsync'd from repo on each make run ---
SKILLS_SRC   ?= $(REPO_ROOT)/skills
AGENT_SKILLS ?= repair-nodes

# --- Build targets ---
build-base:
	docker build -f $(CLAUDE_AGENT_DIR)/Dockerfile -t $(BASE_IMAGE) $(REPO_ROOT)

build:
	docker build -f $(CURDIR)/Dockerfile -t $(IMAGE) $(REPO_ROOT)

# --- Deploy targets ---
prepare:
	mkdir -p $(AGENT_DATA)/workspace $(AGENT_DATA)/claude-home $(AGENT_DATA)/skills

sync-skills:
	@for skill in $(AGENT_SKILLS); do \
		echo "Syncing skill: $$skill"; \
		rsync -a --delete $(SKILLS_SRC)/$$skill/ $(AGENT_DATA)/skills/$$skill/; \
	done
	@for d in $(AGENT_DATA)/skills/*/; do \
		name=$$(basename $$d); \
		if ! echo " $(AGENT_SKILLS) " | grep -q " $$name "; then \
			echo "Removing stale skill: $$name"; \
			rm -rf $$d; \
		fi; \
	done

run: prepare sync-skills
	docker run -d --name $(CONTAINER) --env-file $(AGENT_DATA)/.env --network host \
		-e SSH_AUTH_SOCK=$(SSH_AGENT_SOCK) \
		-e CLAUDE_SKILL_DIR=/app/workspace/.claude/skills/repair-nodes \
		-e MCP_SERVER_ROLE=$(MCP_ROLE) \
		-v $(AGENT_DATA)/workspace:/app/workspace \
		-v $(AGENT_DATA)/skills:/app/workspace/.claude/skills \
		-v $(AGENT_DATA)/claude-home:/root/.claude \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		-v $(REPO_ROOT)/agents/repair-agent/setup-mcp.sh:/usr/local/bin/setup-mcp.sh:ro \
		--entrypoint /bin/bash \
		$(IMAGE) -lc "bash /usr/local/bin/setup-mcp.sh && exec ./entrypoint.sh"

stop:
	-docker stop $(CONTAINER)
	-docker rm $(CONTAINER)

restart: stop run

logs:
	docker logs --tail 200 -f $(CONTAINER)

health:
	curl -sS http://127.0.0.1:8003/health
