.PHONY: build

# Builds the generic claude-agent image. Deployments (e.g. triage-unknown-claude-agent/)
# use this image as-is and provide their own run/stop targets with deployment-specific
# mounts and env files.

REPO_ROOT ?= $(abspath $(CURDIR)/../..)

build:
	docker build -f $(CURDIR)/Dockerfile -t claude-agent $(REPO_ROOT)
