#!/bin/bash
# pgbackrest-archive-guard.sh — WAL archive wrapper with disk safety policy
#
# Called by PostgreSQL archive_command: pgbackrest-archive-guard.sh %p %f
# Wraps pgbackrest archive-push with WAL volume size checks.
#
# Behavior:
#   1. Run pgbackrest --stanza=app archive-push <wal-path>
#   2. Exit 0 when archive succeeds
#   3. Exit 1 when archive fails and WAL volume has headroom → PG retries (keeps WAL)
#   4. Exit 0 when archive fails and WAL volume near full → PG recycles WAL (saves DB, breaks PITR)
#
# NEVER deletes files from pg_wal.

set -euo pipefail

WAL_PATH="$1"
WAL_FILE="$2"

# Detect WAL dir: Docker bind mount ($WAL_DIR/pg_wal) or K8s loop ($WAL_DIR/mount/pg_wal)
if [ -d "/var/lib/postgresql/wal/pg_wal" ]; then
    WAL_DIR="/var/lib/postgresql/wal/pg_wal"
else
    WAL_DIR="/var/lib/postgresql/wal/mount/pg_wal"
fi
WAL_VOLUME_MAX_MB="${WAL_VOLUME_MAX_MB:-2048}"   # must match loop file size
WAL_BYPASS_PCT="${WAL_BYPASS_PCT:-70}"            # above 70% usage → emergency bypass
INCIDENT_DIR="/var/log/pgbackrest/incidents"
LOG_FILE="/var/log/pgbackrest/archive-guard.log"

log() {
    echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") archive-guard $1" >> "$LOG_FILE"
}

get_wal_dir_mb() {
    du -sm "$WAL_DIR" 2>/dev/null | cut -f1 || echo 0
}

wal_mb=$(get_wal_dir_mb)
wal_pct=$(( wal_mb * 100 / WAL_VOLUME_MAX_MB ))

log "archiving WAL=$WAL_FILE wal_size=${wal_mb}MB wal_pct=${wal_pct}% max=${WAL_VOLUME_MAX_MB}MB"

# Try archive-push
if pgbackrest --stanza=app archive-push "$WAL_PATH" >> "$LOG_FILE" 2>&1; then
    log "SUCCESS WAL=$WAL_FILE"
    exit 0
fi

# Archive failed — check WAL volume usage
log "ARCHIVE_FAILED WAL=$WAL_FILE wal_size=${wal_mb}MB wal_pct=${wal_pct}%"

if [ "$wal_pct" -ge "$WAL_BYPASS_PCT" ]; then
    # WAL volume near full — bypass to prevent ENOSPC
    log "EMERGENCY_BYPASS WAL=$WAL_FILE wal_pct=${wal_pct}% impact=pitr_gap"
    
    # Write incident record
    mkdir -p "$INCIDENT_DIR"
    echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") EMERGENCY_BYPASS WAL=$WAL_FILE wal_pct=${wal_pct}%" \
        >> "$INCIDENT_DIR/bypass.log"
    
    # Exit 0 → PG recycles WAL → DB keeps running, node safe
    exit 0
fi

# WAL volume has headroom — return 1 so PostgreSQL keeps WAL and retries
log "RETRY WAL=$WAL_FILE wal_pct=${wal_pct}%"
exit 1
