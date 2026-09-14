#!/bin/bash
# agent-sync.sh — sync agent data to OSS, preserving local hierarchy.
#
# Usage:
#   agent-sync.sh repair                  # sync entire repair agent
#   agent-sync.sh repair/workspace        # sync only workspace
#   agent-sync.sh repair/claude-home/projects  # sync only transcripts
#   agent-sync.sh --all                   # sync all configured agents
#
# Local:  /mntsys/agents/repair/workspace/...
# OSS:    oss://ltp-data/agents-backup/repair/workspace/...
#
# Restore:
#   ossutil cp -r oss://ltp-data/agents-backup/repair/ /mntsys/agents/repair/

set -euo pipefail

AGENTS_ROOT="${AGENTS_ROOT:-/mntsys/agents}"
DST_ROOT="${DST_ROOT:-oss://ltp-data/agents-backup}"

usage() {
    cat <<EOF
Usage: agent-sync.sh <target> [--full]
  target:  <agent>                     sync entire agent (e.g. repair)
           <agent>/<folder>            sync subfolder (e.g. repair/workspace)
           --all                       sync all agents from AGENTS env
  --full:  force re-upload (skip --update)

Examples:
  agent-sync.sh repair                       # backup all repair data
  agent-sync.sh repair/workspace             # backup only workspace
  agent-sync.sh repair/claude-home/projects  # backup only transcripts
  agent-sync.sh --all                        # backup every agent
EOF
}

if [ $# -lt 1 ]; then
    usage; exit 1
fi

FULL=false
TARGET=""

for arg in "$@"; do
    case "$arg" in
        --full)  FULL=true ;;
        --all)   TARGET="--all" ;;
        -h|--help) usage; exit 0 ;;
        *)       TARGET="$arg" ;;
    esac
done

if [ -z "$TARGET" ]; then
    usage; exit 1
fi

# --all: iterate over AGENTS env
if [ "$TARGET" = "--all" ]; then
    IFS=',' read -ra AGENT_LIST <<< "${AGENTS:-}"
    if [ ${#AGENT_LIST[@]} -eq 0 ]; then
        echo "ERROR: AGENTS env not set"; exit 1
    fi
    RC=0
    for agent in "${AGENT_LIST[@]}"; do
        /usr/local/bin/agent-sync.sh "$agent" || RC=1
    done
    exit $RC
fi

# Parse target: agent or agent/folder
AGENT="${TARGET%%/*}"
SUBDIR="${TARGET#*/}"
[ "$SUBDIR" = "$TARGET" ] && SUBDIR=""  # no slash

SRC="$AGENTS_ROOT/$AGENT"
if [ -n "$SUBDIR" ]; then
    SRC="$SRC/$SUBDIR"
fi

if [ ! -d "$SRC" ]; then
    echo "$(date -Iseconds) [$TARGET] $SRC not found, skipping"
    exit 0
fi

DST="$DST_ROOT/$AGENT/"
[ -n "$SUBDIR" ] && DST="$DST_ROOT/$AGENT/$SUBDIR/"

# Per-target lock — retry up to 2 times if another sync holds it
LOCKFILE="/tmp/agent-sync-${AGENT}${SUBDIR:+-$(echo "$SUBDIR" | tr '/' '-')}.lock"
exec 9>"$LOCKFILE"
for attempt in 0 1 2; do
    flock -n 9 && break
    if [ "$attempt" -eq 2 ]; then
        echo "$(date -Iseconds) [$TARGET] lock busy after 2 retries, skipping"
        exit 0
    fi
    echo "$(date -Iseconds) [$TARGET] lock busy, waiting 30s (attempt $((attempt+1))/2)"
    sleep 30
done

UPDATE_FLAG="--update"
$FULL && UPDATE_FLAG=""

START=$(date +%s)
echo "$(date -Iseconds) [$TARGET] syncing $SRC/ -> $DST"

ossutil sync "$SRC/" "$DST" \
    $UPDATE_FLAG \
    -j 16 \
    --retry-times 2 \
    --read-timeout 120 \
    --bigfile-threshold 1M \
    --no-progress \
    --exclude "*/__pycache__/*" \
    --exclude "*/.git/*" \
    --exclude "*/node_modules/*" \
    --exclude "*/tasks/*/.lock" \
    2>&1 | tee /tmp/agent-sync-out.txt
RC=$?

DURATION=$(( $(date +%s) - START ))
SUMMARY="$(grep -E '^(Success|FinishWithError):' /tmp/agent-sync-out.txt | tail -1 || echo '(no summary)')"

# Upload metadata to OSS (best-effort)
META="$(jq -n \
    --arg timestamp "$(date -Iseconds)" \
    --arg target "$TARGET" \
    --arg src "$SRC" \
    --arg dst "$DST" \
    --argjson duration_seconds "$DURATION" \
    --argjson exit_code "$RC" \
    --arg summary "$SUMMARY" \
    '{timestamp: $timestamp, target: $target, src: $src, dst: $dst,
      duration_seconds: $duration_seconds, exit_code: $exit_code, summary: $summary}'
)"
echo "$META" | ossutil cp - "$DST_ROOT/.sync-meta/last_sync_${AGENT}.json" -f >/dev/null 2>&1 || true

if [ "$RC" -ne 0 ]; then
    echo "$(date -Iseconds) [$TARGET] FAILED exit=$RC duration=${DURATION}s"
else
    echo "$(date -Iseconds) [$TARGET] done in ${DURATION}s :: $SUMMARY"
fi

exec 9>&-
