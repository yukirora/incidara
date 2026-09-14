#!/usr/bin/env bash
# Codeup MR merge CLI (pr-merge skill)
#
# Self-contained: this script does NOT source or shell out to any other
# skill's scripts.  The auth/SSH/API helpers are intentionally
# duplicated from pr-creation/pr-integration — the three code/repo
# skills must be independently deployable.
#
# Subcommands:
#   prepare <integration_branch> <target>       Fetch refs + read audit log; emit plan JSON:
#                                               {integrationBranch, target, contributingMrs:[...],
#                                                alreadyInTargetMrs:[...], skippedMrs:[...]}
#   create-integration-mr --source <b> --target <b> --title <t> --body-file <f>
#                                               POST .../changeRequests; echo {localId, detailUrl}
#   merge-mr --mr-id <id> [--method auto|no-ff|ff]
#                                               GET MR detail → validate → POST .../merge;
#                                               echo {localId, method, httpBody} on success
#   comment-mr --mr-id <id> --body <text>       POST a GLOBAL_COMMENT on an MR
#   close-mr --mr-id <id>                       POST .../close (idempotent)
#   delete-branch <branch>                      Scoped SSH `git push origin --delete <branch>`
#   strip-audit <integration_branch>            Push a fast-forward commit to <integration_branch>
#                                               that deletes integration/merge_log_<ts>.jsonl from
#                                               its tip (so the audit log doesn't propagate to
#                                               target via the integration MR). Idempotent.
#   resolve-repo [remote]                       Internal: remote URL → numeric repo id (cached)
#
# Credential discovery (env-first, prod-friendly):
#   1. Env var
#   2. $CODEUP_SECRETS_DIR/<name>   (default /run/secrets/codeup/)
#   3. ~/.codeup_tokens/<name>      (workstation convenience)
#   4. _die loudly.  Never falls back to ~/.ssh/* or git config --global.
#
# Required for API calls : CODEUP_TOKEN, CODEUP_ORG_ID
# Required for git push  : SSH key + known_hosts (via env/secret/dotfile)

set -euo pipefail

# ─────────────────────── constants ────────────────────────────────

CODEUP_ENDPOINT="${CODEUP_ENDPOINT:-https://openapi-rdc.aliyuncs.com}"
CODEUP_HOST="${CODEUP_HOST:-github.com/example}"
CODEUP_TOKENS_DIR="${HOME}/.codeup_tokens"
CODEUP_SECRETS_DIR="${CODEUP_SECRETS_DIR:-/run/secrets/codeup}"

# ─────────────────────── utilities ────────────────────────────────

_die() { echo "ERROR: $*" >&2; exit 1; }
_log() { echo "[pr-merge] $*" >&2; }

_read_secret() {
    local env_name="$1" file_name="$2"
    if [[ -n "${!env_name:-}" ]]; then echo "${!env_name}"; return; fi
    [[ -f "$CODEUP_SECRETS_DIR/$file_name" ]] && { cat "$CODEUP_SECRETS_DIR/$file_name"; return; }
    [[ -f "$CODEUP_TOKENS_DIR/$file_name"  ]] && { cat "$CODEUP_TOKENS_DIR/$file_name";  return; }
    echo ""
}

_get_token()  { _read_secret CODEUP_TOKEN  token;  }
_get_org_id() { _read_secret CODEUP_ORG_ID org_id; }

_get_ssh_key_path() {
    if [[ -n "${CODEUP_SSH_KEY_PATH:-}" && -f "$CODEUP_SSH_KEY_PATH" ]]; then
        echo "$CODEUP_SSH_KEY_PATH"; return
    fi
    if [[ -n "${CODEUP_SSH_KEY_CONTENT:-}" ]]; then
        local tmp; tmp="$(mktemp)"
        printf '%s\n' "$CODEUP_SSH_KEY_CONTENT" > "$tmp"
        chmod 600 "$tmp"
        echo "$tmp"; return
    fi
    [[ -f "$CODEUP_SECRETS_DIR/ssh_key" ]] && { echo "$CODEUP_SECRETS_DIR/ssh_key"; return; }
    [[ -f "$CODEUP_TOKENS_DIR/ssh_key"  ]] && { echo "$CODEUP_TOKENS_DIR/ssh_key";  return; }
    echo ""
}

_get_known_hosts_path() {
    [[ -f "$CODEUP_SECRETS_DIR/known_hosts" ]] && { echo "$CODEUP_SECRETS_DIR/known_hosts"; return; }
    [[ -f "$CODEUP_TOKENS_DIR/known_hosts"  ]] && { echo "$CODEUP_TOKENS_DIR/known_hosts";  return; }
    echo ""
}

_ssh_command() {
    local key kh
    key="$(_get_ssh_key_path)"
    kh="$(_get_known_hosts_path)"
    [[ -n "$key" ]] || _die "no SSH key found (checked CODEUP_SSH_KEY_PATH / CODEUP_SSH_KEY_CONTENT / $CODEUP_SECRETS_DIR/ssh_key / $CODEUP_TOKENS_DIR/ssh_key)"
    [[ -n "$kh"  ]] || _die "no known_hosts file found"
    echo "ssh -i $key -o IdentitiesOnly=yes -o UserKnownHostsFile=$kh -o StrictHostKeyChecking=yes -o LogLevel=ERROR"
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

# Derive the per-run audit log path from an integration branch name.
# Convention matches pr-integration: `integration/<target>/<ts>` →
# `integration/merge_log_<ts>.jsonl`.
_audit_log_path_for() {
    local branch="$1"
    local ts="${branch##*/}"
    [[ -n "$ts" ]] || _die "cannot derive audit-log path from branch '$branch'"
    echo "integration/merge_log_${ts}.jsonl"
}

_mkdir_tokens() {
    mkdir -p "$CODEUP_TOKENS_DIR"
    chmod 700 "$CODEUP_TOKENS_DIR"
}

_api_base() {
    local org; org="$(_get_org_id)"
    [[ -n "$org" ]] || _die "CODEUP_ORG_ID not set and no saved org_id"
    echo "$CODEUP_ENDPOINT/oapi/v1/codeup/organizations/$org"
}

_api_get() {
    local path="$1"
    local pat; pat="$(_get_token)"
    [[ -n "$pat" ]] || _die "CODEUP_TOKEN not set"
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

_api_post_json() {
    local path="$1" payload="$2"
    local pat; pat="$(_get_token)"
    [[ -n "$pat" ]] || _die "CODEUP_TOKEN not set"
    local resp http_code body
    resp=$(curl -sS -w '\n%{http_code}' \
        -X POST \
        -H "x-yunxiao-token: $pat" \
        -H 'Content-Type: application/json' -H 'Accept: application/json' \
        --data "$payload" \
        "$(_api_base)$path") || _die "curl failed"
    http_code="${resp##*$'\n'}"; body="${resp%$'\n'*}"
    if [[ "$http_code" != 2* ]]; then
        echo "$body" >&2
        _die "POST $path returned HTTP $http_code"
    fi
    # Codeup sometimes returns HTTP 200 but with business-level error wrapped.
    # Callers that care should inspect the echoed body.
    echo "$body"
}

# ─────────────────────── internal ─────────────────────────────────

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
    [[ -n "$id" ]] || { echo "$body" >&2; _die "repo $path not found"; }

    _mkdir_tokens
    printf '%s' "$id" > "$cache_file"
    chmod 600 "$cache_file"
    echo "$id"
}

_scoped_git() {
    local ssh_cmd; ssh_cmd="$(_ssh_command)"
    GIT_SSH_COMMAND="$ssh_cmd" git "$@"
}

# ─────────────────────── subcommands ──────────────────────────────

cmd_prepare() {
    local integration_branch="${1:-}" target="${2:-}"
    [[ -n "$integration_branch" ]] || _die "usage: prepare <integration_branch> <target>"
    [[ -n "$target" ]]              || _die "usage: prepare <integration_branch> <target>"

    _log "fetching origin/$integration_branch and origin/$target ..."
    _scoped_git fetch origin --prune 2>&1 | tail -5 >&2

    git rev-parse --verify --quiet "refs/remotes/origin/$integration_branch" >/dev/null \
        || _die "integration branch '$integration_branch' not on origin"
    git rev-parse --verify --quiet "refs/remotes/origin/$target" >/dev/null \
        || _die "target branch '$target' not on origin"

    # Read this run's audit log from the integration branch's tree.
    # Path convention: `integration/merge_log_<ts>.jsonl` where <ts>
    # is the integration branch's timestamp suffix.
    local audit_file; audit_file=$(_audit_log_path_for "$integration_branch")
    local audit
    audit=$(git show "origin/${integration_branch}:${audit_file}" 2>/dev/null) \
        || _die "integration branch has no $audit_file — was it built by pr-integration?"

    # Classify entries.
    echo "$audit" | jq -n --arg branch "$integration_branch" --arg target "$target" --arg path "$audit_file" --rawfile a /dev/stdin '
        ($a | split("\n") | map(select(length>0) | fromjson)) as $entries |
        {
            integrationBranch: $branch,
            target: $target,
            auditPath: $path,
            contributingMrs: ($entries | map(select(.status == "clean" or .status == "auto_resolved"))
                                     | map({localId: .source.localId, sourceBranch: .source.branch, status: .status})),
            alreadyInTargetMrs: ($entries | map(select(.status == "already_in_target"))
                                         | map({localId: .source.localId, sourceBranch: .source.branch})),
            skippedMrs: ($entries | map(select(.status == "skipped"))
                                | map({localId: .source.localId, sourceBranch: .source.branch, reason: .reason}))
        }
    ' <<<"$audit"
}

cmd_create_integration_mr() {
    local source_branch="" target_branch="" title="" body_file=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --source)    source_branch="$2"; shift 2 ;;
            --target)    target_branch="$2"; shift 2 ;;
            --title)     title="$2";         shift 2 ;;
            --body-file) body_file="$2";     shift 2 ;;
            *) _die "unknown flag: $1" ;;
        esac
    done
    [[ -n "$source_branch" ]] || _die "--source <branch> required"
    [[ -n "$target_branch" ]] || _die "--target <branch> required"
    [[ -n "$title" ]]         || _die "--title <text> required"
    [[ -n "$body_file" && -f "$body_file" ]] || _die "--body-file <path> required"

    local repo_id; repo_id=$(cmd_resolve_repo origin)
    local body_text; body_text=$(cat "$body_file")
    local payload
    payload=$(jq -n \
        --arg title  "$title" \
        --arg desc   "$body_text" \
        --arg src    "$source_branch" \
        --arg tgt    "$target_branch" \
        --argjson rid "$repo_id" \
        '{
            title:           $title,
            description:     $desc,
            sourceBranch:    $src,
            targetBranch:    $tgt,
            sourceProjectId: $rid,
            targetProjectId: $rid
        }')

    _log "POST /repositories/$repo_id/changeRequests   $source_branch → $target_branch"
    local resp; resp=$(_api_post_json "/repositories/$repo_id/changeRequests" "$payload")
    local local_id detail_url
    local_id=$(echo  "$resp" | jq -r '.localId   // empty')
    detail_url=$(echo "$resp" | jq -r '.detailUrl // empty')
    [[ -n "$local_id" && -n "$detail_url" ]] \
        || { echo "$resp" >&2; _die "create-integration-mr: missing localId/detailUrl in response"; }

    jq -n --argjson id "$local_id" --arg url "$detail_url" '{localId:$id, detailUrl:$url}'
}

cmd_merge_mr() {
    local mr_id="" method="auto"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mr-id)  mr_id="$2";  shift 2 ;;
            --method) method="$2"; shift 2 ;;
            *) _die "unknown flag: $1" ;;
        esac
    done
    [[ -n "$mr_id" ]] || _die "--mr-id <id> required"
    case "$method" in auto|no-ff|ff) ;; *) _die "--method must be auto|no-ff|ff" ;; esac

    local repo_id; repo_id=$(cmd_resolve_repo origin)

    # GET MR detail: state check, conflict check, auto method resolution.
    local detail; detail=$(_api_get "/repositories/$repo_id/changeRequests/$mr_id")
    local state has_conflict support_ff req_pass conflict_check
    # NOTE: Codeup's single-MR detail endpoint returns the state in
    # `.status`, while the list endpoint returns it in `.state`.  Read
    # both (status takes precedence) to be robust across future API
    # changes.
    state=$(echo          "$detail" | jq -r '.status        // .state // empty')
    has_conflict=$(echo   "$detail" | jq -r '.hasConflict   // false')
    conflict_check=$(echo "$detail" | jq -r '.conflictCheckStatus // empty')
    support_ff=$(echo     "$detail" | jq -r '.supportMergeFastForwardOnly // false')
    req_pass=$(echo       "$detail" | jq -r '.allRequirementsPass // true')

    [[ "$state" == "TO_BE_MERGED" || "$state" == "UNDER_REVIEW" ]] \
        || _die "MR #$mr_id state='$state' is not mergeable"
    [[ "$has_conflict" != "true" ]] \
        || _die "MR #$mr_id has unresolved conflicts against target"
    [[ "$req_pass" == "true" ]] \
        || _die "MR #$mr_id has allRequirementsPass=false — blocked by branch protections"

    # Resolve method.
    #
    # `supportMergeFastForwardOnly: true` does NOT mean "FF is the
    # right choice here" — in practice Codeup rejects FF merges on
    # branches that contain merge commits (integration branches
    # always do, since pr-integration uses `git merge --no-ff`).
    # Default `auto` therefore picks `no-fast-forward` unconditionally.
    # Callers who know their branch is a pure ancestor can pass
    # `--method ff` explicitly.
    local merge_type
    case "$method" in
        no-ff) merge_type="no-fast-forward" ;;
        ff)    merge_type="fast-forward" ;;
        auto)  merge_type="no-fast-forward" ;;
    esac

    _log "merging MR #$mr_id   method=$merge_type   (state=$state conflictCheck=$conflict_check)"
    local payload; payload=$(jq -n --arg mt "$merge_type" '{mergeType:$mt}')
    local resp; resp=$(_api_post_json "/repositories/$repo_id/changeRequests/$mr_id/merge" "$payload")

    jq -n --argjson id "$mr_id" --arg method "$merge_type" --argjson resp "$resp" \
        '{localId:$id, method:$method, response:$resp}'
}

cmd_comment_mr() {
    local mr_id="" body_text=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mr-id) mr_id="$2";     shift 2 ;;
            --body)  body_text="$2"; shift 2 ;;
            *) _die "unknown flag: $1" ;;
        esac
    done
    [[ -n "$mr_id"     ]] || _die "--mr-id <id> required"
    [[ -n "$body_text" ]] || _die "--body <text> required"

    local repo_id; repo_id=$(cmd_resolve_repo origin)
    local payload; payload=$(jq -n --arg body "$body_text" \
        '{content:$body, commentType:"GLOBAL_COMMENT", resolved:false}')
    local resp; resp=$(_api_post_json "/repositories/$repo_id/changeRequests/$mr_id/comments" "$payload")
    echo "$resp" | jq '{commentBizId: .comment_biz_id, content: .content}'
}

cmd_close_mr() {
    local mr_id=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mr-id) mr_id="$2"; shift 2 ;;
            *) _die "unknown flag: $1" ;;
        esac
    done
    [[ -n "$mr_id" ]] || _die "--mr-id <id> required"
    local repo_id; repo_id=$(cmd_resolve_repo origin)

    # Idempotent: if the MR is already in a terminal state (MERGED or
    # CLOSED), treat the close as a no-op.  This is routine when
    # pr-merge runs after the integration merge — Codeup auto-sets
    # contributing MRs to MERGED as soon as their source commits are
    # reachable from target, so by the time we try to close them
    # they're already terminal and the POST /close returns 400.
    local detail; detail=$(_api_get "/repositories/$repo_id/changeRequests/$mr_id") || true
    local cur_state
    cur_state=$(echo "$detail" | jq -r '.status // .state // empty')
    if [[ "$cur_state" == "MERGED" || "$cur_state" == "CLOSED" ]]; then
        _log "MR #$mr_id already in terminal state '$cur_state' — skipping close (no-op)"
        jq -n --arg s "$cur_state" '{result:true, noop:true, state:$s}'
        return 0
    fi

    _api_post_json "/repositories/$repo_id/changeRequests/$mr_id/close" '{}'
}

cmd_delete_branch() {
    local branch="${1:-}"
    [[ -n "$branch" ]] || _die "usage: delete-branch <branch>"
    _log "deleting origin/$branch"
    _scoped_git push origin --delete "$branch"
}

# Strip the per-run audit log (`integration/merge_log_<ts>.jsonl`) from the
# tip of an integration branch by pushing a fast-forward commit that
# removes it. Run this AFTER `prepare` (which has captured the audit
# data into the caller's PLAN variable) and BEFORE
# `create-integration-mr`, so the integration MR's tree no longer
# contains the audit log — and target therefore does not inherit it
# when the integration MR merges.
#
# Idempotent: if the audit log is already absent from the integration
# tip (e.g. retry after a partial run), prints status:already_absent
# and returns 0 without touching origin.
#
# Uses a temporary `git worktree` so the caller's HEAD / index are
# untouched. Push is fast-forward only (no `--force`); if the
# integration tip has moved since fetch, the push fails and `set -e`
# aborts.
cmd_strip_audit() {
    local integration_branch="${1:-}"
    [[ -n "$integration_branch" ]] || _die "usage: strip-audit <integration_branch>"

    local audit_file; audit_file=$(_audit_log_path_for "$integration_branch")

    _log "fetching origin/$integration_branch ..."
    _scoped_git fetch origin --prune 2>&1 | tail -5 >&2
    git rev-parse --verify --quiet "refs/remotes/origin/$integration_branch" >/dev/null \
        || _die "integration branch '$integration_branch' not on origin"

    if ! git cat-file -e "origin/${integration_branch}:${audit_file}" 2>/dev/null; then
        _log "audit log $audit_file already absent on origin/$integration_branch — strip is a no-op"
        jq -n --arg b "$integration_branch" --arg p "$audit_file" \
            '{status:"already_absent",branch:$b,auditPath:$p}'
        return 0
    fi

    local tmp_wt; tmp_wt=$(mktemp -d -t pr-merge-strip-XXXXXX)
    rmdir "$tmp_wt"   # `git worktree add` requires the path NOT to exist

    _log "creating temp worktree at $tmp_wt (detached @ origin/$integration_branch)"
    git worktree add --detach "$tmp_wt" "origin/${integration_branch}" >&2

    # shellcheck disable=SC2064 — capture $tmp_wt at trap-set time, not fire time.
    trap "git worktree remove --force '$tmp_wt' 2>/dev/null || rm -rf '$tmp_wt'" EXIT

    rm -f "$tmp_wt/$audit_file"
    rmdir "$tmp_wt/$(dirname "$audit_file")" 2>/dev/null || true

    git -C "$tmp_wt" add -A

    local result_json
    if git -C "$tmp_wt" diff --cached --quiet; then
        # Reachable only if the worktree's tree already matched origin's
        # (audit log existed in origin tip's tree per the cat-file check
        # above, but rm produced no diff). Defensive — treat as no-op.
        _log "no staged changes after rm — treating as already-stripped"
        result_json=$(jq -n --arg b "$integration_branch" --arg p "$audit_file" \
            '{status:"already_absent",branch:$b,auditPath:$p}')
    else
        local author_name="${CODEUP_GIT_AUTHOR_NAME:-pr-merge}"
        local author_email="${CODEUP_GIT_AUTHOR_EMAIL:-pr-merge@finalsystems.local}"

        GIT_AUTHOR_NAME="$author_name"   GIT_AUTHOR_EMAIL="$author_email" \
        GIT_COMMITTER_NAME="$author_name" GIT_COMMITTER_EMAIL="$author_email" \
            git -C "$tmp_wt" commit -m "pr-merge: strip integration audit log before merge to target"

        local new_sha; new_sha=$(git -C "$tmp_wt" rev-parse HEAD)
        _log "pushing $new_sha → origin/$integration_branch (fast-forward)"
        _scoped_git push origin "$new_sha:refs/heads/$integration_branch"

        result_json=$(jq -n --arg b "$integration_branch" --arg sha "$new_sha" --arg p "$audit_file" \
            '{status:"stripped",branch:$b,strippedCommit:$sha,auditPath:$p}')
    fi

    git worktree remove --force "$tmp_wt" 2>/dev/null || rm -rf "$tmp_wt"
    trap - EXIT

    echo "$result_json"
}

# ─────────────────────── dispatch ─────────────────────────────────

usage() {
    sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//; /^set /d'
}

main() {
    local cmd="${1:-}"
    [[ $# -ge 1 ]] && shift || true
    case "$cmd" in
        prepare)               cmd_prepare               "$@" ;;
        create-integration-mr) cmd_create_integration_mr "$@" ;;
        merge-mr)              cmd_merge_mr              "$@" ;;
        comment-mr)            cmd_comment_mr            "$@" ;;
        close-mr)              cmd_close_mr              "$@" ;;
        delete-branch)         cmd_delete_branch         "$@" ;;
        strip-audit)           cmd_strip_audit           "$@" ;;
        resolve-repo)          cmd_resolve_repo          "$@" ;;
        ""|-h|--help)          usage ;;
        *) _die "unknown subcommand: $cmd (run '$0 --help')" ;;
    esac
}

main "$@"
