#!/bin/bash
# incidara_agents/agents/triage-agent/setup-mcp.sh
# Configure MCP servers for the triage agent container.
# MCP URLs come from env vars (rendered by compose/render.py from config.yaml).
set -euo pipefail

AGENT_NAME="${AGENT_NAME:-triage}"
MCP_ROLE="${MCP_SERVER_ROLE:-diagnosis}"

echo "setup-mcp.sh: AGENT_NAME=$AGENT_NAME  MCP_ROLE=$MCP_ROLE"

python3 -c "
import json, os

def env(key, default=''):
    return os.environ.get(key, default)

agent_name = env('AGENT_NAME', 'triage')
mcp_role = env('MCP_SERVER_ROLE', 'diagnosis')

config = {
    'mcpServers': {
        'node-ops': {
            'type': 'streamable-http',
            'url': env('MCP_NODE_OPS_DIAGNOSIS_URL', 'http://127.0.0.1:8081/mcp'),
            'timeout': 600000,
        },
        'agent-evidence': {
            'type': 'streamable-http',
            'url': env('MCP_AGENT_EVIDENCE_URL', 'http://127.0.0.1:8092/mcp'),
            'timeout': 600000,
        },
        'agent-feedback': {
            'type': 'streamable-http',
            'url': env('MCP_AGENT_FEEDBACK_URL', 'http://127.0.0.1:8091/mcp'),
        }
    }
}

with open('/root/.claude.json', 'w') as f:
    json.dump(config, f, indent=2)

print('Wrote /root/.claude.json with mcpServers: ' + ', '.join(config['mcpServers'].keys()))
print('  node-ops role: ' + mcp_role)
print('  AGENT_NAME: ' + agent_name)

# ---- Write ~/.claude/settings.json (SDK permissions) ----

diagnosis_permissions = [
    'Bash(*)',
    'Read(*)',
    'Write(*)',
    'Edit(*)',
    'mcp__node-ops__*',
    'mcp__agent-evidence__save_evidence_tool',
    'mcp__agent-evidence__get_node_evidence_tool',
    'mcp__agent-evidence__delete_node_evidence_tool',
    'mcp__agent-feedback__*',
]

settings = {
    'permissions': {
        'allow': diagnosis_permissions,
        'deny': [],
    },
    'mcpServers': config['mcpServers'],
}

import os as _os
_os.makedirs('/root/.claude', exist_ok=True)
with open('/root/.claude/settings.json', 'w') as f:
    json.dump(settings, f, indent=2)

print('Wrote /root/.claude/settings.json with ' + str(len(diagnosis_permissions)) + ' allowed MCP tools')
"
