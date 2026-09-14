#!/usr/bin/env bash
# Codeup MR query CLI (pr-list skill)
#
# Query merge request state on Codeup. Self-contained auth.
#
# Subcommands:
#   list-mrs [--source <branch>] [--state <opened|merged|closed>]
#                                              List MRs for current repo, optional filters
#   get-mr <mr-id>                             Get full detail for a single MR
#
# Credential discovery (env-first, prod-friendly):
#   1. Env var
#   2. $CODEUP_SECRETS_DIR/<name>   (default /run/secrets/codeup/)
#   3. ~/.codeup_tokens/<name>      (workstation convenience)
#   4. _die loudly.
#
# Required: CODEUP_TOKEN, CODEUP_ORG_ID

set -euo pipefail

# ─────────────────────── constants ────────────────────────────────

CODEUP_ENDPOINT="${CODEUP_ENDPOINT:-https://openapi-rdc.aliyuncs.com}"
CODEUP_TOKENS_DIR="${HOME}/.codeup_tokens"
CODEUP_SECRETS_DIR="${CODEUP_SECRETS_DIR:-/run/secrets/codeup}"

# ─────────────────────── utilities ────────────────────────────────

_die() { echo "ERROR: $*" >&2; exit 1; }
_log() { echo "[pr-list] $*" >&2; }

_read_secret() {
    local env_name="$1" file_name="$2"
    if [[ -n "${!env_name:-}" ]]; then echo "${!env_name}"; return; fi
    [[ -f "$CODEUP_SECRETS_DIR/$file_name" ]] && { cat "$CODEUP_SECRETS_DIR/$file_name"; return; }
    [[ -f "$CODEUP_TOKENS_DIR/$file_name"  ]] && { cat "$CODEUP_TOKENS_DIR/$file_name";  return; }
    echo ""
}

_get_token()  { _read_secret CODEUP_TOKEN  token;  }
_get_org_id() { _read_secret CODEUP_ORG_ID org_id; }

_mkdir_tokens() {
    mkdir -p "$CODEUP_TOKENS_DIR"
    chmod 700 "$CODEUP_TOKENS_DIR"
}

_urlenc() {
    python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

_parse_remote_path() {
    local url="$1" path=""
    if   [[ "$url" =~ ^git@[^:]+:(.+)$       ]]; then path="${BASH_REMATCH[1]}"
    elif [[ "$url" =~ ^ssh://git@[^/]+/(.+)$ ]]; then path="${BASH_REMATCH[1]}"
    elif [[ "$url" =~ ^https?://[^/]+/(.+)$  ]]; then path="${BASH_REMATCH[1]}"
    else _die "unrecognized remote URL: $url"
    fi
    path="${path%.git}"
    echo "$path"
}

_slug_path() { echo "${1//\//_}"; }

_api_base() {
    local org; org="$(_get_org_id)"
    [[ -n "$org" ]] || _die "CODEUP_ORG_ID not set and no saved org_id"
    echo "$CODEUP_ENDPOINT/oapi/v1/codeup/organizations/$org"
}

_api_get() {
    local path="$1"
    local pat; pat="$(_get_token)"
    [[ -n "$pat" ]] || _die "CODEUP_TOKEN not set and no saved token"
    local resp http_code body
    resp=$(curl -sS -w '\n%{http_code}' \
        -H "x-yunxiao-token: $pat" -H 'Accept: application/json' \
        "$(_api_base)$path") || _die "curl failed"
    http_code="${resp##*$'\n'}"; body="${resp%$'\n'*}"
    if [[ "$http_code" != 2* ]]; then
        echo "$body" >&2
        _die "GET $path returned HTTP $http_code"
    fi
    echo "$body"
}

# ─────────────────────── resolve repo id ──────────────────────────

cmd_resolve_repo() {
    local remote="${1:-origin}"
    local url; url=$(git remote get-url "$remote") \
        || _die "git remote '$remote' not found in $(pwd)"
    local path; path=$(_parse_remote_path "$url")
    local slug; slug=$(_slug_path "$path")

    local cache_file="$CODEUP_TOKENS_DIR/${slug}_repo_id"
    if [[ -f "$cache_file" ]]; then
        cat "$cache_file"
        return
    fi

    local repo_name="${path##*/}"
    _log "resolving $path via /repositories?search=$repo_name ..."
    local body; body=$(_api_get "/repositories?search=$(_urlenc "$repo_name")")

    local id
    id=$(echo "$body" | jq -r --arg p "$path" '
        map(select((.pathWithNamespace // .nameWithNamespace // "")
                   | ascii_downcase
                   | gsub(" "; "") == ($p | ascii_downcase)))
        | first
        | .id // empty
    ')
    [[ -n "$id" ]] || { echo "$body" >&2; _die "repo $path not found in /repositories?search=$repo_name"; }

    _mkdir_tokens
    printf '%s' "$id" > "$cache_file"
    chmod 600 "$cache_file"
    echo "$id"
}

# ─────────────────────── subcommands ──────────────────────────────

cmd_list_mrs() {
    local source_branch="" state=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --source) source_branch="$2"; shift 2 ;;
            --state)  state="$2";         shift 2 ;;
            *) _die "unknown flag: $1" ;;
        esac
    done

    local repo_id; repo_id=$(cmd_resolve_repo origin)

    # Org-level list endpoint (repo-level /repositories/$id/changeRequests returns 404).
    # Params: page (1-based), perPage (max 100), projectIds, state (opened/merged/closed), search.
    # No sourceBranch filter on server — filtered client-side.
    # Ref: alibabacloud-devops-mcp-server operations/codeup/changeRequests.ts
    local all_mrs="[]"
    local page=1
    while true; do
        local path="/changeRequests?projectIds=$repo_id&page=$page&perPage=100"
        if [[ -n "$state" ]]; then
            path="$path&state=$state"
        fi
        local body; body=$(_api_get "$path")
        local batch; batch=$(echo "$body" | jq '.')

        if [[ "$(echo "$batch" | jq 'length')" -eq 0 ]]; then
            break
        fi

        all_mrs=$(echo "$all_mrs" "$batch" | jq -s 'add')
        page=$((page + 1))

        if [[ "$(echo "$batch" | jq 'length')" -lt 100 ]]; then
            break
        fi
    done

    # Client-side filtering by sourceBranch
    local filter_expr=".[]"
    if [[ -n "$source_branch" ]]; then
        filter_expr="$filter_expr | select(.sourceBranch == \"$source_branch\")"
    fi

    echo "$all_mrs" | jq -c "$filter_expr | {localId, state: (.state // .status), sourceBranch, targetBranch, hasConflict, detailUrl, title: (.title[:80])}"
}

cmd_get_mr() {
    local mr_id="${1:-}"
    [[ -n "$mr_id" ]] || _die "usage: get-mr <mr-id>"

    local repo_id; repo_id=$(cmd_resolve_repo origin)
    local body; body=$(_api_get "/repositories/$repo_id/changeRequests/$mr_id")

    # Detail endpoint uses "status", list uses "state". Output both.
    echo "$body" | jq '{
        localId,
        state:     (.status // .state),
        sourceBranch,
        targetBranch,
        hasConflict,
        conflictCheckStatus,
        allRequirementsPass,
        supportMergeFastForwardOnly,
        mergedRevision,
        mergedAt,
        detailUrl,
        title:     (.title[:120])
    }'
}

# ─────────────────────── dispatch ─────────────────────────────────

usage() {
    sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//; /^set /d'
}

main() {
    local cmd="${1:-}"
    [[ $# -ge 1 ]] && shift || true
    case "$cmd" in
        list-mrs)     cmd_list_mrs      "$@" ;;
        get-mr)       cmd_get_mr        "$@" ;;
        resolve-repo) cmd_resolve_repo  "$@" ;;
        ""|-h|--help) usage ;;
        *) _die "unknown subcommand: $cmd (run '$0 --help')" ;;
    esac
}

main "$@"
