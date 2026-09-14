#!/bin/bash
# Detection agent entrypoint — starts claude-agent gateway.
# Switch monitor runs as a separate container (switch-monitor-cron).

# --- Start the base entrypoint (claude-agent) ---
exec /app/entrypoint.sh "$@"
