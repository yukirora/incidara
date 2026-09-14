#!/bin/bash
# ci/detect-changed-services.sh
# Detects which services need rebuild based on changed files.
# Outputs two things:
#   stdout: service names (one per line) that need rebuild+deploy
#   stderr: SKILL_CHANGED=1 if skills changed (only need rsync + restart)
#
# Usage: ./ci/detect-changed-services.sh [base_ref]
#   base_ref defaults to origin/main

set -euo pipefail

BASE_REF="${1:-origin/main}"
MAP_FILE="$(dirname "$0")/service-path-map.tsv"

# Get changed files
if git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    CHANGED_FILES=$(git diff --name-only --diff-filter=ACMR "$BASE_REF"...HEAD)
else
    CHANGED_FILES=$(git ls-files)
fi

if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

# ── Check skill changes (volume-mounted, no rebuild needed) ──
if echo "$CHANGED_FILES" | grep -qE '^incidara_agents/skills/'; then
    echo "SKILL_CHANGED=1" >&2
fi

# ── Check compose/ infrastructure changes → rebuild all ──
if echo "$CHANGED_FILES" | grep -qE '^compose/(render\.py|.*\.yml\.j2|Makefile|config\.yaml\.example)$'; then
    echo "COMPOSE_INFRA_CHANGED=1" >&2
    grep -v '^#' "$MAP_FILE" | grep -v '^$' | awk -F'\t' '{print $1}' | sort -u
    exit 0
fi

# ── Check env.j2 changes → re-render + recreate that service ──
# env.j2 files are matched by the path map (they're in the service dir)

# ── Check each changed file against the path map ──
AFFECTED=""
while IFS=$'\t' read -r service path_pattern; do
    [ -z "$service" ] && continue
    [[ "$service" == \#* ]] && continue
    if echo "$CHANGED_FILES" | grep -qE "$path_pattern"; then
        AFFECTED="$AFFECTED $service"
    fi
done < <(grep -v '^#' "$MAP_FILE" | grep -v '^$')

# Deduplicate and output
echo "$AFFECTED" | tr ' ' '\n' | grep -v '^$' | sort -u
