.PHONY: build build-base run stop restart logs health sync-skills

CLAUDE_AGENT_DIR ?= $(abspath $(CURDIR)/../claude-agent)
AGENT_DIR        := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT        ?= $(abspath $(CURDIR)/../..)

BASE_IMAGE       ?= claude-agent:latest
IMAGE            ?= claude-agent:attention
CONTAINER        ?= attention-agent

AGENT_DATA       ?= /data/agents/attention
SSH_AGENT_SOCK   ?= /tmp/ssh-agent.sock
PORT             = 8010

SKILLS_SRC       ?= $(REPO_ROOT)/skills
AGENT_SKILLS     ?= agent-attention-queue

build-base:
	docker build -f $(CLAUDE_AGENT_DIR)/Dockerfile -t $(BASE_IMAGE) $(REPO_ROOT)

build:
	docker build -f $(CURDIR)/Dockerfile -t $(IMAGE) $(REPO_ROOT)

prepare:
	mkdir -p $(AGENT_DATA)/workspace $(AGENT_DATA)/claude-home $(AGENT_DATA)/skills

sync-skills:
	@for skill in $(AGENT_SKILLS); do \
		echo "Syncing skill: $$skill"; \
		rsync -a --delete $(SKILLS_SRC)/$$skill/ $(AGENT_DATA)/skills/$$skill/; \
	done

run: prepare sync-skills
	docker run -d --name $(CONTAINER) --env-file $(AGENT_DATA)/.env --network host \
		-e CLAUDE_SKILL_DIR=/app/workspace/.claude/skills \
		-e PORT=$(PORT) \
		-v $(AGENT_DATA)/workspace:/app/workspace \
		-v $(AGENT_DATA)/skills:/app/workspace/.claude/skills \
		-v $(AGENT_DATA)/claude-home:/root/.claude \
		-v /data/agents/triage-unknown/workspace/sessions:/mnt/sessions/triage:ro \
		-v /data/agents/repair/workspace/sessions:/mnt/sessions/repair:ro \
		-v /data/agents/recycler/workspace/sessions:/mnt/sessions/recycler:ro \
		-v /data/agents/detection/workspace/sessions:/mnt/sessions/detection:ro \
		-v /data/agents/feedback/workspace/sessions:/mnt/sessions/feedback:ro \
		-v $(REPO_ROOT)/agents/attention-agent/setup-mcp.sh:/usr/local/bin/setup-mcp.sh:ro \
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
