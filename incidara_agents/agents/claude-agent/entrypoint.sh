#!/bin/bash

# SSH agent — use host-mounted socket
if [ -S "$SSH_AUTH_SOCK" ]; then
  echo "Using host SSH agent: $SSH_AUTH_SOCK"
  ssh-add -l 2>/dev/null || echo "Warning: no keys in agent"
else
  echo "Warning: SSH_AUTH_SOCK not available"
fi

# LTP token — write env var to file so scripts can find it
if [ -n "$LTP_TOKEN" ]; then
  mkdir -p "$HOME/.ltp_tokens"
  echo "$LTP_TOKEN" > "$HOME/.ltp_tokens/${LTP_HOST_ADDR}_token"
  echo "LTP token written to $HOME/.ltp_tokens/${LTP_HOST_ADDR}_token"
fi

# Skills directory — optional; set by deployment to point at its skill set.
if [ -z "$CLAUDE_SKILL_DIR" ]; then
  echo "Note: CLAUDE_SKILL_DIR not set (deployment may override)"
fi

# Git identity — set from env vars if provided (for agent-committed repos)
if [ -n "$GIT_AUTHOR_NAME" ]; then
  git config --global user.name "$GIT_AUTHOR_NAME"
  git config --global user.email "$GIT_AUTHOR_EMAIL"
fi

# Warm up bind mounts — Docker overlay2 + nested bind mounts can cause spurious
# EROFS on first write after container start. Pre-creating dirs and touching files
# forces the overlay to establish write paths before the Claude SDK tries.
mkdir -p "$HOME/.claude/session-env"
mkdir -p /app/workspace/reports

# Touch and remove a temp file in each bind-mounted path to warm up the write path
for dir in "$HOME/.claude/session-env" /app/workspace /app/workspace/reports; do
  _tmp="${dir}/.mount-warmup-$$"
  touch "$_tmp" 2>/dev/null && rm -f "$_tmp" 2>/dev/null
done

# ---- Security baseline (shared across ALL agents) ----
# Write security deny/ask rules to settings.json if not already set by agent setup-mcp.sh.
# These prevent credential exposure, destructive file operations, and database writes.
# setup-mcp.sh may ADD to these but should NOT remove them.
python3 << 'SECEOF'
import json, os

BASELINE_ALLOW = [
    # All agents need these core capabilities
    "Read(*)",
    "Write(*)",
    "Edit(*)",
    "Bash(*)",
    "Skill(*)",
]

BASELINE_DENY = [
    # Credential EXPOSURE — block reading/printing/exfiltrating secrets,
    # but NOT using them as arguments (e.g. psql "$CHAT_UI_DB_URL" is fine).
    # Pattern: <exfil-command> *<secret-name>*
    "Bash(*printenv*)",
    "Bash(*cat /proc/*/environ*)",
    "Bash(*set | grep*)",
    # echo
    "Bash(*echo *PASS*)",
    "Bash(*echo *TOKEN*)",
    "Bash(*echo *SECRET*)",
    "Bash(*echo *KEY*)",
    "Bash(*echo *AUTH*)",
    "Bash(*echo *CRED*)",
    "Bash(*echo *LOGIN*)",
    "Bash(*echo *DATABASE_URL*)",
    "Bash(*echo *CONNECTION_STR*)",
    "Bash(*echo *POSTGRES*)",
    "Bash(*echo *LTP_TOKEN*)",
    "Bash(*echo *LTP_USER*)",
    "Bash(*echo *LTP_PASS*)",
    "Bash(*echo *API_KEY*)",
    "Bash(*echo *EVIDENCE_DB*)",
    # grep
    "Bash(*grep *PASS*)",
    "Bash(*grep *SECRET*)",
    "Bash(*grep *TOKEN*)",
    "Bash(*grep *KEY*)",
    "Bash(*grep *DATABASE_URL*)",
    "Bash(*grep *POSTGRES*)",
    "Bash(*grep *LTP_TOKEN*)",
    "Bash(*grep *API_KEY*)",
    # cat
    "Bash(*cat *.env*)",
    "Bash(*cat *credential*)",
    "Bash(*cat *config*json*)",
    # env/export — too many false positives; keep specific patterns below
    "Bash(*export *PASS*)",
    "Bash(*export *TOKEN*)",
    "Bash(*export *SECRET*)",
    "Bash(*export *KEY*)",
    "Bash(*export *DATABASE_URL*)",
    "Bash(*export *POSTGRES*)",
    "Bash(*export *LTP_TOKEN*)",
    "Bash(*export *API_KEY*)",
    # base64 decode of secrets
    "Bash(*base64*KEY*)",
    "Bash(*base64*TOKEN*)",
    "Bash(*base64*PASS*)",
    # JWT decode
    "Bash(*decode*JWT*)",
    # Destructive filesystem operations (hard-deny)
    "Bash(*rm -rf*)",
    "Bash(*rm -r *)",
    "Bash(*sudo rm*)",
    "Bash(*dd if*)",
    "Bash(*mkfs*)",
    "Bash(*fdisk*)",
    "Bash(*parted*)",
    "Bash(*shred*)",
    "Bash(*wipefs*)",
]

BASELINE_ASK = [
    # Database writes
    "Bash(*DELETE *)",
    "Bash(*DROP *)",
    "Bash(*TRUNCATE *)",
    "Bash(*delete from*)",
    "Bash(*drop table*)",
    "Bash(*truncate table*)",
    # Permission changes
    "Bash(*chmod 777*)",
    "Bash(*chown -R*)",
    # Irreversible node actions — require human approval
    "mcp__node-ops__run_kubectl_dangerous",
]

settings_path = os.path.expanduser("~/.claude/settings.json")
existing = {}
try:
    with open(settings_path) as f:
        existing = json.load(f)
except Exception:
    pass

if "permissions" not in existing:
    existing["permissions"] = {}

existing_allow = set(existing["permissions"].get("allow", []))
existing_allow.update(BASELINE_ALLOW)
existing["permissions"]["allow"] = sorted(existing_allow)

existing_deny = set(existing["permissions"].get("deny", []))
existing_deny.update(BASELINE_DENY)
existing["permissions"]["deny"] = sorted(existing_deny)

existing_ask = set(existing["permissions"].get("ask", []))
existing_ask.update(BASELINE_ASK)
existing["permissions"]["ask"] = sorted(existing_ask)

with open(settings_path, "w") as f:
    json.dump(existing, f, indent=2)

print(f"Security baseline applied: {len(existing_deny)} deny, {len(existing_ask)} ask rules")
SECEOF

exec node dist/server.js
