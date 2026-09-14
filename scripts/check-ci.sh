#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

bash -n ci/*.sh infra/backup/agent/*.sh infra/postgresql/backup/scripts/*.sh

grep -q $'^incidara-console-api\t\^console/' ci/service-path-map.tsv
grep -q $'^incidara-console-web\t\^console/' ci/service-path-map.tsv

# A skill-only change must still synchronize the repository even though it
# rebuilds no image. Use an isolated deployment root so the check has no side effects.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/compose"
cp compose/config.yaml.example "$tmp/compose/config.yaml"
REPO_DIR="$tmp" SKILL_CHANGED=1 CHANGED_SERVICES='' \
  bash ci/build-and-deploy.sh </dev/null >/dev/null

test -f "$tmp/incidara_agents/skills/job-incident-response/SKILL.md"
test -f "$tmp/compose/config.yaml"
echo "CI checks passed"
