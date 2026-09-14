#!/bin/bash
# run-cron.sh — Cron scheduler for pgBackRest backups
#
# Runs inside the cron sidecar container.
# Reads BACKUP_ENABLED and schedule from env vars.
# If BACKUP_ENABLED=false, exits silently.

set -euo pipefail

if [ "${BACKUP_ENABLED:-true}" != "true" ]; then
    echo ">>> run-cron: BACKUP_ENABLED=$BACKUP_ENABLED, sleeping"
    exec sleep infinity
fi

# Render pgbackrest.conf (same as entrypoint does for db)
/usr/local/bin/render-pgbackrest-conf.sh

# Fix permissions
mkdir -p /var/lib/pgbackrest /var/log/pgbackrest /var/log/pgbackrest/incidents /tmp/pgbackrest
chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest /tmp/pgbackrest 2>/dev/null || true

# Install crontab for the postgres user
CRONTAB=$(mktemp)
cat > "$CRONTAB" <<EOF
${FULL_BACKUP_SCHEDULE:-0 20 * * 6}  pgbackrest --stanza=app --type=full backup >> /var/log/pgbackrest/cron.log 2>&1
${INCR_BACKUP_SCHEDULE:-0 20 * * 0-5}  pgbackrest --stanza=app --type=incr backup >> /var/log/pgbackrest/cron.log 2>&1
${HEALTH_CHECK_SCHEDULE:-*/5 * * * *}  pgbackrest --stanza=app info >> /var/log/pgbackrest/cron-health.log 2>&1
EOF

crontab -u postgres "$CRONTAB"
rm -f "$CRONTAB"

echo ">>> run-cron: crontab installed"
crontab -u postgres -l

# Run cron daemon in foreground
echo ">>> run-cron: starting cron daemon"
exec cron -f
