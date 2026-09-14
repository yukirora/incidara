#!/bin/bash
# node_check.sh — Safe read-only SSH wrapper for running commands on cluster nodes
#
# The model decides what to check — this script just provides a safe tunnel.
# Commands requiring sudo (like dmesg, journalctl) are allowed since they are read-only.
#
# Usage: ./node_check.sh <node_ip> <command>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
source "$SCRIPT_DIR/scripts/ssh_setup.sh"

NODE_IP="${1:?Usage: $0 <node_ip> <command>}"
shift
CMD="${*:?Usage: $0 <node_ip> <command>}"

# --- Safety: block known dangerous commands ---
BLOCKED_PATTERNS=(
    "systemctl restart" "systemctl stop" "systemctl start"
    "systemctl enable" "systemctl disable"
    "nvidia-smi -r" "nvidia-smi --gpu-reset"
    "reboot" "shutdown" "poweroff" "halt" "init "
    "modprobe -r" "rmmod" "insmod"
    "kill " "pkill " "killall " "fuser -k"
    "rm " "rm -" "rmdir"
    "mv " "cp " "dd "
    "mkfs" "fdisk" "parted"
    "kubectl"
    "iptables" "ip6tables" "nft "
    "useradd" "userdel" "usermod" "passwd"
    "chmod " "chown "
)

CMD_LOWER=$(echo "$CMD" | tr '[:upper:]' '[:lower:]')
for pattern in "${BLOCKED_PATTERNS[@]}"; do
    if [[ "$CMD_LOWER" == *"$pattern"* ]]; then
        echo "STOP: '$CMD' contains '$pattern' which is a dangerous operation that can damage the node." >&2
        echo "You are a read-only investigation agent. Do not attempt to modify node state." >&2
        echo "If you need to fix something, report the finding and let the user decide the action." >&2
        exit 1
    done
done

# --- Execute ---
# Use bash -c with base64 encoding to avoid quoting issues with pipes, quotes, etc.
CMD_B64=$(echo "$CMD" | base64)
ssh -A $SSH_COMMON_OPTS "operator@$NODE_IP" \
    "echo $CMD_B64 | base64 -d | bash"
