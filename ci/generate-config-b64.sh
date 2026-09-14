#!/bin/bash
# ci/generate-config-b64.sh
# Helper to generate the base64-encoded config.yaml for Codeup pipeline variable.
#
# Usage: bash ci/generate-config-b64.sh > config_b64.txt
# Then copy the output into Codeup pipeline variable CONFIG_YAML_B64 (encrypted).
#
# In Codeup: 流水线 → 变量 → 新建变量 →
#   Name: CONFIG_YAML_B64
#   Value: (paste output)
#   Encrypted: Yes

set -euo pipefail

CONFIG_FILE="${1:-compose/config.yaml}"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: $CONFIG_FILE not found" >&2
    exit 1
fi

# Base64 encode without newlines (single line)
base64 -w 0 "$CONFIG_FILE"
echo
