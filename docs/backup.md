# Agent Data Backup Service — Design

## What to Back Up

| Agent Dir | Contents | Size | Change Rate | Priority |
|---|---|---|---|---|
| `workspace/sessions/` | Chat UI session transcripts (JSONL) | 335M (repair) | Per-session | **Critical** |
| `claude-home/projects/` | Claude Code session transcripts (JSONL+TXT) | 252M (repair), 1.5G (detection) | Per-session | **Critical** |
| `claude-home/settings.json` | Permission rules, MCP config | <5K | Rare | Medium |
| `claude-home/session-env/` | Per-session env vars | <1M | Per-session | Low |
| `workspace/reports/` | Triage/repair output reports | Variable | Per-task | Medium |
| `workspace/evidence/` | Investigation evidence files | Variable | Per-task | Medium |
| `workspace/*-work/` | Working files (triage results, etc.) | Variable | Per-task | Medium |
| `skills/` | Skill files | <3M total | Rare (git-managed) | Low |
| `claude-home/backups/` | Claude internal backups | <24K | Rare | Low |
| `claude-home/tasks/` | Task state files | <1M | Per-task | Low |

**NOT backed up** (separate systems):
- `pgdata/` — already has pgBackRest → OSS
- `incidara/` repo — in git
- `chat-ui/` data — chat-ui-db has pgBackRest

## Total Size: ~4GB across 6 agents

## OSS Path Structure

```
oss://ltp-data/agents-backup/
├── {hostname}/                          # e.g. ai-infra-23
│   ├── {agent}/                         # e.g. repair, triage-unknown
│   │   ├── workspace/
│   │   │   ├── sessions/               # Chat UI session transcripts
│   │   │   ├── reports/                # Task output reports
│   │   │   ├── evidence/              # Investigation evidence
│   │   │   └── *-work/                # Working files
│   │   └── claude-home/
│   │       ├── projects/              # Claude Code transcripts
│   │       ├── settings.json          # MCP + permissions config
│   │       ├── session-env/           # Per-session env
│   │       └── tasks/                 # Task state
│   └── backup-meta/
│       ├── latest.json                # Last backup timestamp + stats per agent
│       └── history.jsonl              # Append-only backup history log
```

## Backup Strategy: ossutil + cron

### Why not pgBackRest-style?
- Agent data is **files**, not a WAL-based DB
- No transactional consistency needed (each session file is independent)
- `ossutil cp -r --update` does incremental by mtime/size — perfect for file trees
- Simple, reliable, no custom code needed

### Why not restic/borg?
- ~4GB total — overkill for dedup
- ossutil is native to Aliyun OSS, no compatibility layer
- One dependency to install, one config file

## Implementation

### 1. Install ossutil

```bash
curl -o /usr/local/bin/ossutil64 https://gosspublic.alicdn.com/ossutil/1.7.18/ossutil-v1.7.18-linux-amd64.zip
chmod +x /usr/local/bin/ossutil64
ossutil64 config  # endpoint, access-key-id, access-key-secret
```

### 2. Backup Script: `infra/backup/agent-backup.sh`

```bash
#!/bin/bash
# Agent data backup to Aliyun OSS
# Incremental via ossutil --update (skips unchanged files by mtime/size)
#
# Usage: agent-backup.sh [--full] [agent1 agent2 ...]
#   --full   Force full upload (ignore mtime)
#   agents   Backup specific agents only (default: all)

set -euo pipefail

AGENTS_ROOT="${AGENTS_ROOT:-/mntsys/agents}"
OSS_BUCKET="${OSS_BUCKET:-oss://ltp-data}"
OSS_PREFIX="${OSS_PREFIX:-agents-backup}"
HOSTNAME=$(hostname -s)
DRY_RUN="${DRY_RUN:-false}"
FULL=false
AGENTS=()

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --full) FULL=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) AGENTS+=("$1"); shift ;;
  esac
done

if [ ${#AGENTS[@]} -eq 0 ]; then
  AGENTS=(attention detection feedback recycler repair triage-unknown)
fi

UPDATE_FLAG="--update"
if $FULL; then
  UPDATE_FLAG=""
fi

DRY_FLAG=""
if $DRY_RUN; then
  DRY_FLAG="--dry-run"
fi

META_DIR="/tmp/agent-backup-meta"
mkdir -p "$META_DIR"

for agent in "${AGENTS[@]}"; do
  AGENT_DIR="$AGENTS_ROOT/$agent"
  if [ ! -d "$AGENT_DIR" ]; then
    echo "[SKIP] $agent: directory not found at $AGENT_DIR"
    continue
  fi

  echo "=== Backing up $agent ==="
  START=$(date +%s)

  # Backup workspace (excludes: __pycache__, .git, node_modules)
  for subdir in workspace claude-home; do
    SRC="$AGENT_DIR/$subdir"
    if [ ! -d "$SRC" ]; then
      continue
    fi
    DST="$OSS_BUCKET/$OSS_PREFIX/$HOSTNAME/$agent/$subdir/"

    echo "  $subdir -> $DST"
    ossutil64 cp -r $UPDATE_FLAG $DRY_FLAG \
      --exclude "__pycache__/*" \
      --exclude ".git/*" \
      --exclude "node_modules/*" \
      --exclude "*.pyc" \
      --force \
      "$SRC/" "$DST" 2>&1 | tail -1
  done

  END=$(date +%s)
  DURATION=$((END - START))

  # Record metadata
  TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  cat >> "$META_DIR/history.jsonl" <<EOF
{"agent":"$agent","timestamp":"$TIMESTAMP","duration_s":$DURATION,"type":"$([ "$FULL" = true ] && echo full || echo incremental)"}
EOF
done

# Upload metadata
ossutil64 cp -r --force "$META_DIR/" "$OSS_BUCKET/$OSS_PREFIX/$HOSTNAME/backup-meta/" 2>/dev/null

echo "=== Backup complete ==="
```

### 3. Cron Schedule

```bash
# /etc/cron.d/agent-backup
# Incremental backup every 6 hours
0 */6 * * * root /usr/local/bin/agent-backup.sh >> /var/log/agent-backup.log 2>&1
# Full backup weekly (Sunday 04:00 UTC)
0 4 * * 0 root /usr/local/bin/agent-backup.sh --full >> /var/log/agent-backup.log 2>&1
```

### 4. Restore

```bash
# Restore a single agent
ossutil64 cp -r oss://ltp-data/agents-backup/ai-infra-23/repair/ /mntsys/agents/repair-restored/

# Restore specific file
ossutil64 cp oss://ltp-data/agents-backup/ai-infra-23/repair/workspace/sessions/12345.jsonl /tmp/12345.jsonl
```

## Docker Compose Integration (Alternative)

Instead of host cron, run as a compose sidecar — consistent with the DB backup pattern:

```yaml
# In compose/databases.yml.j2 or new compose/backup.yml.j2
  agent-backup:
    image: alpine:3.20
    restart: unless-stopped
    volumes:
      - /mntsys/agents:/data/agents:ro
      - ./agent-backup.sh:/usr/local/bin/agent-backup.sh:ro
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        apk add --no-cache curl bash
        curl -o /usr/local/bin/ossutil64 ... && chmod +x /usr/local/bin/ossutil64
        echo "0 */6 * * * /usr/local/bin/agent-backup.sh" | crontab -
        crond -f
```

**But** this is more complex than host cron for little benefit. Agent data backup doesn't need Docker isolation. Recommend host cron for simplicity.

## Retention

- OSS lifecycle rule: keep all incremental backups indefinitely (4GB total is trivial vs 13TB capacity)
- If cost becomes a concern: add `--delete` flag to remove OSS files not in source (mirror mode)
- Weekly full backup ensures all files are present even if incremental misses deletions

## Cost Estimate

- 4GB stored × ¥0.12/GB/month ≈ ¥0.48/month (storage)
- Incremental uploads ~few MB/day × ¥0.50/GB (put requests) ≈ negligible
- Total: < ¥1/month
