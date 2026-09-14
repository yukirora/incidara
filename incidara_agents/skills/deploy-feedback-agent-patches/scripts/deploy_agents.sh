#!/bin/bash
# deploy_agents.sh — Deterministic agent deployment script.
# Detects which agents are affected by changes, rebuilds, and verifies health.
#
# Usage:
#   # Deploy from main after branches merged (diff vs last deploy tag):
#   ./deploy_agents.sh --from-deploy-tag deploy-20260520 [--no-cache] [--dry-run]
#
#   # Deploy from a branch (diff branch vs main):
#   ./deploy_agents.sh --branch gap-6-validation-log-routing [--no-cache] [--dry-run]
#
# Must be run on the remote host (192.0.2.10) where agents are deployed.

set -euo pipefail

# --- Args ---
BRANCH=""
NO_CACHE=""
DRY_RUN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)     BRANCH="$2"; shift 2 ;;
        --no-cache)   NO_CACHE="--no-cache"; shift ;;
        --dry-run)    DRY_RUN="1"; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ -z "$BRANCH" ]]; then
    echo "Usage: BUILD_ROOT=<path> $0 --branch <branch-name> [--no-cache] [--dry-run]"
    exit 1
fi

REPO_URL="git@codeup.aliyun.com:your-org/incidara.git"
: "${BUILD_ROOT:?BUILD_ROOT must be set (e.g. /home/operator/yutji)}"
BUILD_CONTEXT="${BUILD_ROOT}/incidara"
AGENTS_DIR="${BUILD_CONTEXT}/incidara_agents/agents"

# --- Deploy scope: only these agents are deployed on the host ---
DEPLOYED_AGENTS="triage-agent repair-agent recycler-agent feedback-agent"

get_agent_port() {
    local agent="$1"
    local makefile="${AGENTS_DIR}/${agent}/Makefile"
    if [[ ! -f "$makefile" ]]; then
        echo ""
        return
    fi
    # Try PORT = XXX first, then fall back to health check URL
    local port
    port=$(grep -oP '^PORT\s*[:?]?=\s*\K\d+' "$makefile" 2>/dev/null | head -1)
    if [[ -z "$port" ]]; then
        port=$(grep -oP '127\.0\.0\.1:\K\d+' "$makefile" 2>/dev/null | head -1)
    fi
    echo "$port"
}

# --- Skill-to-agent mapping ---
skill_to_agents() {
    local skill="$1"
    case "$skill" in
        triage-nodes|job-check)     echo "triage-agent" ;;
        repair-nodes)               echo "repair-agent" ;;
        repair-ticket-summary-methodology) echo "repair-agent" ;;
        skill-optimize|vendor-feedback|analyze-rma-cases|collect-rma-cases|case-diagnosis|patch-and-validate) echo "feedback-agent" ;;
        node-reallocation|ticket-check) echo "recycler-agent" ;;
        *)                          echo "" ;;
    esac
}

# --- MCP server-to-agent mapping ---
mcp_to_agents() {
    local mcp="$1"
    case "$mcp" in
        node-operations)  echo "triage-agent repair-agent recycler-agent feedback-agent" ;;
        agent-evidence)   echo "triage-agent repair-agent feedback-agent" ;;
        agent-feedback)   echo "feedback-agent" ;;
        ltp-operations)   echo "feedback-agent" ;;
        *)                echo "" ;;
    esac
}

# --- Step 1: Clone repo to tempdir and compute diff ---
WORK_DIR=$(mktemp -d /tmp/ltp-deploy-XXXXXX)
trap "rm -rf '${WORK_DIR}'" EXIT

echo "=== Cloning repo to ${WORK_DIR} ==="

git clone --branch "${BRANCH}" "${REPO_URL}" "${WORK_DIR}/repo"
cd "${WORK_DIR}/repo"

if [[ "$BRANCH" == "main" ]]; then
    echo ""
    echo "=== Changed files (HEAD~1..main) ==="
    CHANGED_FILES=$(git diff --name-only "HEAD~1..HEAD")
else
    git fetch origin main 2>/dev/null || true
    echo ""
    echo "=== Changed files (main..${BRANCH}) ==="
    CHANGED_FILES=$(git diff --name-only "origin/main..HEAD" 2>/dev/null || git diff --name-only "remotes/origin/main..HEAD" 2>/dev/null || echo "")
fi

echo "$CHANGED_FILES"

if [[ -z "$CHANGED_FILES" ]]; then
    echo ""
    echo "=== No changes detected ==="
    exit 0
fi

# --- Step 2: Determine affected agents ---
AFFECTED_AGENTS=""

CHANGED_SKILLS=$(echo "$CHANGED_FILES" | grep -oP 'incidara_agents/skills/\K[^/]+' | sort -u || true)
for skill in $CHANGED_SKILLS; do
    AGENTS=$(skill_to_agents "$skill")
    AFFECTED_AGENTS="${AFFECTED_AGENTS} ${AGENTS}"
done

CHANGED_MCPS=$(echo "$CHANGED_FILES" | grep -oP 'incidara_agents/mcp_servers/\K[^/]+' | sort -u || true)
for mcp in $CHANGED_MCPS; do
    AGENTS=$(mcp_to_agents "$mcp")
    AFFECTED_AGENTS="${AFFECTED_AGENTS} ${AGENTS}"
done

CHANGED_AGENTS_DIRECT=$(echo "$CHANGED_FILES" | grep -oP 'incidara_agents/agents/\K[^/]+' | sort -u || true)
AFFECTED_AGENTS="${AFFECTED_AGENTS} ${CHANGED_AGENTS_DIRECT}"

AFFECTED_AGENTS=$(echo "$AFFECTED_AGENTS" | tr ' ' '\n' | sort -u | grep -v '^$' || true)

# If claude-agent (base image) changed, all deployed agents need rebuild
BASE_CHANGED=""
for agent in $AFFECTED_AGENTS; do
    if [[ "$agent" == "claude-agent" ]]; then
        BASE_CHANGED="1"
        break
    fi
done

if [[ -n "$BASE_CHANGED" ]]; then
    echo ""
    echo "=== Base image (claude-agent) changed — all deployed agents need rebuild ==="
    AFFECTED_AGENTS="${AFFECTED_AGENTS} ${DEPLOYED_AGENTS}"
    AFFECTED_AGENTS=$(echo "$AFFECTED_AGENTS" | tr ' ' '\n' | sort -u | grep -v '^$' || true)
fi

# Filter to only agents in deploy scope (plus claude-agent for base rebuild)
SCOPED_AGENTS=""
for agent in $AFFECTED_AGENTS; do
    if [[ "$agent" == "claude-agent" ]] || echo " $DEPLOYED_AGENTS " | grep -q " $agent "; then
        SCOPED_AGENTS="${SCOPED_AGENTS} ${agent}"
    fi
done
AFFECTED_AGENTS=$(echo "$SCOPED_AGENTS" | tr ' ' '\n' | sort -u | grep -v '^$' || true)

if [[ -z "$AFFECTED_AGENTS" ]]; then
    echo ""
    echo "=== No agents affected by these changes ==="
    exit 0
fi

echo ""
echo "=== Affected agents ==="
echo "$AFFECTED_AGENTS"

# Auto-detect if --no-cache needed
if [[ -z "$NO_CACHE" ]]; then
    MCP_PY_CHANGED=$(echo "$CHANGED_FILES" | grep -c 'mcp_servers/.*\.py' || true)
    if [[ "$MCP_PY_CHANGED" -gt 0 ]]; then
        echo ""
        echo "=== MCP Python files changed — auto-enabling --no-cache ==="
        NO_CACHE="--no-cache"
    fi
fi

# --- Dry run ---
if [[ -n "$DRY_RUN" ]]; then
    echo ""
    echo "=== DRY RUN — would rebuild these agents ==="
    echo "$AFFECTED_AGENTS"
    echo "No-cache: ${NO_CACHE:-no}"
    exit 0
fi

# --- Step 3: Copy changed files to BUILD_ROOT ---
# Only copy files that actually changed — don't touch anything else.
# The build context may have external deps (ltp-platform) not in the git repo.

echo ""
echo "=== Copying changed files to ${BUILD_CONTEXT} ==="

CHANGED_COUNT=0
DELETED_COUNT=0
while IFS= read -r file; do
    SRC="${WORK_DIR}/repo/${file}"
    DST="${BUILD_CONTEXT}/${file}"

    if [[ -f "$SRC" ]]; then
        mkdir -p "$(dirname "$DST")"
        cp "$SRC" "$DST"
        CHANGED_COUNT=$((CHANGED_COUNT + 1))
    elif [[ -e "$DST" ]]; then
        # File was deleted in the branch — remove from build context too
        rm -f "$DST"
        DELETED_COUNT=$((DELETED_COUNT + 1))
    fi
done <<< "$CHANGED_FILES"

echo "  Copied ${CHANGED_COUNT} files, deleted ${DELETED_COUNT} files (vs main)"

# --- Step 4: Rebuild each affected agent ---
for agent in $AFFECTED_AGENTS; do
    AGENT_DIR="${AGENTS_DIR}/${agent}"
    PORT=$(get_agent_port "$agent")

    if [[ ! -d "$AGENT_DIR" ]]; then
        echo ""
        echo "⚠️  Agent directory not found: ${AGENT_DIR} — skipping"
        continue
    fi

    # claude-agent is the base image only — build it, no container to run
    if [[ "$agent" == "claude-agent" ]]; then
        echo ""
        echo "=== Rebuilding base image (claude-agent) ==="
        cd "${AGENT_DIR}"

        echo "  Building..."
        sudo make build 2>&1 | tail -5
        echo "  ✅ Base image built"
        continue
    fi

    echo ""
    echo "=== Rebuilding ${agent} (port ${PORT:-unknown}) ==="
    cd "${AGENT_DIR}"

    echo "  Stopping..."
    sudo make stop 2>/dev/null || true

    echo "  Building..."
    sudo make build 2>&1 | tail -5

    echo "  Starting..."
    sudo make run 2>&1 | tail -1

    if [[ -n "$PORT" ]]; then
        echo "  Waiting for health check (port ${PORT})..."
        sleep 5
        HEALTH=$(curl -s "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo '{"status":"error"}')
        STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "parse-error")

        if [[ "$STATUS" == "ok" ]]; then
            echo "  ✅ ${agent} healthy"
        else
            echo "  ❌ ${agent} NOT healthy (status=${STATUS})"
        fi
    else
        echo "  ⚠️  No port found for ${agent} — skipping health check"
    fi
done

echo ""
echo "=== Deploy complete ==="
echo "Agents rebuilt: ${AFFECTED_AGENTS}"
echo "No-cache: ${NO_CACHE:-no}"
