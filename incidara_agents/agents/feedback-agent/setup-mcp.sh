#!/bin/bash
# incidara_agents/agents/feedback-agent/setup-mcp.sh
# Configure MCP servers for the feedback agent container.
# MCP URLs come from env vars (rendered by compose/render.py from config.yaml).

set -euo pipefail

AGENT_NAME="${AGENT_NAME:-feedback}"
MCP_ROLE="${MCP_SERVER_ROLE:-feedback}"

echo "setup-mcp.sh: AGENT_NAME=$AGENT_NAME  MCP_ROLE=$MCP_ROLE"

python3 -c "
import json, os

def env(key, default=''):
    return os.environ.get(key, default)

agent_name = env('AGENT_NAME', 'feedback')
mcp_role = env('MCP_SERVER_ROLE', 'feedback')

config = {
    'mcpServers': {
        'agent-feedback': {
            'type': 'streamable-http',
            'url': env('MCP_AGENT_FEEDBACK_WRITE_URL', 'http://127.0.0.1:8093/mcp'),
            'timeout': 600000,
        },
        'node-ops': {
            'type': 'streamable-http',
            'url': env('MCP_NODE_OPS_FEEDBACK_URL', 'http://127.0.0.1:8084/mcp'),
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
    }
}

with open('/root/.claude.json', 'w') as f:
    json.dump(config, f, indent=2)

print('Wrote /root/.claude.json with mcpServers: ' + ', '.join(config['mcpServers'].keys()))

# ---- Write ~/.claude/settings.json (SDK permissions) ----

feedback_permissions = [
    'Bash(*)',
    'Read(*)',
    'Write(*)',
    'Edit(*)',
    'Skill(*)',
    # agent-feedback (write instance on 8093) — all tools
    'mcp__agent-feedback__*',
    # node-ops (feedback role on 8084) — all tools
    'mcp__node-ops__*',
    # agent-evidence — all tools
    'mcp__agent-evidence__*',
    # patrol-cron — READ-ONLY tools only (no create/update/record/save)
    'mcp__patrol-cron__list_collectors',
    'mcp__patrol-cron__get_collector_health',
    'mcp__patrol-cron__list_rules',
    'mcp__patrol-cron__get_rule_detail',
    'mcp__patrol-cron__list_findings',
    'mcp__patrol-cron__get_finding_raw_data',
    'mcp__patrol-cron__list_rule_replay_cases',
    'mcp__patrol-cron__get_rule_accuracy',
    'mcp__patrol-cron__get_rule_rejection_reasons',
    'mcp__patrol-cron__get_rule_bad_feedback_rate',
    'mcp__patrol-cron__list_dirty_reconciliation_rules',
    'mcp__patrol-cron__query_prometheus',
    'mcp__patrol-cron__query_job_metadata',
]

settings = {
    'permissions': {
        'allow': feedback_permissions,
        'deny': [],
    },
    'mcpServers': config['mcpServers'],
}

import os as _os
_os.makedirs('/root/.claude', exist_ok=True)
with open('/root/.claude/settings.json', 'w') as f:
    json.dump(settings, f, indent=2)

print('Wrote /root/.claude/settings.json with ' + str(len(feedback_permissions)) + ' allowed MCP tools')
"
