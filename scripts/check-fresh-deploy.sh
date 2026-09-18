#!/usr/bin/env bash
# check-fresh-deploy.sh — verify the shipped example config deploys from scratch.
#
# Starts a throwaway deployment from compose/config.yaml.example on empty state
# directories, then asserts the database schema is complete. This catches the
# failure mode that matters most on a clean host: the Postgres entrypoint runs
# init.sql with ON_ERROR_STOP=1, so one bad statement leaves a partial schema
# while the container still reports healthy.
#
# The example is used unchanged; only host-specific paths and ports are set,
# because those must point at this machine. Nothing touches compose/config.yaml
# or compose/rendered/.
#
# Usage: bash scripts/check-fresh-deploy.sh
# Env:   FRESH_PORT_OFFSET (default 21000) — keeps the check clear of a live stack

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT_OFFSET="${FRESH_PORT_OFFSET:-21000}"
PROJECT="incidara-fresh-check"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "SKIP: docker is not available; run this check on a host with a Docker daemon."
    exit 0
fi

WORK_DIR="$(mktemp -d)"
CONFIG="${WORK_DIR}/config.yaml"
RENDERED="${WORK_DIR}/rendered"
STATE="${WORK_DIR}/state"

cleanup() {
    docker compose -p "${PROJECT}" -f "${RENDERED}/databases.yml" down -v >/dev/null 2>&1 || true
    # Bind mounts are created root-owned, so remove the state through a container.
    docker run --rm -v "${WORK_DIR}:/work" postgres:16-alpine rm -rf /work/state >/dev/null 2>&1 || true
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

compose_db() { docker compose -p "${PROJECT}" -f "${RENDERED}/databases.yml" "$@"; }

echo "==> rendering the example config (unchanged) for this host"
python3 - "$REPO_DIR" "$CONFIG" "$STATE" <<'PY'
import sys
from pathlib import Path

import yaml

repo_dir, config_path, state = sys.argv[1:4]

# The only host-specific inputs: where state lives, and a port offset so the
# check can run next to a live deployment. Credentials stay as shipped.
config = yaml.safe_load((Path(repo_dir) / "compose" / "config.yaml.example").read_text())
config["common"]["state_root"] = state
Path(config_path).write_text(yaml.safe_dump(config, sort_keys=False))
PY

python3 "${REPO_DIR}/compose/render.py" --check --config "${CONFIG}" --port-offset "${PORT_OFFSET}" >/dev/null
python3 "${REPO_DIR}/compose/render.py" --config "${CONFIG}" --rendered-dir "${RENDERED}" --port-offset "${PORT_OFFSET}" >/dev/null

# Validate every service in the graph, not only the databases started below.
(cd "${RENDERED}" && docker compose -f docker-compose.yml config -q)

# Database containers have fixed names, so a running deployment would be disturbed.
for name in $(python3 -c "import yaml; print(' '.join(s.get('container_name', '') for s in yaml.safe_load(open('${RENDERED}/databases.yml'))['services'].values()))"); do
    if docker ps --format '{{.Names}}' | grep -qx "${name}"; then
        echo "SKIP: ${name} is already running; this check needs a host with no Incidara deployment (or one using another name prefix)."
        exit 0
    fi
done

if grep -q "BACKUP_ENABLED=true" "${RENDERED}/agent-db.env"; then
    echo "FAIL: the example enables database backup, so a clean deployment would need an OSS repository" >&2
    exit 1
fi

echo "==> starting databases on empty state directories"
compose_db up -d agent-db chat-ui-db >/dev/null

for _ in $(seq 1 60); do
    state="$(docker inspect -f '{{.State.Health.Status}}' $(compose_db ps -q agent-db chat-ui-db) 2>/dev/null | sort -u | tr '\n' ' ')"
    [ "${state}" = "healthy " ] && break
    sleep 2
done
[ "${state}" = "healthy " ] || { echo "FAIL: databases did not become healthy (${state})" >&2; exit 1; }

echo "==> checking the initialised schema"
expected_agent=$(( $(grep -c 'CREATE TABLE IF NOT EXISTS' "${REPO_DIR}/infra/postgresql/init.sql") \
                 + $(grep -c 'CREATE TABLE IF NOT EXISTS' "${REPO_DIR}/infra/postgresql/pricing.sql") ))
expected_console=$(grep -h 'CREATE TABLE IF NOT EXISTS' "${REPO_DIR}"/console/db/init/*.sql | wc -l | tr -d ' ')

# Take the credentials from the rendered deployment so the check follows the example.
agent_user=$(sed -n 's/^POSTGRES_USER=//p' "${RENDERED}/agent-db.env")
agent_db=$(sed -n 's/^POSTGRES_DB=//p' "${RENDERED}/agent-db.env")
console_user=$(sed -n 's/^POSTGRES_USER=//p' "${RENDERED}/chat-ui-db.env")
console_db=$(sed -n 's/^POSTGRES_DB=//p' "${RENDERED}/chat-ui-db.env")

fail=0
if compose_db logs agent-db 2>&1 | grep -qE "ERROR|FATAL"; then
    echo "FAIL: init.sql raised errors on a fresh database:" >&2
    compose_db logs agent-db 2>&1 | grep -E "ERROR|FATAL" | head -5 >&2
    fail=1
fi

agent_tables=$(compose_db exec -T agent-db psql -U "${agent_user}" -d "${agent_db}" -tAc \
    "select count(*) from information_schema.tables where table_schema='public'")
console_tables=$(compose_db exec -T chat-ui-db psql -U "${console_user}" -d "${console_db}" -tAc \
    "select count(*) from information_schema.tables where table_schema='public'")

[ "${agent_tables}" -eq "${expected_agent}" ] || {
    echo "FAIL: agent database has ${agent_tables} tables, init.sql declares ${expected_agent}" >&2; fail=1; }
[ "${console_tables}" -eq "${expected_console}" ] || {
    echo "FAIL: console database has ${console_tables} tables, console/db/init declares ${expected_console}" >&2; fail=1; }

[ "${fail}" -eq 0 ] || exit 1
echo "PASS: clean deployment initialised ${agent_tables} agent tables and ${console_tables} console tables"
