#!/bin/bash
# incidara_agents/agents/repair-agent/setup-mcp.sh
# Configure MCP servers for the repair agent container.
# MCP URLs come from env vars (rendered by compose/render.py from config.yaml).
set -euo pipefail

mkdir -p /root/.claude

# ---- 1. Write ~/.claude.json (primary — this is what Claude Code reads) ----

python3 << 'PYEOF'
import json, os

def env(key, default=""):
    return os.environ.get(key, default)

existing = {}
try:
    with open("/root/.claude.json") as f:
        existing = json.load(f)
except FileNotFoundError:
    pass

role = env("MCP_SERVER_ROLE", "ops")
mcp = {
    "node-ops": {
        "type": "streamable-http",
        "url": env("MCP_NODE_OPS_OPS_URL", "http://127.0.0.1:8083/mcp"),
        "timeout": 14400000,
    },
    "agent-evidence": {
        "type": "streamable-http",
        "url": env("MCP_AGENT_EVIDENCE_URL", "http://127.0.0.1:8092/mcp"),
        "timeout": 600000,
    },
    "agent-feedback": {
        "type": "streamable-http",
        "url": env("MCP_AGENT_FEEDBACK_URL", "http://127.0.0.1:8091/mcp"),
    },
    "playwright": {
        "type": "stdio",
        "command": "npx",
        "args": ["@playwright/mcp@latest", "--ignore-https-errors", "--headless"],
        "env": {
            "PLAYWRIGHT_MCP_IGNORE_HTTPS_ERRORS": "true",
        }
    }
}

if "mcpServers" not in existing:
    existing["mcpServers"] = {}
existing["mcpServers"].update(mcp)

with open("/root/.claude.json", "w") as f:
    json.dump(existing, f, indent=2)

print(f"MCP configured in /root/.claude.json (role={role}, agent={env('AGENT_NAME', 'unknown')})")
PYEOF

# ---- 2. Write ~/.claude/settings.json (secondary — for SDK settingSources) ----

python3 << 'PYEOF'
import json, os

def env(key, default=""):
    return os.environ.get(key, default)

role = env("MCP_SERVER_ROLE", "ops")
settings = {
    "permissions": {
        "allow": [
            "Bash(*)",
            "Read(*)",
            "Write(*)",
            "Edit(*)",
            "mcp__node-ops__*",
            "mcp__agent-evidence__save_evidence_tool",
            "mcp__agent-evidence__get_node_evidence_tool",
            "mcp__agent-evidence__delete_node_evidence_tool",
            "mcp__agent-feedback__*",
            "mcp__playwright__*",
        ],
    }
}

with open("/root/.claude/settings.json", "w") as f:
    json.dump(settings, f, indent=2)

print("MCP configured in /root/.claude/settings.json")
PYEOF

# ---- 3. Verify SSH agent forwarding ----

if [ -S "$SSH_AUTH_SOCK" ]; then
  echo "Using host SSH agent: $SSH_AUTH_SOCK"
  ssh-add -l 2>/dev/null || echo "Warning: no keys in agent"
else
  echo "Warning: SSH_AUTH_SOCK not available"
fi
