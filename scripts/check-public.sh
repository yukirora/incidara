#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

required_agents=(claude-agent detection-agent triage-agent repair-agent recycler-agent feedback-agent attention-agent)
required_mcp=(patrol-cron node-operations agent-evidence agent-feedback switch-operations feishu-bitable)
required_skills=(job-incident-response job-log-triage system-evidence-diagnosis job-recovery training-reproduction rca-closeout)
required_paths=(ci/flow.yml compose/render.py compose/config.yaml.example infra/postgresql/init.sql infra/backup/agent/agent-sync.sh incidara_agents/.dockerignore incidara_agents/ltp-platform docs/incidara-agent-sre.md docs/roadmap.md)
excluded=(analyzer optimizer reproducer ltp-job-eva-agent tco ticket-replay-agent feedback-test-agent)

for name in "${required_agents[@]}"; do
  test -d "incidara_agents/agents/$name" || { echo "missing agent: $name" >&2; exit 1; }
done
for name in "${required_mcp[@]}"; do
  test -d "incidara_agents/mcp_servers/$name" || { echo "missing MCP server: $name" >&2; exit 1; }
done
for name in "${required_skills[@]}"; do
  test -d "incidara_agents/skills/$name" || { echo "missing operational skill: $name" >&2; exit 1; }
done
for path in "${required_paths[@]}"; do
  test -e "$path" || { echo "missing deployment dependency: $path" >&2; exit 1; }
done
test ! -e compose.yaml || { echo "misleading root compose.yaml present" >&2; exit 1; }
test ! -e incidara_agents/skills/pr-merge || { echo "incomplete pr-merge workflow present" >&2; exit 1; }
for name in "${excluded[@]}"; do
  test ! -e "incidara_agents/agents/$name" || { echo "excluded agent present: $name" >&2; exit 1; }
done

tracked=$(git ls-files)
if grep -Eq '(^|/)(node_modules|\.venv|__pycache__|\.pytest_cache|dist)/' <<<"$tracked"; then
  echo "generated dependency/build directory is tracked" >&2
  exit 1
fi
if grep -Eq '(^|/)\.env$|\.(pyc|log|tsbuildinfo)$' <<<"$tracked"; then
  echo "secret or generated file is tracked" >&2
  exit 1
fi

patterns='codeup\.aliyun\.com/finalsystems|xingyunzhili\.com|shaipower\.com|10\.100\.[0-9]+\.[0-9]+|rootpass|oauth2:|BEGIN [A-Z ]*PRIVATE KEY|ssh-(rsa|ed25519) [A-Za-z0-9+/]{40,}|gh[opsu]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.|LTAI[A-Za-z0-9]{12,}|cli_[A-Za-z0-9]{12,}|tbl[A-Za-z0-9]{12,}|pt-[A-Za-z0-9_-]{20,}'
if rg -n -i --hidden --glob '!.git/**' --glob '!scripts/check-public.sh' --glob '!pnpm-lock.yaml' --glob '!package-lock.json' "$patterns" .; then
  echo "private endpoint, identity, key, or credential pattern found" >&2
  exit 1
fi

if rg -n --hidden --glob '!.git/**' --glob '!scripts/check-public.sh' --glob '!pnpm-lock.yaml' --glob '!package-lock.json' '(LTP Agents|LTP Agent|LTP Mesh|ltp-agents|ltp_agents|ltp-chat-ui|ltp_session)' .; then
  echo "old project branding found" >&2
  exit 1
fi

echo "public-safety checks passed"
