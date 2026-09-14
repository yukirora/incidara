.PHONY: build build-base run stop restart logs health sync-skills

# Feedback agent — build + deploy via Makefile.
#
# Build (from repo):        cd agents/feedback-agent && make build
# Deploy (from repo):       cd agents/feedback-agent && sudo make run
# Restart:                  sudo make restart
#
# Runtime data lives in AGENT_DATA (/data/agents/feedback/):
#   workspace/    → /app/workspace    (reports, session JSONL, working files)
#   claude-home/  → /root/.claude     (SDK conversations, config)
#   .env          → secrets

# --- Paths ---
CLAUDE_AGENT_DIR ?= $(abspath $(CURDIR)/../claude-agent)
REPO_ROOT        ?= $(abspath $(CURDIR)/../..)

# --- Image vars ---
BASE_IMAGE       ?= claude-agent:latest
IMAGE            ?= claude-agent:feedback
CONTAINER        ?= feedback-agent

# --- Deploy vars ---
AGENT_DATA       ?= /data/agents/feedback
SSH_AGENT_SOCK  ?= /tmp/ssh-agent.sock
MCP_ROLE         = feedback
PORT             = 8006

# --- Skills this agent needs — rsync'd from repo on each make run ---
SKILLS_SRC   ?= $(REPO_ROOT)/skills
AGENT_SKILLS ?= analyze-rma-cases skill-optimize collect-rma-cases deploy-feedback-agent-patches pr-creation pr-merge pr-list triage-nodes

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
		-e CLAUDE_SKILL_DIR=/app/workspace/.claude/skills \
		-e MCP_SERVER_ROLE=$(MCP_ROLE) \
		-e PORT=$(PORT) \
		-v $(AGENT_DATA)/workspace:/app/workspace \
		-v $(AGENT_DATA)/skills:/app/workspace/.claude/skills \
		-v $(AGENT_DATA)/claude-home:/root/.claude \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		-v /data/agents/triage-unknown/workspace/sessions:/mnt/sessions/triage:ro \
		-v /data/agents/repair/workspace/sessions:/mnt/sessions/repair:ro \
		-v /data/agents/recycler/workspace/sessions:/mnt/sessions/recycler:ro \
		-v /data/agents/detection/workspace/sessions:/mnt/sessions/detection:ro \
		-v /data/agents/triage-unknown/claude-home/projects:/mnt/transcripts/triage:ro \
		-v /data/agents/repair/claude-home/projects:/mnt/transcripts/repair:ro \
		-v /data/agents/recycler/claude-home/projects:/mnt/transcripts/recycler:ro \
		-v /data/agents/detection/claude-home/projects:/mnt/transcripts/detection:ro \
		-v $(REPO_ROOT)/skills:/mnt/skills:ro \
		-v $(AGENT_DATA)/ssh/deploy_key:/root/.ssh/deploy_key:ro \
		-v $(REPO_ROOT)/agents/feedback-agent/setup-mcp.sh:/usr/local/bin/setup-mcp.sh:ro \
		--entrypoint /bin/bash \
		$(IMAGE) -lc "bash /usr/local/bin/setup-mcp.sh && exec ./entrypoint.sh"

stop:
	-docker stop $(CONTAINER)
	-docker rm $(CONTAINER)

restart: stop run

logs:
	docker logs --tail 200 -f $(CONTAINER)

health:
	curl -sS http://127.0.0.1:$(PORT)/health