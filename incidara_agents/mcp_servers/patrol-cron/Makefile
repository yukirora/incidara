# patrol-cron Makefile
# Run from .23: cd ~/incidara/incidara && make -f incidara_agents/mcp_servers/patrol-cron/Makefile run
#
#   make run        — build + stop + run (full deploy)
#   make build      — build image only
#   make run-job    — build + stop-job + run-job (full deploy)
#   make stop       — stop infrastructure
#   make stop-job   — stop job_detection
#   make test       — run tests

CURDIR       := $(dir $(realpath $(firstword $(MAKEFILE_LIST))))
REPO_ROOT    := $(abspath $(CURDIR)/../../..)
DOCKERFILE   := $(CURDIR)/Dockerfile
IMAGE        := patrol-cron:latest
SSH_AGENT_SOCK ?= /tmp/ssh-agent.sock

INFRA_ENV := /data/agents/detection/.env
JOB_ENV   := /data/agents/job-detection/.env

.PHONY: build run run-job stop stop-job restart shell test

build:
	sudo docker build -t $(IMAGE) -f $(DOCKERFILE) $(REPO_ROOT)

# ── Infrastructure (port 8080, default role) ────────────────────────────

run: build stop
	sudo docker run -d --name patrol-cron --network host \
		--env-file $(INFRA_ENV) \
		-e MCP_HOST=127.0.0.1 \
		-v $(SSH_AGENT_SOCK):$(SSH_AGENT_SOCK) \
		-v /data/agents/detection/topology:/app/workspace/topology:ro \
		-v /data/agents/detection/patrol-logs:/tmp/patrol_cron \
		$(IMAGE)

stop:
	-sudo docker stop patrol-cron
	-sudo docker rm patrol-cron

# ── Job Detection (port 8082, PATROL_ROLE=job_detection) ────────────────

run-job: build stop-job
	sudo docker run -d --name job-patrol --network host \
		--env-file $(JOB_ENV) \
		-e PATROL_ROLE=job_detection \
		-e MCP_HOST=127.0.0.1 \
		$(IMAGE)

stop-job:
	-sudo docker stop job-patrol
	-sudo docker rm job-patrol

# ── Utility ──────────────────────────────────────────────────────────────

restart: stop run

shell:
	sudo docker exec -it patrol-cron bash

logs:
	sudo docker logs -f patrol-cron

test:
	sudo docker run --rm --network host \
		--env-file $(INFRA_ENV) \
		--entrypoint python $(IMAGE) -m pytest tests/ -v
