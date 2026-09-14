#!/bin/bash
# switch-operations container entrypoint.
#
# Starts MCP server in foreground (http on configured port).
# Port via SWITCH_OPS_PORT env var (default: 8090).
# Host via MCP_HOST env var (default: 127.0.0.1).

set -euo pipefail

echo "Starting switch-operations MCP server (host=${MCP_HOST:-127.0.0.1}, port=${SWITCH_OPS_PORT:-8090})..."
exec python3 /opt/switch-operations/mcp_server.py \
    --transport http
