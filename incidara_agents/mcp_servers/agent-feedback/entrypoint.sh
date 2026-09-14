#!/bin/bash
# agent-feedback container entrypoint.
#
# Starts MCP server in foreground (http on configured port).
# Role via AGENT_FEEDBACK_ROLE env var (default: feedback_readonly).
# Port via AGENT_FEEDBACK_PORT env var (default: 8091).
# Host via MCP_HOST env var (default: 127.0.0.1).

set -euo pipefail

ROLE="${AGENT_FEEDBACK_ROLE:-feedback_readonly}"

echo "Starting agent-feedback MCP server (role=${ROLE}, host=${MCP_HOST:-127.0.0.1}, port=${AGENT_FEEDBACK_PORT:-8091})..."
exec python3 /opt/agent-feedback/mcp_server.py \
    --role "${ROLE}" \
    --transport http
