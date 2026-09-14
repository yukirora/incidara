#!/bin/bash
# db_query.sh — Safe read-only SQL wrapper for the LTP PostgreSQL database
#
# This script:
#   1. Connects to the database with configured credentials
#   2. Enforces read-only: scans SQL for write statements and rejects them
#   3. Passes the query through to psql
#
# All database access MUST go through this script.
#
# Usage:
#   ./db_query.sh <sql_file> [psql_var=value ...]
#   echo "SELECT 1" | ./db_query.sh -

set -euo pipefail

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER}"
DB_PASS="${DB_PASS}"
DB_NAME="${DB_NAME:-openpai}"

SQL_FILE="${1:?Usage: $0 <sql_file|-> [var=value ...]}"
shift

# Build -v flags for psql variables
VARS=()
for arg in "$@"; do
    VARS+=(-v "$arg")
done

# --- Safety: reject write statements ---
check_readonly() {
    local sql_upper
    sql_upper=$(echo "$1" | tr '[:lower:]' '[:upper:]')
    for pattern in "INSERT " "UPDATE " "DELETE " "DROP " "ALTER " "TRUNCATE " "CREATE " "GRANT " "REVOKE " "COPY "; do
        if [[ "$sql_upper" == *"$pattern"* ]]; then
            echo "STOP: Your query contains '$pattern' which modifies the database. You should not do this." >&2
            echo "You are a read-only investigation agent. Only SELECT queries are allowed." >&2
            echo "If you need to write data, report the finding and let the execute-triage phase handle it." >&2
            exit 1
        fi
    done
}

if [ "$SQL_FILE" = "-" ]; then
    SQL_CONTENT=$(cat)
    check_readonly "$SQL_CONTENT"
    echo "$SQL_CONTENT" | PGPASSWORD="$DB_PASS" psql \
        -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -P pager=off \
        "${VARS[@]+"${VARS[@]}"}"
else
    if [ ! -f "$SQL_FILE" ]; then
        echo "ERROR: SQL file not found: $SQL_FILE" >&2
        exit 1
    fi
    check_readonly "$(cat "$SQL_FILE")"
    PGPASSWORD="$DB_PASS" psql \
        -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -P pager=off \
        "${VARS[@]+"${VARS[@]}"}" \
        -f "$SQL_FILE"
fi
