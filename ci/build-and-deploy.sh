#!/bin/bash
# ci/build-and-deploy.sh
# Builds and deploys only the affected services.
# Runs on the private CI runner which is on the target machine.
# Runner has Docker daemon access (enableDockerDaemon: true), no sudo needed.
#
# Usage: ./ci/build-and-deploy.sh
# Expects: CHANGED_SERVICES env var (space-separated service names),
#          or reads from stdin (one per line). SKILL_CHANGED=1 forces a repo
#          sync even when no image needs rebuilding.

set -euo pipefail

# ── Config ──
# This MUST match repo_dir in config.yaml — containers volume-mount skills from here
REPO_DIR="${REPO_DIR:-/data/agents/incidara}"
COMPOSE_DIR="$REPO_DIR/compose"
RENDERED_DIR="$COMPOSE_DIR/rendered"

# ── Allowlist: ONLY services managed by Docker Compose ──
COMPOSE_SERVICES="
  agent-backup
  agent-db
  agent-db-cron
  agent-evidence
  agent-feedback
  agent-feedback-write
  attention-agent
  chat-ui-db
  chat-ui-db-cron
  detection-agent
  feedback-agent
  job-patrol
  incidara-console-api
  incidara-console-web
  node-ops-diagnosis
  node-ops-feedback
  node-ops-ops
  patrol-cron
  recycler-agent
  repair-agent
  switch-operations
  triage-agent
"

# ── Read affected services ──
if [ -n "${CHANGED_SERVICES:-}" ]; then
    SERVICES="$CHANGED_SERVICES"
else
    SERVICES=$(cat | tr '\n' ' ')
fi

if [ -z "$SERVICES" ] && [ "${SKILL_CHANGED:-0}" != "1" ]; then
    echo ">>> No services or skills changed"
    exit 0
fi

echo ">>> Affected services (raw): $SERVICES"

# ── Filter: only deploy services in the compose allowlist ──
FILTERED=""
for svc in $SERVICES; do
    if echo "$COMPOSE_SERVICES" | grep -qw "$svc"; then
        FILTERED="$FILTERED $svc"
    else
        echo ">>> Skipping $svc (not managed by Docker Compose)"
    fi
done
SERVICES=$(echo "$FILTERED" | xargs)

if [ -z "$SERVICES" ] && [ "${SKILL_CHANGED:-0}" != "1" ]; then
    echo ">>> No compose-managed services to rebuild"
    exit 0
fi

echo ">>> Deploying: $SERVICES"

# ── Sync repo to deployment path ──
echo ">>> Syncing repo to $REPO_DIR..."
mkdir -p "$(dirname "$REPO_DIR")"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'compose/config.yaml' \
  --exclude 'compose/rendered/' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude 'node_modules' \
  --exclude 'dist' \
  --exclude 'console/config/agents.yaml' \
  --exclude 'console/config/groups.yaml' \
  --exclude 'console/db/data/' \
  --exclude '.gsd/' \
  --exclude '.env' \
  ./ "$REPO_DIR/"

# ── config.yaml is written by flow.yml from encrypted pipeline variable ──
if [ ! -f "$COMPOSE_DIR/config.yaml" ]; then
    echo ">>> ERROR: config.yaml not found at $COMPOSE_DIR/config.yaml"
    echo ">>> Ensure CONFIG_YAML_B64 pipeline variable is set"
    exit 1
fi
echo ">>> config.yaml OK"

if [ -z "$SERVICES" ]; then
    echo ">>> Skill-only change synced; new sessions will load the updated skills"
    exit 0
fi

# ── Render compose config ──
echo ">>> Rendering compose config..."
cd "$COMPOSE_DIR"
python3 render.py

cd "$RENDERED_DIR"

# Optional services (for example backup/cron profiles) may not be present in
# the rendered deployment. Ignore those instead of failing the whole rollout.
AVAILABLE_SERVICES=$(docker compose config --services)
ACTIVE_SERVICES=""
for svc in $SERVICES; do
    if echo "$AVAILABLE_SERVICES" | grep -qx "$svc"; then
        ACTIVE_SERVICES="$ACTIVE_SERVICES $svc"
    else
        echo ">>> Skipping $svc (not enabled in rendered Compose config)"
    fi
done
SERVICES=$(echo "$ACTIVE_SERVICES" | xargs)

if [ -z "$SERVICES" ]; then
    echo ">>> No enabled services to deploy"
    exit 0
fi

# ── Build + recreate each service ──
# Deduplicate images: multiple instances of same image (e.g. node-ops) only build once
IMAGES_BUILT=""
for svc in $SERVICES; do
    image=$(docker compose config --images "$svc" 2>/dev/null | head -1)
    if echo "$IMAGES_BUILT" | grep -qw "$image"; then
        echo ">>> Skipping build for $svc (image $image already built)"
        continue
    fi
    echo ""
    echo "=========================================="
    echo ">>> Building: $svc (image: $image)"
    echo "=========================================="
    docker compose build "$svc"
    IMAGES_BUILT="$IMAGES_BUILT $image"
done

# ── Recreate each service ──
for svc in $SERVICES; do
    echo ""
    echo ">>> Deploying: $svc"
    docker compose up -d --no-deps --force-recreate "$svc"

    echo ">>> Waiting for $svc to be healthy..."
    timeout 120 sh -c "until docker inspect --format='{{.State.Health.Status}}' \$(docker compose ps -q $svc) 2>/dev/null | grep -q healthy; do sleep 2; done" || \
        echo ">>> WARNING: $svc did not become healthy within 120s"
done

echo ""
echo ">>> All affected services deployed"
echo ">>> Services:"
docker compose ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null | head -30
