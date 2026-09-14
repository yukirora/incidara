#!/usr/bin/env bash
# Codeup PR (merge request) creation CLI
#
# Two independent auth layers:
#   - Codeup MR API over HTTPS  →  PAT (Personal Access Token, `pt-...`)
#                                   via `x-yunxiao-token` header to
#                                   openapi-rdc.aliyuncs.com.
#   - git push                  →  SSH key  (HTTPS push is blocked by policy)
#
# Credential discovery (env-first, prod-friendly):
#   1. Env var
#   2. $CODEUP_SECRETS_DIR/<name>   (default /run/secrets/codeup/)
#   3. ~/.codeup_tokens/<name>      (workstation convenience)
#   4. _die loudly.  Never falls back to ~/.ssh/* or git config --global.
#
# Subcommands:
#   save-token <pat>                           Save PAT to ~/.codeup_tokens/token (600)
#   save-org   <org_id>                        Save org ID to ~/.codeup_tokens/org_id
#   save-ssh-key <path>                        Copy private key + populate known_hosts
#   save-identity <name> <email>               Manually save commit-author identity
#   whoami                                     Probe SSH banner → user + synthesized email
#   test-ssh                                   ssh -T git@github.com/example with scoped config
#   resolve-repo [remote]                      git remote URL → numeric Codeup repo id (cached)
#   git-push <branch>                          Push current HEAD via scoped GIT_SSH_COMMAND
#   create-mr --source <b> --target <b> --title <t> --body-file <f>
#                                              POST .../changeRequests, print detailUrl + localId
#
# Required for API calls : CODEUP_TOKEN, CODEUP_ORG_ID
# Required for git push  : SSH key (via env/secret/dotfile), known_hosts
# Required for commits   : CODEUP_USER, CODEUP_EMAIL (from whoami / save-identity / env)

set -euo pipefail

# ─────────────────────── constants ────────────────────────────────

# Yunxiao OpenAPI base for Codeup — uses `x-yunxiao-token` header auth.
# (The older devops.cn-hangzhou.aliyuncs.com endpoint requires AK+SK
# signing, so PATs don't work there.)
CODEUP_ENDPOINT="${CODEUP_ENDPOINT:-https://openapi-rdc.aliyuncs.com}"
CODEUP_HOST="${CODEUP_HOST:-github.com/example}"
CODEUP_TOKENS_DIR="${HOME}/.codeup_tokens"
CODEUP_SECRETS_DIR="${CODEUP_SECRETS_DIR:-/run/secrets/codeup}"

# ─────────────────────── utilities ────────────────────────────────

_die()     { echo "ERROR: $*" >&2; exit 1; }
_require() { [[ -n "${!1:-}" ]] || _die "env var $1 is not set"; }
_log()     { echo "[codeup] $*" >&2; }

_mkdir_tokens() {
    mkdir -p "$CODEUP_TOKENS_DIR"
    chmod 700 "$CODEUP_TOKENS_DIR"
}

# Read a secret from: env > $CODEUP_SECRETS_DIR/<name> > ~/.codeup_tokens/<name>
_read_secret() {
    local env_name="$1" file_name="$2"
    if [[ -n "${!env_name:-}" ]]; then
        echo "${!env_name}"
        return
    fi
    if [[ -f "$CODEUP_SECRETS_DIR/$file_name" ]]; then
        cat "$CODEUP_SECRETS_DIR/$file_name"
        return
    fi
    if [[ -f "$CODEUP_TOKENS_DIR/$file_name" ]]; then
        cat "$CODEUP_TOKENS_DIR/$file_name"
        return
    fi
    echo ""
}

_get_token()  { _read_secret CODEUP_TOKEN  token;  }
_get_org_id() { _read_secret CODEUP_ORG_ID org_id; }
_get_user()   { _read_secret CODEUP_USER   user;   }
_get_email()  { _read_secret CODEUP_EMAIL  email;  }

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
    if [[ -f "$CODEUP_SECRETS_DIR/ssh_key" ]]; then
        echo "$CODEUP_SECRETS_DIR/ssh_key"; return
    fi
    if [[ -f "$CODEUP_TOKENS_DIR/ssh_key" ]]; then
        echo "$CODEUP_TOKENS_DIR/ssh_key"; return
    fi
    echo ""
}

_get_known_hosts_path() {
    if [[ -f "$CODEUP_SECRETS_DIR/known_hosts" ]]; then
        echo "$CODEUP_SECRETS_DIR/known_hosts"; return
    fi
    if [[ -f "$CODEUP_TOKENS_DIR/known_hosts" ]]; then
        echo "$CODEUP_TOKENS_DIR/known_hosts"; return
    fi
    echo ""
}

_ssh_command() {
    local key; key="$(_get_ssh_key_path)"
    [[ -n "$key" ]] || _die "no SSH key found (checked CODEUP_SSH_KEY_PATH / CODEUP_SSH_KEY_CONTENT / $CODEUP_SECRETS_DIR/ssh_key / $CODEUP_TOKENS_DIR/ssh_key)"
    local kh; kh="$(_get_known_hosts_path)"
    [[ -n "$kh" ]] || _die "no known_hosts file found (run 'pr-creation.sh save-ssh-key <path>' to populate ~/.codeup_tokens/known_hosts)"
    echo "ssh -i $key -o IdentitiesOnly=yes -o UserKnownHostsFile=$kh -o StrictHostKeyChecking=yes -o LogLevel=ERROR"
}

_urlenc() {
    python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

_mask() {
    local s="${1:-}"
    if [[ ${#s} -le 8 ]]; then echo "***"; else echo "${s:0:6}***${s: -4}"; fi
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

# Org-level API base: every Yunxiao Codeup endpoint we use is under this.
_api_base() {
    local org; org="$(_get_org_id)"
    [[ -n "$org" ]] || _die "CODEUP_ORG_ID not set and no saved org_id (run 'pr-creation.sh save-org <id>')"
    echo "$CODEUP_ENDPOINT/oapi/v1/codeup/organizations/$org"
}

# GET a Yunxiao Codeup API path with PAT header. Echoes body; dies on non-2xx.
_api_get() {
    local path="$1"
    local pat; pat="$(_get_token)"
    [[ -n "$pat" ]] || _die "CODEUP_TOKEN not set and no saved token (run 'pr-creation.sh save-token <pat>')"
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

# POST JSON with PAT header. Echoes body; dies on non-2xx.
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
    echo "$body"
}

# ─────────────────────── subcommands ──────────────────────────────

cmd_save_token() {
    local pat="${1:-}"
    [[ -n "$pat" ]] || _die "usage: save-token <pat>"
    _mkdir_tokens
    printf '%s' "$pat" > "$CODEUP_TOKENS_DIR/token"
    chmod 600 "$CODEUP_TOKENS_DIR/token"
    _log "saved PAT ($(_mask "$pat")) to $CODEUP_TOKENS_DIR/token"
}

cmd_save_org() {
    local org="${1:-}"
    [[ -n "$org" ]] || _die "usage: save-org <org_id>"
    _mkdir_tokens
    printf '%s' "$org" > "$CODEUP_TOKENS_DIR/org_id"
    chmod 600 "$CODEUP_TOKENS_DIR/org_id"
    _log "saved org_id=$org to $CODEUP_TOKENS_DIR/org_id"
}

cmd_save_identity() {
    local name="${1:-}" email="${2:-}"
    [[ -n "$name" && -n "$email" ]] || _die "usage: save-identity <name> <email>"
    _mkdir_tokens
    printf '%s' "$name"  > "$CODEUP_TOKENS_DIR/user"
    printf '%s' "$email" > "$CODEUP_TOKENS_DIR/email"
    chmod 600 "$CODEUP_TOKENS_DIR/user" "$CODEUP_TOKENS_DIR/email"
    _log "saved identity $name <$email>"
}

cmd_save_ssh_key() {
    local src="${1:-}"
    [[ -n "$src" ]] || _die "usage: save-ssh-key <path-to-private-key>"
    [[ -f "$src" ]] || _die "SSH key not found at $src"
    _mkdir_tokens
    install -m 600 "$src" "$CODEUP_TOKENS_DIR/ssh_key"
    _log "copied $src → $CODEUP_TOKENS_DIR/ssh_key (mode 600)"

    _log "populating $CODEUP_TOKENS_DIR/known_hosts via ssh-keyscan $CODEUP_HOST ..."
    if ! ssh-keyscan -T 10 -H "$CODEUP_HOST" > "$CODEUP_TOKENS_DIR/known_hosts" 2>/dev/null; then
        rm -f "$CODEUP_TOKENS_DIR/known_hosts"
        _die "ssh-keyscan $CODEUP_HOST failed — check network / DNS"
    fi
    chmod 600 "$CODEUP_TOKENS_DIR/known_hosts"
    _log "wrote $(wc -l < "$CODEUP_TOKENS_DIR/known_hosts") host key line(s)"
}

cmd_test_ssh() {
    local ssh_cmd; ssh_cmd="$(_ssh_command)"
    _log "running: $ssh_cmd -T git@$CODEUP_HOST"
    set +e
    local out rc
    out=$($ssh_cmd -T "git@$CODEUP_HOST" 2>&1); rc=$?
    set -e
    echo "$out"
    case $rc in
        0|1) _log "SSH reachable (exit $rc)"; return 0 ;;
        *)   _die "SSH failed (exit $rc): $out" ;;
    esac
}

# Resolve the authenticated user via the SSH banner — the one identity
# layer that is 100% determined on the server and doesn't depend on
# Codeup's OpenAPI being enabled in the current enterprise.  Codeup
# answers `ssh -T git@github.com/example` with the line
#   "Welcome to Codeup, <username>!"
# which is our authoritative source for the username.  Email is not
# exposed via SSH, so we synthesize a noreply form (overridable via
# CODEUP_EMAIL env or `save-identity`).
cmd_whoami() {
    local ssh_cmd; ssh_cmd="$(_ssh_command)"
    set +e
    local out rc
    out=$($ssh_cmd -T "git@$CODEUP_HOST" 2>&1); rc=$?
    set -e
    case $rc in 0|1) ;; *) _die "ssh probe failed (exit $rc): $out" ;; esac

    local username
    username=$(echo "$out" | grep -oE 'Welcome to Codeup, [^!]+' | head -1 | sed 's/^Welcome to Codeup, //')
    [[ -n "$username" ]] || _die "could not parse username from SSH banner: $out"

    # Prefer explicit email (env or saved); else synthesize a
    # Codeup-style noreply address.  Codeup itself uses synthetic
    # Teambition mailbox addresses internally, so a synthesized
    # noreply email is consistent with platform convention.
    local email
    email=$(_get_email)
    [[ -n "$email" ]] || email="${username}@users.noreply.github.com/example"

    echo "username: $username"
    echo "email:    $email"

    if [[ ! -f "$CODEUP_TOKENS_DIR/user" ]]; then
        _mkdir_tokens
        printf '%s' "$username" > "$CODEUP_TOKENS_DIR/user"
        chmod 600 "$CODEUP_TOKENS_DIR/user"
        _log "saved username → $CODEUP_TOKENS_DIR/user"
    fi
    if [[ ! -f "$CODEUP_TOKENS_DIR/email" ]]; then
        _mkdir_tokens
        printf '%s' "$email" > "$CODEUP_TOKENS_DIR/email"
        chmod 600 "$CODEUP_TOKENS_DIR/email"
        _log "saved email → $CODEUP_TOKENS_DIR/email"
    fi
}

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

cmd_git_push() {
    local branch="${1:-}"
    [[ -n "$branch" ]] || _die "usage: git-push <branch>"
    local ssh_cmd; ssh_cmd="$(_ssh_command)"
    _log "pushing HEAD → origin/$branch (SSH, scoped command)"
    GIT_SSH_COMMAND="$ssh_cmd" git push origin "HEAD:refs/heads/$branch"
}

cmd_create_mr() {
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
    local resp
    resp=$(_api_post_json "/repositories/$repo_id/changeRequests" "$payload")

    local detail_url local_id state
    detail_url=$(echo "$resp" | jq -r '.detailUrl // empty')
    local_id=$(echo   "$resp" | jq -r '.localId   // empty')
    state=$(echo      "$resp" | jq -r '.state     // empty')
    [[ -n "$detail_url" ]] || { echo "$resp" >&2; _die "no detailUrl in create-mr response"; }

    # Use `if` blocks (not `[[ ]] && echo`) so that an empty field at
    # the end of the function doesn't propagate `1` as the function's
    # exit code — `if` with a false test and no else returns 0.
    # Codeup's create response has no top-level `state` field (that one
    # appears only in GET/list responses); keeping the line for future
    # compatibility.
    echo "webUrl:  $detail_url"
    if [[ -n "$local_id" ]]; then echo "localId: $local_id"; fi
    if [[ -n "$state"    ]]; then echo "state:   $state";   fi
}

# ─────────────────────── dispatch ─────────────────────────────────

usage() {
    sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//; /^set /d'
}

main() {
    local cmd="${1:-}"
    [[ $# -ge 1 ]] && shift || true
    case "$cmd" in
        save-token)    cmd_save_token    "$@" ;;
        save-org)      cmd_save_org      "$@" ;;
        save-ssh-key)  cmd_save_ssh_key  "$@" ;;
        save-identity) cmd_save_identity "$@" ;;
        whoami)        cmd_whoami        "$@" ;;
        test-ssh)      cmd_test_ssh      "$@" ;;
        resolve-repo)  cmd_resolve_repo  "$@" ;;
        git-push)      cmd_git_push      "$@" ;;
        create-mr)     cmd_create_mr     "$@" ;;
        ""|-h|--help)  usage ;;
        *) _die "unknown subcommand: $cmd (run '$0 --help')" ;;
    esac
}

main "$@"
