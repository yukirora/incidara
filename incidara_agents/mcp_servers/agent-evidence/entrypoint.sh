#!/bin/bash
# agent-evidence container entrypoint.
#
# Starts MCP server in foreground (http on configured port).
# Port via AGENT_EVIDENCE_PORT env var (default: 8092).
# Host via MCP_HOST env var (default: 127.0.0.1).

set -euo pipefail

echo "Starting agent-evidence MCP server (host=${MCP_HOST:-127.0.0.1}, port=${AGENT_EVIDENCE_PORT:-8092})..."
exec python3 /opt/agent-evidence/mcp_server.py \
    --transport http
