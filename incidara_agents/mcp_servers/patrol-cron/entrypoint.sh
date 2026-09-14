#!/bin/bash
# patrol-cron container entrypoint.
#
# 1. Start cron daemon (for collector jobs)
# 2. Start cron_sync in background (watches DB, writes crontab)
# 3. Run MCP server in foreground (SSE on port 8080)

set -euo pipefail

# Ensure logs directory exists (volume mount may replace /tmp/patrol_cron)
mkdir -p /tmp/patrol_cron/logs

echo "Starting cron daemon..."
cron

echo "Starting cron_sync daemon (background)..."
python3 -m patrol_cron.cron_sync --watch \
    >> /tmp/patrol_cron/logs/cron_sync.log 2>&1 &
SYNC_PID=$!
echo "cron_sync started (PID=$SYNC_PID)"

echo "Starting MCP server (http on port ${MCP_PORT:-8080})..."
exec python3 /opt/patrol-cron/mcp_server.py \
    --transport http \
    --port "${MCP_PORT:-8080}"
