#!/usr/bin/env bash
# LTP (OpenPAI) job management CLI
#
# Usage:
#   ltp.sh login                          # Login and cache token
#   ltp.sh save-token <token>             # Save token for current host
#   ltp.sh submit <job.yaml>              # Submit a job
#   ltp.sh list [--state STATE] [--vc VC] [--user USER] [--limit N]
#   ltp.sh status <user>~<job>            # Show job details
#   ltp.sh stop <user>~<job>               # Stop a job
#   ltp.sh ssh <user>~<job>               # Get SSH connection info
#   ltp.sh config <user>~<job>            # Get raw job config
#   ltp.sh logs <user>~<job> [idx|last] [--stream stdout|stderr|all]
#
# Auth priority: LTP_TOKEN env > ~/.ltp_tokens/<host>_token > interactive login
# Required: LTP_HOST (e.g., http://192.0.2.10)

set -euo pipefail

HOST="${LTP_HOST:-}"
USER="${LTP_USER:-}"
TOKEN_DIR="${HOME}/.ltp_tokens"
_die() { echo "ERROR: $*" >&2; exit 1; }
_require() { [[ -n "${!1:-}" ]] || _die "env var $1 is not set"; }

# Get host identifier for token file (sanitize URL)
_get_host_id() {
    local host="${1:-${LTP_HOST:-}}"
    [[ -z "$host" ]] && { echo ""; return; }
    # Remove protocol, port, and path to get a simple host identifier
    echo "$host" | sed -E 's|https?://||; s|/.*||; s/[^a-zA-Z0-9._-]/_/g'
}

_normalize_host() {
    local host="${1:-}"
    [[ -z "$host" ]] && { echo ""; return; }
    host="${host%/}"
    [[ "$host" =~ /rest-server$ ]] || host="${host}/rest-server"
    echo "$host"
}

_get_token_file() {
    local host_id; host_id=$(_get_host_id)
    [[ -z "$host_id" ]] && _die "LTP_HOST is not set"
    echo "${TOKEN_DIR}/${host_id}_token"
}

_get_user_file() {
    local host_id; host_id=$(_get_host_id)
    [[ -z "$host_id" ]] && _die "LTP_HOST is not set"
    echo "${TOKEN_DIR}/${host_id}_user"
}

# Get saved username
_get_saved_user() {
    local user_file; user_file=$(_get_user_file)
    [[ -f "$user_file" ]] && cat "$user_file" || echo ""
}

# Cross-platform stat for file modification time
_stat_mtime() {
    local file="$1"
    if stat -c %Y "$file" 2>/dev/null; then
        return  # GNU stat (Linux)
    elif stat -f %m "$file" 2>/dev/null; then
        return  # BSD stat (macOS)
    else
        echo "0"
    fi
}

# Load token from various sources
_get_token() {
    # Priority 1: Environment variable
    [[ -n "${LTP_TOKEN:-}" ]] && { echo "$LTP_TOKEN"; return; }
    
    # Priority 2: Saved token file
    local token_file; token_file=$(_get_token_file)
    if [[ -f "$token_file" ]]; then
        # Check if token is expired (9 hours = 32400 seconds)
        local age=$(( $(date +%s) - $(_stat_mtime "$token_file") ))
        if [[ $age -lt 32400 ]]; then
            cat "$token_file"
            return
        fi
    fi
    
    _die "No valid token. Run: ltp.sh save-token <token> or ltp.sh login"
}

# Save token for current host
_save_token() {
    local token="$1"
    local host_id; host_id=$(_get_host_id)
    [[ -z "$host_id" ]] && _die "LTP_HOST is not set"
    
    mkdir -p "$TOKEN_DIR"; chmod 700 "$TOKEN_DIR"
    local token_file; token_file=$(_get_token_file)
    echo "$token" > "$token_file"; chmod 600 "$token_file"
    
    # Also save username if available
    if [[ -n "${LTP_USER:-}" ]]; then
        local user_file; user_file=$(_get_user_file)
        echo "$LTP_USER" > "$user_file"; chmod 600 "$user_file"
    fi
    
    echo "Token saved for host: $host_id"
}

# Auto-save token if from env var and operation succeeds
_auto_save_token() {
    local token="${LTP_TOKEN:-}"
    [[ -z "$token" ]] && return
    
    local host_id; host_id=$(_get_host_id)
    [[ -z "$host_id" ]] && return
    
    local token_file; token_file=$(_get_token_file)
    
    # Only save if not already saved or different token
    if [[ ! -f "$token_file" ]] || [[ "$(cat "$token_file")" != "$token" ]]; then
        _save_token "$token"
        echo "(Token auto-saved for future use)"
    fi
}

_fetch_token() {
    [[ -z "${LTP_HOST:-}" ]] && { read -r -p "LTP host: " LTP_HOST; export LTP_HOST; }
    [[ -z "${LTP_USER:-}" ]] && { read -r -p "LTP username: " LTP_USER; export LTP_USER; }
    [[ -z "${LTP_PASS:-}" ]] && { read -r -s -p "LTP password: " LTP_PASS; echo; export LTP_PASS; }

    local host; host=$(_normalize_host "$LTP_HOST")
    local body tok http_code
    for path in /api/v2/authn/basic/login /api/v1/authn/basic/login; do
        body=$(curl -sS -w "\n__HTTP_CODE__:%{http_code}" -X POST \
            -d "username=${LTP_USER}" -d "password=${LTP_PASS}" -d "expiration=36000" \
            "${host}${path}" 2>&1)
        http_code=$(echo "$body" | grep -o '__HTTP_CODE__:[0-9]*' | cut -d: -f2)
        body=$(echo "$body" | sed 's/__HTTP_CODE__:[0-9]*//')
        [[ "$http_code" == "200" ]] && \
            tok=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null) && break
        [[ "$http_code" == "404" ]] && continue
        _die "Login failed (HTTP $http_code) at ${host}${path}: $body"
    done
    [[ -n "${tok:-}" ]] || _die "Login failed: $body"

    _save_token "$tok"
    echo "$tok"
}

_api() {
    local method="$1" path="$2"; shift 2
    local host; host=$(_normalize_host "${LTP_HOST:-$HOST}")
    [[ -n "$host" ]] || _die "LTP_HOST is not set"
    
    local response
    response=$(curl -sS -w "\n__HTTP_CODE__:%{http_code}" -X "$method" \
        -H "Authorization: Bearer $(_get_token)" \
        -H "Accept: application/json" \
        "${host}${path}" "$@" 2>&1)
    
    local http_code=$(echo "$response" | grep -o '__HTTP_CODE__:[0-9]*' | tail -1 | cut -d: -f2)
    response=$(echo "$response" | sed 's/__HTTP_CODE__:[0-9]*//g')
    
    # Auto-save token on successful request
    if [[ "$http_code" == "200" ]] || [[ "$http_code" == "202" ]]; then
        _auto_save_token 2>/dev/null || true
    fi
    
    echo "$response"
    [[ "$http_code" =~ ^(200|201|202|204)$ ]] || return 1
}

_pretty() { python3 -m json.tool 2>/dev/null || cat; }

cmd_login() {
    rm -f "$(_get_token_file)"
    local tok; tok=$(_fetch_token)
    echo "Login successful. Token saved."
}

cmd_save_token() {
    local token="${1:-}"
    [[ -n "$token" ]] || _die "Usage: ltp.sh save-token <token>"
    _require LTP_HOST
    _save_token "$token"
}

cmd_submit() {
    local yaml="${1:-}"; [[ -f "$yaml" ]] || _die "Usage: ltp.sh submit <job.yaml>"

    # Generate unique suffix and modify YAML
    local suffix; suffix="$(date +%s)-$(openssl rand -hex 2 2>/dev/null || printf '%04x' $RANDOM)"
    local py; py=$(mktemp)
    cat > "$py" << 'PYEOF'
import sys, re
suffix = sys.argv[2]
with open(sys.argv[1]) as f: content = f.read()
match = re.search(r'^(name:\s*)(.+)$', content, re.MULTILINE)
if match:
    prefix = match.group(1)
    name = match.group(2).strip().strip('"').strip("'")
    content = re.sub(r'^(name:\s*)(.+)$', prefix + name + '-' + suffix, content, count=1, flags=re.MULTILINE)
print(content)
PYEOF
    local modified; modified=$(python3 "$py" "$yaml" "$suffix")
    rm -f "$py"

    local name; name=$(echo "$modified" | grep -E '^name:' | head -1 | sed "s/^name:[[:space:]]*//; s/[[:space:]]*$//; s/^['\"]*//; s/['\"]*$//")
    echo "Submitting: $name"
    echo "$modified" | _api POST /api/v2/jobs -H "Content-Type: text/yaml" --data-binary @- | _pretty
}

cmd_list() {
    local params="limit=50"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --state)  params="${params}&state=$2"; shift 2 ;;
            --vc)     params="${params}&vc=$2"; shift 2 ;;
            --user)   params="${params}&username=$2"; shift 2 ;;
            --limit)  params=$(echo "$params" | sed "s/limit=[0-9]*/limit=$2/"); shift 2 ;;
            --keyword) params="${params}&keyword=$2"; shift 2 ;;
            *)        _die "Unknown option: $1" ;;
        esac
    done
    _api GET "/api/v2/jobs?${params}" | python3 -c "
import sys, json, datetime
data = json.load(sys.stdin)
jobs = data if isinstance(data, list) else data.get('jobs', [])
if not jobs: print('No jobs found.'); sys.exit(0)
fmt = '{:<40} {:<10} {:<12} {:<20} {}'
print(fmt.format('NAME', 'STATE', 'VC', 'SUBMITTED', 'USER'))
print('-' * 100)
for j in jobs:
    s = j.get('jobStatus', {})
    state = j.get('state', s.get('state', ''))
    ts = j.get('submissionTime', s.get('submissionTime', 0))
    dt = datetime.datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M') if ts else '-'
    print(fmt.format(j.get('name','')[:39], state[:9], j.get('virtualCluster','')[:11], dt, j.get('username','')))
print(f'\nTotal: {len(jobs)} jobs')
"
}

cmd_status() {
    local frame="${1:-}"; [[ -n "$frame" ]] || _die "Usage: ltp.sh status <user>~<job>"
    _api GET "/api/v2/jobs/${frame}" | python3 -c "
import sys, json, datetime
d = json.load(sys.stdin); s = d.get('jobStatus', {})
state = s.get('state', 'UNKNOWN')
colors = {'RUNNING': '\033[32m', 'SUCCEEDED': '\033[34m', 'FAILED': '\033[31m', 'WAITING': '\033[33m'}
c = colors.get(state, ''); r = '\033[0m' if c else ''
print(f'Job:      {d.get(\"name\",\"\")}')
print(f'User:     {d.get(\"username\",\"\")}')
print(f'VC:       {d.get(\"virtualCluster\",\"\")}')
print(f'State:    {c}{state}{r}')
if s.get('submissionTime'):
    dt = datetime.datetime.fromtimestamp(s['submissionTime']/1000).strftime('%Y-%m-%d %H:%M:%S')
    print(f'Submitted: {dt}')
if s.get('completionTime'):
    dt = datetime.datetime.fromtimestamp(s['completionTime']/1000).strftime('%Y-%m-%d %H:%M:%S')
    print(f'Completed: {dt}')
if s.get('retries'): print(f'Retries:  {s[\"retries\"]}')
roles = d.get('taskRoles', {})
if roles:
    print('\nTask Roles:')
    for role, info in roles.items():
        tasks = info.get('taskStatuses', [])
        print(f'  {role}: {len(tasks)} task(s)')
        for t in tasks:
            print(f'    [{t.get(\"taskIndex\",\"?\")}] {t.get(\"taskState\",\"?\")}  container={t.get(\"containerId\",\"\")[:20]}')
print(f'\nDetail: ltp.sh config {d.get(\"username\",\"\")}~{d.get(\"name\",\"\")}')
print(f'SSH:    ltp.sh ssh {d.get(\"username\",\"\")}~{d.get(\"name\",\"\")}')
"
}

cmd_stop() {
    local frame="${1:-}"; [[ -n "$frame" ]] || _die "Usage: ltp.sh stop <user>~<job>"
    _api PUT "/api/v2/jobs/${frame}/executionType" -H "Content-Type: application/json" -d '{"value":"STOP"}' | _pretty
    echo "Stop signal sent to $frame"
}

cmd_ssh() {
    local frame="${1:-}"; [[ -n "$frame" ]] || _die "Usage: ltp.sh ssh <user>~<job>"
    _api GET "/api/v2/jobs/${frame}/ssh" | python3 -c "
import sys, json
d = json.load(sys.stdin); containers = d.get('containers', [])
if not containers: print('No SSH info (job not running).'); sys.exit(0)
for c in containers:
    print(f'--- {c.get(\"id\",\"?\")} ---')
    print(f'  ssh -p {c.get(\"sshPort\",\"?\")} root@{c.get(\"sshIp\",\"?\")}')
    if c.get('sshKeys'): print(f'  Identity: {c[\"sshKeys\"][0]}')
"
}

cmd_config() {
    local frame="${1:-}"; [[ -n "$frame" ]] || _die "Usage: ltp.sh config <user>~<job>"
    _api GET "/api/v2/jobs/${frame}/config"
}

cmd_logs() {
    local frame="${1:-}"
    [[ -n "$frame" ]] || _die "Usage: ltp.sh logs <user>~<job> [idx|last] [--stream stdout|stderr|all]"

    local idx=0 stream="stdout" arg_idx=0
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --stream) stream="$2"; shift 2 ;;
            last) arg_idx="last"; shift ;;
            [0-9]*) arg_idx="$1"; idx="$1"; shift ;;
            *) _die "Unknown: $1" ;;
        esac
    done

    if [[ "$arg_idx" == "last" ]]; then
        idx=$(_api GET "/api/v2/jobs/${frame}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
roles = d.get('taskRoles', {})
role = list(roles.keys())[0] if roles else None
tasks = roles[role].get('taskStatuses', []) if role else []
print(len(tasks) - 1 if tasks else 0)
" 2>/dev/null || echo "0")
    fi

    local log_path; log_path=$(_api GET "/api/v2/jobs/${frame}" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
roles = d.get('taskRoles', {})
role = list(roles.keys())[0] if roles else None
tasks = roles[role].get('taskStatuses', []) if role else []
t = tasks[$idx] if $idx < len(tasks) else (tasks[0] if tasks else None)
print(t.get('containerLog', '') if t else '')
" || echo "")
    [[ -n "$log_path" ]] || _die "No containerLog for $frame[$idx]"

    local locs; locs=$(_api GET "$log_path" 2>/dev/null || echo "")
    [[ -n "$locs" ]] || _die "Logs cleaned up for $frame[$idx]"

    local log_uri; log_uri=$(echo "$locs" | python3 -c "
import sys, json
locs = json.load(sys.stdin).get('locations', [])
stream = '$stream'
uris = {l['name']: l['uri'] for l in locs}
if stream not in uris:
    print(f'STREAMS: {\" \".join(uris.keys())}', file=sys.stderr); sys.exit(1)
print(uris[stream])
" 2>&1)
    [[ "$log_uri" == STREAMS:* ]] && _die "Stream '$stream' not found. Available: ${log_uri#STREAMS: }"

    echo "=== $frame [$idx] :: $stream ==="
    local host; host=$(_normalize_host "${LTP_HOST:-$HOST}")
    curl -sf "${host%/rest-server}${log_uri}" 2>/dev/null || _die "Failed to fetch logs"
}

cmd_events() {
    local frame="${1:-}"; [[ -n "$frame" ]] || _die "Usage: ltp.sh events <user>~<job>"
    _api GET "/api/v2/jobs/${frame}/events" | _pretty
}

CMD="${1:-}"
shift 2>/dev/null || true
case "$CMD" in
    login)  cmd_login "$@" ;;
    save-token) cmd_save_token "$@" ;;
    submit) cmd_submit "$@" ;;
    list)   cmd_list "$@" ;;
    status) cmd_status "$@" ;;
    stop)   cmd_stop "$@" ;;
    ssh)    cmd_ssh "$@" ;;
    config) cmd_config "$@" ;;
    logs)   cmd_logs "$@" ;;
    events) cmd_events "$@" ;;
    *) sed -n '/^# Usage:/,/^#$/p' "$0" | sed 's/^# \?//'; exit 1 ;;
esac
