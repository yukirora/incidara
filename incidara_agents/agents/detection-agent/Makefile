.PHONY: build build-base run stop restart logs health sync-skills

# Detection agent — proactive infrastructure monitoring.
#
# Build (from REPO_ROOT): cd incidara_agents && make -f agents/detection-agent/Makefile build
# Deploy (from REPO_ROOT): cd incidara_agents && sudo make -f agents/detection-agent/Makefile run
# Restart:                  sudo make restart
#
# Runtime data lives in AGENT_DATA (/data/agents/detection/):
#   workspace/    → /app/workspace    (detection state, reports, sessions)
#   claude-home/  → /root/.claude     (SDK conversations, config)
#   .env          → secrets
#   topology/     → switch list + topology files

# --- Paths ---
# AGENT_DIR is the directory containing this Makefile (always absolute)
AGENT_DIR        := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
CLAUDE_AGENT_DIR ?= $(abspath $(AGENT_DIR)/../../agents/claude-agent)
REPO_ROOT        ?= $(abspath $(AGENT_DIR)/../..)

# --- Image vars ---
BASE_IMAGE       ?= claude-agent:latest
IMAGE            ?= claude-agent:detection
CONTAINER        ?= detection-agent

# --- Deploy vars ---
AGENT_DATA       ?= /data/agents/detection
SSH_AGENT_SOCK  ?= /tmp/ssh-agent.sock
MCP_ROLE         = diagnosis
PORT             = 8007

# --- Skills this agent needs ---
SKILLS_SRC   ?= $(REPO_ROOT)/skills
AGENT_SKILLS ?= scan-cluster inspect-infra-issue automate-detection-pattern job-check \
	job-incident-response job-log-triage system-evidence-diagnosis \
	job-recovery training-reproduction rca-closeout

# --- Build targets ---
build-base:
	docker build -f $(CLAUDE_AGENT_DIR)/Dockerfile -t $(BASE_IMAGE) $(REPO_ROOT)

build: 
	docker build -f $(abspath $(CURDIR)/Dockerfile) -t $(IMAGE) $(REPO_ROOT)

# --- Deploy targets ---
prepare:
	mkdir -p $(AGENT_DATA)/workspace $(AGENT_DATA)/claude-home $(AGENT_DATA)/skills $(AGENT_DATA)/topology

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
		-e CLAUDE_SKILL_DIR=/app/workspace/.claude/skills \
		-e MCP_SERVER_ROLE=$(MCP_ROLE) \
		-e PORT=$(PORT) \
		-v $(AGENT_DATA)/workspace:/app/workspace \
		-v $(AGENT_DATA)/skills:/app/workspace/.claude/skills \
		-v $(AGENT_DATA)/claude-home:/root/.claude \
		-v $(AGENT_DATA)/topology:/app/workspace/topology:ro \
		-v $(AGENT_DATA)/patrol-logs:/tmp/patrol_cron:ro \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		-v $(REPO_ROOT)/agents/detection-agent/setup-mcp.sh:/usr/local/bin/setup-mcp.sh:ro \
		--entrypoint /bin/bash \
		$(IMAGE) -lc "bash /usr/local/bin/setup-mcp.sh && exec /detection-entrypoint.sh"

stop:
	-docker stop $(CONTAINER)
	-docker rm $(CONTAINER)

restart: stop run

logs:
	docker logs --tail 200 -f $(CONTAINER)

health:
	curl -sS http://127.0.0.1:$(PORT)/health
