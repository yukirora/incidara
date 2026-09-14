#!/bin/bash
# node-operations container entrypoint.
#
# Starts MCP server in foreground (http on configured port).
# Role is set via MCP_SERVER_ROLE env var (default: diagnosis).
# Port is set via NODE_OPS_PORT env var (default: 8081).

set -euo pipefail

ROLE="${MCP_SERVER_ROLE:-diagnosis}"
PORT="${NODE_OPS_PORT:-8081}"

echo "Starting node-operations MCP server (role=${ROLE}, port=${PORT})..."
exec python3 /opt/node-operations/mcp_server.py \
    --role "${ROLE}" \
    --transport http \
    --port "${PORT}"
