#!/bin/bash
# ssh_setup.sh — Common SSH configuration for triage sub-skill scripts
#
# Provides JUMP_HOST and SSH_COMMON_OPTS used by kubectl_check.sh and node_check.sh.
# Sourced from sub-skills via: source "$SCRIPT_DIR/scripts/ssh_setup.sh"

# Jump host for kubectl access (LTP management node)
JUMP_HOST="${JUMP_HOST:-<user>@<jump-host>}"

# Common SSH options
SSH_COMMON_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
