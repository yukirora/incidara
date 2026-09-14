#!/bin/bash
# kubectl_check.sh — Safe read-only kubectl wrapper via SSH jump host
#
# The model decides what to check — this script just provides a safe tunnel.
#
# Usage: ./kubectl_check.sh <get|describe|logs|top|version> [args...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
source "$SCRIPT_DIR/scripts/ssh_setup.sh"

# --- Safety: read-only whitelist ---
KUBECTL_CMD="${1:?Usage: $0 <get|describe|logs|top|version> [args...]}"
case "$KUBECTL_CMD" in
    get|describe|logs|top|version)
        ;;
    *)
        echo "STOP: 'kubectl $KUBECTL_CMD' modifies cluster state. You should not do this." >&2
        echo "You are a read-only investigation agent. Only get/describe/logs/top/version are allowed." >&2
        echo "If you need to change something, report the finding and let the user decide the action." >&2
        exit 1
        ;;
esac

# --- Execute ---
ssh -A $SSH_COMMON_OPTS "$JUMP_HOST" \
    "kubectl $*"
