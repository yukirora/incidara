#!/bin/bash
# incidara_agents/agents/detection-agent/setup-mcp.sh
# Configure MCP servers for the detection agent container.
# MCP URLs come from env vars (rendered by compose/render.py from config.yaml).

set -euo pipefail

AGENT_NAME="${AGENT_NAME:-detection}"
MCP_ROLE="${MCP_SERVER_ROLE:-ops}"

echo "setup-mcp.sh: AGENT_NAME=$AGENT_NAME  MCP_ROLE=$MCP_ROLE"

python3 -c "
import json, os

def env(key, default=''):
    return os.environ.get(key, default)

agent_name = env('AGENT_NAME', 'detection')
mcp_role = env('MCP_SERVER_ROLE', 'ops')

config = {
    'mcpServers': {
        'feishu-bitable': {
            'command': 'python3',
            'args': ['/opt/feishu-bitable/mcp_server.py'],
            'env': {
                'FEISHU_APP_ID': env('FEISHU_APP_ID', ''),
                'FEISHU_APP_SECRET': env('FEISHU_APP_SECRET', ''),
                'FEISHU_BASE_TOKEN': env('FEISHU_BASE_TOKEN', ''),
                'FEISHU_TABLE_ID': env('FEISHU_TABLE_ID', ''),
            }
        },
        'node-ops': {
            'type': 'streamable-http',
            'url': env('MCP_NODE_OPS_DIAGNOSIS_URL', 'http://127.0.0.1:8081/mcp'),
            'timeout': 600000,
        },
        'switch-ops': {
            'type': 'streamable-http',
            'url': env('MCP_SWITCH_OPS_URL', 'http://127.0.0.1:8090/mcp'),
            'timeout': 600000,
        },
        'agent-evidence': {
            'type': 'streamable-http',
            'url': env('MCP_AGENT_EVIDENCE_URL', 'http://127.0.0.1:8092/mcp'),
            'timeout': 600000,
        },
        'patrol-cron': {
            'type': 'streamable-http',
            'url': env('MCP_PATROL_CRON_URL', 'http://127.0.0.1:8080/mcp'),
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

# ---- Write ~/.claude/settings.json (SDK permissions) ----

detection_permissions = [
    'Bash(*)',
    'Read(*)',
    'Write(*)',
    'Edit(*)',
    'Skill(*)',
    'mcp__feishu-bitable__*',
    'mcp__node-ops__*',
    'mcp__switch-ops__*',
    'mcp__agent-evidence__*',
    'mcp__patrol-cron__*',
    'mcp__agent-feedback__*',
]

settings = {
    'permissions': {
        'allow': detection_permissions,
        'deny': [],
    },
    'mcpServers': config['mcpServers'],
}

import os as _os
_os.makedirs('/root/.claude', exist_ok=True)
with open('/root/.claude/settings.json', 'w') as f:
    json.dump(settings, f, indent=2)

print('Wrote /root/.claude/settings.json with ' + str(len(detection_permissions)) + ' allowed MCP tools')
"
