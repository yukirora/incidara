#!/bin/bash
# render-pgbackrest-conf.sh — Generate pgbackrest.conf entirely from env vars
# No template files needed — all config comes from the environment
# (K8s: env vars from Secret + ConfigMap; Docker: env vars from .env)
#
# PG socket: pgbackrest connects via local socket by default.
# For Docker sidecar: share /var/run/postgresql/ as named volume between
# DB and cron containers. pg1-socket-path tells pgbackrest where to find it.

CONF="/etc/pgbackrest/pgbackrest.conf"

PG_PORT="${PG_PORT:-5432}"
PG_DATABASE="${PG_DATABASE:-openpai}"
PG_USER="${PG_USER:-root}"
PG_SOCKET_PATH="${PG_SOCKET_PATH:-/var/run/postgresql}"

# Build [app] section
APP_SECTION="pg1-path=${PGDATA:-/var/lib/postgresql/data}
pg1-port=${PG_PORT}
pg1-database=${PG_DATABASE}
pg1-user=${PG_USER}
pg1-socket-path=${PG_SOCKET_PATH}"

if [ -n "${OSS_ACCESS_KEY_ID:-}" ] && [ -n "${OSS_ACCESS_KEY_SECRET:-}" ]; then
    echo ">>> Rendering pgbackrest.conf for OSS/S3 repo"
    cat > "$CONF.tmp" <<EOF
[global]
repo1-type=s3
repo1-s3-bucket=${OSS_BUCKET:-ltp-data}
repo1-s3-endpoint=${OSS_ENDPOINT:-oss-cn-shanghai.aliyuncs.com}
repo1-s3-region=${OSS_REGION:-cn-shanghai}
repo1-s3-uri-style=${OSS_URI_STYLE:-host}
repo1-path=${OSS_REPO_PATH:-/postgresql/openpai}
repo1-retention-full=${RETENTION_FULL:-4}
repo1-retention-diff=${RETENTION_DIFF:-30}
repo1-s3-key=${OSS_ACCESS_KEY_ID}
repo1-s3-key-secret=${OSS_ACCESS_KEY_SECRET}
start-fast=y
process-max=2
log-level-console=info
log-level-file=detail
log-path=/var/log/pgbackrest

[app]
${APP_SECTION}
EOF
else
    echo ">>> No OSS credentials found, using local repo config"
    cat > "$CONF.tmp" <<EOF
[global]
repo1-type=local
repo1-path=/var/lib/pgbackrest
repo1-retention-full=${RETENTION_FULL:-4}
repo1-retention-diff=${RETENTION_DIFF:-30}
start-fast=y
process-max=2
log-level-console=info
log-level-file=detail
log-path=/var/log/pgbackrest

[app]
${APP_SECTION}
EOF
fi

chmod 644 "$CONF.tmp"
mv -f "$CONF.tmp" "$CONF"
