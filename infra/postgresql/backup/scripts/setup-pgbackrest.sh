#!/bin/bash
# setup-pgbackrest.sh — Runs during initdb phase (before first postgres start)
# Appends WAL archiving settings to postgresql.conf
#
# archive_mode starts OFF — entrypoint.sh enables it at runtime after stanza-create.
# This avoids archive_command failures before pgBackRest stanza exists.

set -e

CONF="$PGDATA/postgresql.conf"
BACKUP_ENABLED="${BACKUP_ENABLED:-true}"

echo ">>> setup-pgbackrest: BACKUP_ENABLED=$BACKUP_ENABLED"

# Always set wal_level=replica — cannot be changed without restart,
# and keeping it ready means backup can be enabled later without rebuilding.
printf '\n# ===== pgBackRest WAL archiving =====\n' >> "$CONF"
printf 'wal_level = replica\n' >> "$CONF"
printf 'wal_keep_size = 0\n' >> "$CONF"
printf 'max_wal_senders = 3\n' >> "$CONF"
printf 'checkpoint_completion_target = 0.9\n' >> "$CONF"

if [ "$BACKUP_ENABLED" = "true" ]; then
    # archive_mode=off — will be enabled by entrypoint after stanza-create
    printf 'archive_mode = off\n' >> "$CONF"
    printf "archive_command = '/usr/local/bin/pgbackrest-archive-guard.sh %%p %%f'\n" >> "$CONF"
    printf "restore_command = 'pgbackrest --stanza=app archive-get %%f %%p'\n" >> "$CONF"
    printf 'archive_timeout = 300\n' >> "$CONF"
    echo ">>> WAL archiving settings written (archive_mode will be enabled after stanza-create)"
else
    printf 'archive_mode = off\n' >> "$CONF"
    echo ">>> WAL archiving disabled (archive_mode=off)"
fi
