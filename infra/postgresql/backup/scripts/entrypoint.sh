#!/bin/bash
# entrypoint.sh — PostgreSQL + pgBackRest for K8s production
#
# Principle: PG MUST ALWAYS START. Backup is a feature, not a requirement.
# - Backup fails → DB keeps running (archive_guard handles this)
# - WAL volume lost → recover from OSS or pg_resetwal
# - Everything fails → fallback to plain PG without backup
#

BACKUP_ENABLED="${BACKUP_ENABLED:-true}"
WAL_VOLUME_MAX_MB="${WAL_VOLUME_MAX_MB:-2048}"

PGDATA="${PGDATA:-/var/lib/postgresql/data/pgdata}"
WAL_DIR="/var/lib/postgresql/wal"          # container mount point for pgwal-host
WAL_MOUNT=""                               # resolved in Step 0

log() { echo ">>> entrypoint: $1"; }

# ── Step 0: WAL Volume Setup ──
# Detect WAL volume — init container already handled creation/mount logic:
#   - If loop image exists on host → init container mounted it (regardless of BACKUP_ENABLED)
#   - If no loop image + BACKUP_ENABLED=true → init container created and mounted
#   - If no loop image + BACKUP_ENABLED=false → init container skipped, no loop mount
#
# Here we just detect what's available. We only use WAL volume if:
#   - K8s loop volume is mounted at $WAL_DIR/mount, OR
#   - Docker loop volume is mounted at $WAL_DIR/mount
# A bare pgwal-host bind mount at $WAL_DIR without loop is NOT enough —
# it's just the host directory for the loop image file, not for WAL data.

if mountpoint -q "${WAL_DIR}/mount" 2>/dev/null; then
    # K8s/Docker loop volume already mounted by init container
    WAL_MOUNT="${WAL_DIR}/mount/pg_wal"
    log "WAL loop volume detected at ${WAL_DIR}/mount"
elif [ "$BACKUP_ENABLED" = "true" ] && command -v setup-wal-volume.sh >/dev/null 2>&1; then
    # Docker: try loop volume setup (needs privileged mode)
    setup-wal-volume.sh 2>&1
    if mountpoint -q "${WAL_DIR}/mount" 2>/dev/null; then
        WAL_MOUNT="${WAL_DIR}/mount/pg_wal"
        log "Docker loop volume created at ${WAL_DIR}/mount"
    fi
fi

if [ -z "$WAL_MOUNT" ]; then
    # Docker bind mount fallback (no size limiting) — only when backup is enabled
    # In K8s with backup disabled, pgwal-host is just a host directory for loop image,
    # NOT for WAL data. pg_wal should stay in PGDATA.
    if [ "$BACKUP_ENABLED" = "true" ] && mountpoint -q "$WAL_DIR" 2>/dev/null; then
        WAL_MOUNT="$WAL_DIR/pg_wal"
        log "Docker bind mount fallback at $WAL_DIR (no size limiting)"
        mkdir -p "$WAL_MOUNT"
        chown 999:999 "$WAL_MOUNT" 2>/dev/null || true
    else
        log "No WAL loop volume available — pg_wal stays in PGDATA"
    fi
fi

# ── Step 1: Check WAL Volume ──
WAL_VOLUME_OK=false
if [ -d "$WAL_MOUNT" ] && ls "$WAL_MOUNT/"*.0* >/dev/null 2>&1; then
    WAL_VOLUME_OK=true
    log "WAL volume available with existing WAL files"
elif [ -d "$WAL_MOUNT" ]; then
    WAL_VOLUME_OK=true
    log "WAL volume available (empty)"
fi

# ── Step 2: Configure pg_wal location ──
if [ -d "$PGDATA" ] && [ -f "$PGDATA/PG_VERSION" ]; then
    # Existing database
    if [ "$WAL_VOLUME_OK" = "true" ]; then
        if [ ! -L "$PGDATA/pg_wal" ] && [ -d "$PGDATA/pg_wal" ]; then
            # pg_wal is real dir — migrate to WAL volume
            log "Migrating pg_wal to WAL volume"
            cp -a "$PGDATA/pg_wal/." "$WAL_MOUNT/" 2>/dev/null || true
            rm -rf "$PGDATA/pg_wal"
            ln -s "$WAL_MOUNT" "$PGDATA/pg_wal"
        elif [ -L "$PGDATA/pg_wal" ]; then
            # Already symlinked — ensure target exists
            if [ ! -d "$PGDATA/pg_wal" ]; then
                log "Symlink target missing, recreating"
                mkdir -p "$WAL_MOUNT"
                chown 999:999 "$WAL_MOUNT" 2>/dev/null || true
            fi
        elif [ ! -e "$PGDATA/pg_wal" ]; then
            # pg_wal missing — create symlink
            log "pg_wal missing, creating symlink to WAL volume"
            mkdir -p "$WAL_MOUNT"
            chown 999:999 "$WAL_MOUNT" 2>/dev/null || true
            ln -s "$WAL_MOUNT" "$PGDATA/pg_wal"
        fi
        # Ensure symlink exists
        if [ ! -L "$PGDATA/pg_wal" ]; then
            ln -s "$WAL_MOUNT" "$PGDATA/pg_wal"
        fi
    else
        # WAL volume not available — pg_wal stays in PGDATA
        log "WAL volume not available, pg_wal in PGDATA (backup degraded)"
        if [ -L "$PGDATA/pg_wal" ]; then
            # Symlink exists but broken — need to create real directory
            log "Removing broken symlink, creating pg_wal in PGDATA"
            rm -f "$PGDATA/pg_wal"
            mkdir -p "$PGDATA/pg_wal/archive_status"
            chown 999:999 "$PGDATA/pg_wal" "$PGDATA/pg_wal/archive_status" 2>/dev/null || true
        elif [ ! -e "$PGDATA/pg_wal" ]; then
            mkdir -p "$PGDATA/pg_wal/archive_status"
            chown 999:999 "$PGDATA/pg_wal" "$PGDATA/pg_wal/archive_status" 2>/dev/null || true
        fi
    fi
fi

# ── Step 3: pgBackRest config ──
# In K8s, pgbackrest.conf is mounted from a Secret — skip rendering.
# In Docker, no mount exists — render from env vars.
if [ "$BACKUP_ENABLED" = "true" ] && [ "$WAL_VOLUME_OK" = "true" ]; then
    if [ ! -f /etc/pgbackrest/pgbackrest.conf ] || ! grep -q "repo1-type" /etc/pgbackrest/pgbackrest.conf 2>/dev/null; then
        /usr/local/bin/render-pgbackrest-conf.sh
    else
        log "pgbackrest.conf already exists (mounted from Secret)"
    fi
fi

# ── Step 4: Configure archive_mode ──
if [ -d "$PGDATA" ] && [ -f "$PGDATA/PG_VERSION" ]; then
    CONF="$PGDATA/postgresql.conf"

    if [ "$BACKUP_ENABLED" = "true" ] && [ "$WAL_VOLUME_OK" = "true" ]; then
        # archive_mode=on from the start — archive_guard returns 1 (retry)
        # if pgbackrest stanza doesn't exist yet, so PG keeps WAL until stanza-create.
        if ! grep -q "^archive_mode = on" "$CONF" 2>/dev/null; then
            # Remove any existing pgBackRest archiving lines first
            sed -i '/^archive_mode/d; /^archive_command/d; /^restore_command/d; /^archive_timeout/d; /^wal_level/d; /^wal_keep_size/d; /^max_wal_senders/d; /^checkpoint_completion_target/d; /pgBackRest/d' "$CONF"
            log "Appending WAL archiving settings"
            {
                echo ''
                echo '# ===== pgBackRest WAL archiving ====='
                echo 'archive_mode = on'
                echo "archive_command = '/usr/local/bin/pgbackrest-archive-guard.sh %p %f'"
                echo "restore_command = 'pgbackrest --stanza=app archive-get %f %p'"
                echo 'archive_timeout = 300'
                echo 'wal_level = replica'
                echo 'wal_keep_size = 1GB'
                echo 'max_wal_senders = 3'
                echo 'checkpoint_completion_target = 0.9'
            } >> "$CONF"
        fi
    else
        # Disable archiving
        if grep -q "archive_mode = on" "$CONF" 2>/dev/null; then
            log "Disabling WAL archiving (backup degraded or WAL volume unavailable)"
            sed -i 's/^archive_mode = on/archive_mode = off/' "$CONF"
        fi
    fi
fi

# ── Step 5: WAL recovery (only when backup_label exists from pgBackRest restore) ──
# Empty pg_wal on existing DB → pg_resetwal (not recovery — no stanza yet)
# backup_label present → recovery from pgBackRest restore
if [ -d "$PGDATA" ] && [ -f "$PGDATA/PG_VERSION" ]; then
    if [ -f "$PGDATA/backup_label" ]; then
        log "backup_label found — PG data was restored, creating recovery.signal"
        touch "$PGDATA/recovery.signal"
        chown 999:999 "$PGDATA/recovery.signal" 2>/dev/null || true
    fi

    # pg_wal empty with no backup_label — run pg_resetwal
    WAL_COUNT=$(find "$PGDATA/pg_wal/" -name '*.0*' 2>/dev/null | wc -l)
    if [ "$WAL_COUNT" -eq 0 ] && [ ! -f "$PGDATA/backup_label" ]; then
        # Empty pg_wal on existing DB — PG cannot start without a checkpoint record
        # Remove any leftover recovery.signal
        rm -f "$PGDATA/recovery.signal" 2>/dev/null || true
        log "pg_wal is empty on existing DB — running pg_resetwal"
        gosu postgres pg_resetwal -f "$PGDATA" 2>&1 || {
            log "pg_resetwal failed — will attempt PG start anyway"
        }

        # pg_resetwal may reset postgresql.conf — re-apply archive settings if backup is enabled
        if [ "$BACKUP_ENABLED" = "true" ] && [ "$WAL_VOLUME_OK" = "true" ]; then
            CONF="$PGDATA/postgresql.conf"
            if ! grep -q "^archive_mode = on" "$CONF" 2>/dev/null; then
                # Remove any partial pgBackRest lines first
                sed -i '/^archive_mode/d; /^archive_command/d; /^restore_command/d; /^archive_timeout/d; /^wal_level/d; /^wal_keep_size/d; /^max_wal_senders/d; /^checkpoint_completion_target/d; /pgBackRest/d' "$CONF"
                log "Re-applying WAL archiving settings after pg_resetwal"
                {
                    echo ''
                    echo '# ===== pgBackRest WAL archiving ====='
                    echo 'archive_mode = on'
                    echo "archive_command = '/usr/local/bin/pgbackrest-archive-guard.sh %p %f'"
                    echo "restore_command = 'pgbackrest --stanza=app archive-get %f %p'"
                    echo 'archive_timeout = 300'
                    echo 'wal_level = replica'
                    echo 'wal_keep_size = 1GB'
                    echo 'max_wal_senders = 3'
                    echo 'checkpoint_completion_target = 0.9'
                } >> "$CONF"
            fi
        fi
    fi
fi

# Pass --waldir to initdb for fresh databases
if [ "$WAL_VOLUME_OK" = "true" ]; then
    export POSTGRES_INITDB_ARGS="${POSTGRES_INITDB_ARGS:-} --waldir=$WAL_MOUNT"
fi

# ── Step 6: pgBackRest stanza setup (background, after PG starts) ──
if [ "$BACKUP_ENABLED" = "true" ] && [ "$WAL_VOLUME_OK" = "true" ]; then
    (
        for i in $(seq 1 60); do
            if pg_isready -U "${POSTGRES_USER:-root}" -d "${POSTGRES_DB:-openpai}" -q 2>/dev/null; then
                break
            fi
            sleep 1
        done
        mkdir -p /var/lib/pgbackrest /var/log/pgbackrest /var/log/pgbackrest/incidents /tmp/pgbackrest 2>/dev/null
        chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest /var/log/pgbackrest/incidents /tmp/pgbackrest 2>/dev/null || true

        # Auto-promote if PG is in recovery mode after restore.
        # Resuming replay is not enough; promotion must make the DB writable.
        if psql -U "${POSTGRES_USER:-root}" -d "${POSTGRES_DB:-openpai}" -Atc 'SELECT pg_is_in_recovery();' 2>/dev/null | grep -qx 't'; then
            log "PG in recovery mode — promoting"
            gosu postgres pg_ctl -D "$PGDATA" promote -w -t "${RESTORE_TIMEOUT_SECONDS:-1800}" 2>&1 || true
        fi

        gosu postgres pgbackrest --stanza=app stanza-create 2>&1 || \
            gosu postgres pgbackrest --stanza=app stanza-upgrade 2>&1 || true
        log "pgBackRest stanza ready — archive-push will succeed from now"

        # Check for bypass incidents — if found, trigger full backup to establish new baseline
        if [ -f /var/log/pgbackrest/incidents/bypass.log ] && [ -s /var/log/pgbackrest/incidents/bypass.log ]; then
            log "Bypass incidents detected — triggering full backup for new baseline"
            gosu postgres pgbackrest --stanza=app --type=full backup 2>&1 || true
            mv /var/log/pgbackrest/incidents/bypass.log /var/log/pgbackrest/incidents/bypass.log.old 2>/dev/null || true
        fi

        log "pgBackRest setup done"
    ) &
fi

# ── Step 7: Start PostgreSQL ──
log "Starting PostgreSQL (backup_enabled=$BACKUP_ENABLED, wal_volume=$WAL_VOLUME_OK)"

# ── Step 8: Start cron for pgBackRest scheduled backups ──
if [ "${BACKUP_ENABLED:-false}" = "true" ]; then
    (
        until pg_isready -U "${POSTGRES_USER:-postgres}" -q; do sleep 2; done
        log "PG ready, installing backup crontab"

        mkdir -p /etc/crontabs
        cat > /etc/crontabs/root <<CRONEOF
PATH=/usr/local/bin:/usr/bin:/bin
${FULL_BACKUP_SCHEDULE:-0 20 * * *}  gosu postgres pgbackrest --stanza=app --type=full backup >> /var/log/pgbackrest/cron.log 2>&1
${INCR_BACKUP_SCHEDULE:-0 * * * *}  gosu postgres pgbackrest --stanza=app --type=incr backup >> /var/log/pgbackrest/cron.log 2>&1
${HEALTH_CHECK_SCHEDULE:-*/5 * * * *}  gosu postgres pgbackrest --stanza=app info >> /var/log/pgbackrest/cron-health.log 2>&1
CRONEOF

        log "Crontab installed: full=${FULL_BACKUP_SCHEDULE}, incr=${INCR_BACKUP_SCHEDULE}"
        cron
        log "cron started"
    ) &
fi

exec docker-entrypoint.sh "$@"
