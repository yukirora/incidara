#!/bin/bash
# restore-and-promote.sh — One-shot pgBackRest restore plus PostgreSQL promotion.
#
# Intended for compose/K8s restore jobs where an operator wants a restored DB
# to become a writable primary on the target host.

set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
RESTORE_TYPE="${RESTORE_TYPE:-immediate}"
RESTORE_PROMOTE="${RESTORE_PROMOTE:-true}"
RESTORE_KEEP_RUNNING="${RESTORE_KEEP_RUNNING:-false}"
RESTORE_TIMEOUT_SECONDS="${RESTORE_TIMEOUT_SECONDS:-1800}"
RESTORE_VERIFY_SQL="${RESTORE_VERIFY_SQL:-select current_database(), current_user, pg_is_in_recovery();}"
RESTORE_CLEAN_DATA="${RESTORE_CLEAN_DATA:-false}"
RESTORE_CLEAN_WAL="${RESTORE_CLEAN_WAL:-false}"

log() { echo ">>> restore: $1"; }
die() { echo "restore error: $1" >&2; exit 1; }

[ -n "${POSTGRES_USER:-}" ] || die "POSTGRES_USER is required"
[ -n "${POSTGRES_DB:-}" ] || die "POSTGRES_DB is required"

if [ "$RESTORE_CLEAN_DATA" = "true" ]; then
    log "cleaning PGDATA=$PGDATA"
    find "$PGDATA" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

if [ "$RESTORE_CLEAN_WAL" = "true" ]; then
    log "cleaning WAL mount /var/lib/postgresql/wal"
    find /var/lib/postgresql/wal -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
fi

log "rendering pgBackRest config"
/usr/local/bin/render-pgbackrest-conf.sh

log "preparing restore directories"
mkdir -p "$PGDATA" /var/lib/pgbackrest /var/log/pgbackrest /tmp/pgbackrest
chown -R postgres:postgres "$PGDATA" /var/lib/pgbackrest /var/log/pgbackrest /tmp/pgbackrest 2>/dev/null || true

log "running pgBackRest restore type=$RESTORE_TYPE"
gosu postgres pgbackrest --stanza=app --type="$RESTORE_TYPE" --delta restore

log "starting PostgreSQL for recovery"
/usr/local/bin/entrypoint.sh postgres &
pg_pid="$!"

cleanup() {
    status=$?
    if [ "$status" -ne 0 ] && kill -0 "$pg_pid" 2>/dev/null; then
        log "stopping PostgreSQL after restore failure"
        kill "$pg_pid" 2>/dev/null || true
        wait "$pg_pid" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT

deadline=$((SECONDS + RESTORE_TIMEOUT_SECONDS))
until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q; do
    if ! kill -0 "$pg_pid" 2>/dev/null; then
        wait "$pg_pid"
    fi
    if [ "$SECONDS" -gt "$deadline" ]; then
        die "timed out waiting for PostgreSQL to accept connections"
    fi
    sleep 2
done

if [ "$RESTORE_PROMOTE" = "true" ]; then
    if psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pg_is_in_recovery();" | grep -qx "t"; then
        log "promoting restored database"
        gosu postgres pg_ctl -D "$PGDATA" promote -w -t "$RESTORE_TIMEOUT_SECONDS"
    else
        log "database is already primary"
    fi
else
    log "promotion disabled by RESTORE_PROMOTE=$RESTORE_PROMOTE"
fi

log "verifying restored database"
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "$RESTORE_VERIFY_SQL"

if [ "$RESTORE_KEEP_RUNNING" != "true" ]; then
    log "stopping PostgreSQL after successful restore verification"
    gosu postgres pg_ctl -D "$PGDATA" stop -m fast -w -t 120
    wait "$pg_pid"
    trap - EXIT
    log "restore complete"
    exit 0
fi

trap - EXIT
log "restore complete; PostgreSQL remains in foreground because RESTORE_KEEP_RUNNING=true"
wait "$pg_pid"
