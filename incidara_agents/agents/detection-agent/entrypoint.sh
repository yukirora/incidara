#!/bin/bash
# Detection agent entrypoint — starts claude-agent gateway.
# Patrol and switch MCP services run as separate containers.

# --- Start the base entrypoint (claude-agent) ---
exec /app/entrypoint.sh "$@"
