#!/bin/bash
set -euo pipefail

python3 - <<'PY'
import json
import os

config = {"mcpServers": {}}
os.makedirs("/root/.claude", exist_ok=True)
with open("/root/.claude.json", "w") as f:
    json.dump(config, f, indent=2)

settings = {
    "permissions": {
        "allow": [
            "Read(*)",
            "Write(*)",
            "Edit(*)",
            "Bash(*)",
            "Skill(*)",
        ],
        "deny": [],
    },
    "mcpServers": {},
}
with open("/root/.claude/settings.json", "w") as f:
    json.dump(settings, f, indent=2)
PY
