# compose/Makefile — Ops commands for managing the compose deployment
#
# Usage:
#   make config-push          # Upload local config.yaml to remote
#   make config-pull          # Download remote config.yaml to local
#   make render               # Re-render compose files on remote
#   make build SVC=detection  # Build one service image on remote
#   make build-all            # Build all service images on remote
#   make start SVC=detection  # Start one service
#   make stop SVC=detection   # Stop one service
#   make restart SVC=detection # Restart one service
#   make status               # Show all container statuses
#   make deploy SVC=detection # Build + start one service
#   make sync                 # Rsync repo to remote (excludes config.yaml)
#   make full-deploy          # Sync + render + build-all + start all

REMOTE_HOST ?= operator@192.0.2.10
REMOTE_DIR  ?= /tmp/incidara
COMPOSE_DIR ?= /tmp/incidara/compose/rendered
LOCAL_DIR   ?= $(shell cd .. && pwd)

# --- Config management ---

.PHONY: config-push config-pull config-check

config-check:
	@echo "Validating config.yaml..."
	python3 render.py --check

config-push: config-check
	@echo "Pushing config.yaml to remote..."
	scp config.yaml $(REMOTE_HOST):$(REMOTE_DIR)/compose/config.yaml
	@echo "Re-rendering on remote..."
	ssh $(REMOTE_HOST) "cd $(REMOTE_DIR)/compose && python3 render.py"

config-pull:
	@echo "Pulling config.yaml from remote..."
	scp $(REMOTE_HOST):$(REMOTE_DIR)/compose/config.yaml config.yaml

# --- Repo sync ---

.PHONY: sync

sync:
	@echo "Syncing repo to remote (excluding config.yaml)..."
	rsync -avz --exclude config.yaml --exclude rendered/ --exclude __pycache__ \
		$(LOCAL_DIR)/ $(REMOTE_HOST):$(REMOTE_DIR)/

# --- Render ---

.PHONY: render

render:
	ssh $(REMOTE_HOST) "cd $(REMOTE_DIR)/compose && python3 render.py"

# --- Build ---

.PHONY: build build-all

build:
	@test -n "$(SVC)" || (echo "Usage: make build SVC=<service-name>" && exit 1)
	ssh $(REMOTE_HOST) "cd $(COMPOSE_DIR) && sudo docker compose build $(SVC)"

build-all:
	ssh $(REMOTE_HOST) "cd $(COMPOSE_DIR) && sudo docker compose build"

# --- Service lifecycle ---

.PHONY: start stop restart status

start:
	@test -n "$(SVC)" || (echo "Usage: make start SVC=<service-name>" && exit 1)
	ssh $(REMOTE_HOST) "cd $(COMPOSE_DIR) && sudo docker compose up -d $(SVC)"

stop:
	@test -n "$(SVC)" || (echo "Usage: make stop SVC=<service-name>" && exit 1)
	ssh $(REMOTE_HOST) "cd $(COMPOSE_DIR) && sudo docker compose stop $(SVC)"

restart:
	@test -n "$(SVC)" || (echo "Usage: make restart SVC=<service-name>" && exit 1)
	ssh $(REMOTE_HOST) "cd $(COMPOSE_DIR) && sudo docker compose restart $(SVC)"

status:
	ssh $(REMOTE_HOST) "cd $(COMPOSE_DIR) && sudo docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'"

# --- Composite targets ---

.PHONY: deploy full-deploy

deploy:
	@test -n "$(SVC)" || (echo "Usage: make deploy SVC=<service-name>" && exit 1)
	$(MAKE) build SVC=$(SVC)
	$(MAKE) start SVC=$(SVC)

full-deploy:
	$(MAKE) sync
	$(MAKE) config-push
	$(MAKE) build-all
	ssh $(REMOTE_HOST) "cd $(COMPOSE_DIR) && sudo docker compose up -d"
