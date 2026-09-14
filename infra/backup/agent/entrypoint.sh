#!/bin/bash
# Agent backup sidecar entrypoint
# - Writes ossutilconfig
# - Verifies bucket
# - Runs first sync per agent
# - Installs one cron job per agent
set -euo pipefail

# Write ossutilconfig
cat > /root/.ossutilconfig <<EOF
[default]
mode = AK
region = ${OSS_REGION:-cn-shanghai}
endpoint = ${OSS_ENDPOINT:-oss-cn-shanghai.aliyuncs.com}
accessKeyID = ${OSS_ACCESS_KEY_ID:?OSS_ACCESS_KEY_ID required}
accessKeySecret = ${OSS_ACCESS_KEY_SECRET:?OSS_ACCESS_KEY_SECRET required}
EOF
chmod 600 /root/.ossutilconfig

# Verify bucket (read + write)
DST_ROOT="${DST_ROOT:-oss://ltp-data/agents-backup}"
BUCKET="$(echo "$DST_ROOT" | sed 's|oss://\([^/]*\).*|\1|')"
ossutil ls "oss://$BUCKET" >/dev/null 2>&1 || { echo "ERROR: cannot list $BUCKET"; exit 1; }
PROBE="$DST_ROOT/.probe-$$"
echo "probe" | ossutil cp - "$PROBE" -f >/dev/null 2>&1 || { echo "ERROR: cannot write to $DST_ROOT"; exit 1; }
ossutil rm "$PROBE" -f >/dev/null 2>&1 || true
echo "Bucket OK (read+write)"

# Parse agents list
AGENTS_ROOT="${AGENTS_ROOT:-/mntsys/agents}"
SYNC_INTERVAL="${SYNC_INTERVAL_MINUTES:-360}"
IFS=',' read -ra AGENT_LIST <<< "${AGENTS:-}"

# Build cron schedule
if [ "$SYNC_INTERVAL" -lt 60 ]; then
    CRON_SPEC="*/${SYNC_INTERVAL} * * * *"
elif [ "$SYNC_INTERVAL" -lt 1440 ]; then
    CRON_SPEC="0 */$((SYNC_INTERVAL / 60)) * * *"
else
    CRON_SPEC="0 0 */$((SYNC_INTERVAL / 1440)) * *"
fi

# First sync + install per-agent cron jobs
CRON=""
for agent in "${AGENT_LIST[@]}"; do
    echo "=== $agent ==="
    /usr/local/bin/agent-sync.sh "$agent" || true
    CRON+="${CRON_SPEC} /usr/local/bin/agent-sync.sh $agent >> /root/.cache/agent-sync/$agent.log 2>&1"$'\n'
done

mkdir -p /root/.cache/agent-sync
echo -n "$CRON" >> /etc/crontabs/root

echo "Ready — ${#AGENT_LIST[@]} agents, syncing every ${SYNC_INTERVAL} min"
crond -f -l 2
