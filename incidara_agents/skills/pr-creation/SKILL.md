---
name: pr-creation
description: "Create a merge request (MR / pull request) on Aliyun Codeup from working-tree changes. End-to-end flow: stage → commit → push → open MR. Trigger on phrases like 'create PR', 'create MR', 'open pull request', 'open merge request', '提 MR', '开 MR', '提交合并请求', '发起 MR', 'open a PR on codeup', 'push and create MR'."
---

# Codeup MR (Merge Request) Creation

End-to-end automation for turning working-tree changes into a Codeup merge request: stage, commit, push over SSH, then open the MR via the Yunxiao Codeup OpenAPI. Two independent auth layers — **PAT (as `x-yunxiao-token` header) for the MR API, SSH key for `git push`**. HTTPS git push is blocked by policy and is not used.

**Trigger phrases:** "create PR", "create MR", "open pull request", "open merge request", "提 MR", "开 MR", "提交合并请求", "发起 MR", "push and open MR".

---

## API endpoints (verified against a real Codeup enterprise)

- **Base:** `https://openapi-rdc.aliyuncs.com/oapi/v1/codeup/organizations/{org_id}`
- **Auth header:** `x-yunxiao-token: <PAT>` (the PAT value is *not* passed in the query string)
- **List repos:** `GET /repositories?search=<name>` → items contain numeric `id`
- **Create MR:** `POST /repositories/{id}/changeRequests` — body must include `{title, description, sourceBranch, targetBranch, sourceProjectId, targetProjectId}` (both project IDs = the repo's own numeric `id`)
- **Response** on create-MR: `detailUrl` is the user-facing URL; `localId` is the MR number

The older `devops.cn-hangzhou.aliyuncs.com` / `/api/v4/...` endpoints require AK+SK signed requests — PATs do not work there. Some deploy tiers also return `UnsupportedInCurrentEnv` for `/oapi/v1/platform/users/...`, so `whoami` uses the SSH banner instead (see below).

---

## Quick Start

### One-time setup (workstation)

```bash
SCRIPT=${CLAUDE_SKILL_ROOT}/scripts/pr-creation.sh

# SSH key (used only for `git push`).  Populates ~/.codeup_tokens/ssh_key
# and runs `ssh-keyscan github.com/example` to fill known_hosts.
bash $SCRIPT save-ssh-key ~/.ssh/codeup_bot_ed25519

# PAT (used only for the MR API).
bash $SCRIPT save-token <access-token>

# Codeup organization ID.
bash $SCRIPT save-org 123456

# Resolve and cache commit-author identity from the PAT.
bash $SCRIPT whoami

# Verify SSH reachability.
bash $SCRIPT test-ssh
```

### Production (container / CI)

Deliver the same secrets as env vars or as files in `$CODEUP_SECRETS_DIR` (default `/run/secrets/codeup/`):

| Credential | Env var | File (in `$CODEUP_SECRETS_DIR`) |
|---|---|---|
| PAT | `CODEUP_TOKEN` | `token` |
| Organization ID | `CODEUP_ORG_ID` | `org_id` |
| Commit author name | `CODEUP_USER` | `user` |
| Commit author email | `CODEUP_EMAIL` | `email` |
| SSH private key | `CODEUP_SSH_KEY_PATH` / `CODEUP_SSH_KEY_CONTENT` | `ssh_key` (mode 600) |
| Known hosts | — | `known_hosts` |

Discovery order: **env > `$CODEUP_SECRETS_DIR` > `~/.codeup_tokens/` > fail**. The script never falls back to `~/.ssh/*` or `git config --global`.

---

## Subcommands

| Subcommand | Purpose |
|---|---|
| `save-token <pat>` | Save PAT to `~/.codeup_tokens/token` (mode 600). |
| `save-org <id>` | Save organization ID. |
| `save-ssh-key <path>` | Copy private key + run `ssh-keyscan` to populate `known_hosts`. |
| `save-identity <name> <email>` | Save commit-author identity manually. |
| `whoami` | Parse `Welcome to Codeup, <user>!` from the SSH banner → username + synthesized noreply email. Auto-saves on first run. |
| `test-ssh` | Verify SSH reachability with the scoped config. |
| `resolve-repo [remote]` | `git remote` URL → numeric Codeup repo ID (cached). |
| `git-push <branch>` | Push `HEAD` via a scoped `GIT_SSH_COMMAND` (no `~/.ssh` / `.git/config` mutation). |
| `create-mr --source <b> --target <b> --title <t> --body-file <f>` | Open the MR via Yunxiao OpenAPI, print `webUrl` (`detailUrl`) + `localId`. |

---

## End-to-end flow

The agent executing this skill should run the steps below **in order**, confirming with the user at decision points (commit message, PR title/body) before moving on.

### 1. Classify the current state

Before editing anything, figure out which of these cases you're in:

```bash
git status --porcelain                                   # working-tree changes?
git rev-list --count origin/<current>..HEAD              # local commits not yet pushed?  (0 if fully in sync)
git rev-list --count origin/<target>..origin/<current>   # branch already ahead of target on origin?
```

| Case | Working tree | HEAD vs origin/current | origin/current vs origin/target | Action |
|---|---|---|---|---|
| **A. Fresh work** | dirty | — | — | Full flow: stage → commit → push → create MR |
| **B. Local commits only** | clean | HEAD ahead of origin/current | — | Skip edit; push (step 4) → create MR |
| **C. Branch ready** | clean | HEAD == origin/current | origin/current ahead of origin/target | **Skip edit, skip commit, skip push. Jump straight to step 5 (title/body) and step 6 (create-mr).** |
| **D. Nothing to ship** | clean | HEAD == origin/current | origin/current == origin/target (no divergence) | Stop. There is nothing to MR. |

**Do not synthesize changes** just to have something to commit. If the user's task prompt is "open an MR" / "提 MR" and the branch is already in Case C, go directly to create-mr — adding a made-up edit is wrong.

### 2. Refuse to operate on protected branches

If `git branch --show-current` is `main` or `master`, stop and ask the user to switch to or create a feature branch. Do not override without an explicit confirmation.

### 3. Propose and make the commit   *(Cases A only)*

**Commit message style**: follow this repo's dominant pattern — `<Component> - <Action>`, title-case, e.g.:

- `Reproducer Agent - Fix bugs and refactor folder structure`
- `Infra(postgres) - Add pricing table schema + seed`
- `Optimization Pipeline - Initiate dev doc and refactor reproducer`

Show the diff summary + the proposed message to the user. After confirmation:

```bash
# Stage explicit paths — do NOT use `git add -A`.
git add path/to/changed-file1 path/to/changed-file2

# Commit with scoped author env vars — no `git config --local/--global` writes.
CODEUP_USER=$(bash $SCRIPT ...)    # from saved identity
CODEUP_EMAIL=$(bash $SCRIPT ...)
GIT_AUTHOR_NAME="$CODEUP_USER"  GIT_AUTHOR_EMAIL="$CODEUP_EMAIL" \
GIT_COMMITTER_NAME="$CODEUP_USER" GIT_COMMITTER_EMAIL="$CODEUP_EMAIL" \
    git commit -m "<Component> - <Action>"
```

If a pre-commit hook fails, fix the underlying issue and create a **new** commit — do not `--amend`.

### 4. Push   *(Cases A + B only — skip in Case C, branch is already pushed)*

```bash
BRANCH=$(git branch --show-current)
bash $SCRIPT git-push "$BRANCH"
```

`git-push` builds a scoped `GIT_SSH_COMMAND` with `-i <key> -o IdentitiesOnly=yes -o UserKnownHostsFile=<kh> -o StrictHostKeyChecking=yes`. No writes to `~/.ssh/` or `.git/config`.

### 5. Draft MR title + body   *(all cases that reach here)*

**Title**: same `<Component> - <Action>` style as the commit. If the MR spans multiple commits, use a title that summarizes the whole change rather than echoing the last commit.

**Body template** (write to a temp file, let the user edit before submit):

```md
## Summary
- <what & why, 1–3 bullets>

## Changes
- <file or area bullets>

## Test plan
- [ ] <how to verify>
```

### 6. Create the MR

```bash
TITLE="<Component> - <Action>"
BODY_FILE=/tmp/mr_body.md        # already populated in step 5
bash $SCRIPT create-mr \
    --source "$BRANCH" \
    --target main \
    --title "$TITLE" \
    --body-file "$BODY_FILE"
```

The script prints:

```
webUrl:  https://github.com/example/<org>/<repo>/change/<id>
localId: <id>
state:   TO_BE_MERGED
```

Surface the `webUrl` to the user as the final output.

---

## Safety rules

- **Never** `git push --force` or `--force-with-lease` unless the user explicitly asks for it.
- **Never** `git commit --amend` on a commit that has already been pushed.
- **Never** `--no-verify` / `--no-gpg-sign` / skip hooks.
- **Never** stage paths matching `.env*`, `*credential*`, `*secret*`, `*.pem`, `*.key` without the user explicitly naming them.
- **Never** write `git config --global` or `git config --local user.*` — author identity flows through `GIT_AUTHOR_*` env vars only.
- **Never** mutate the user's `~/.ssh/` or rely on ambient ssh-agent keys — `git-push` uses a scoped `GIT_SSH_COMMAND`.

---

## Troubleshooting

| Error | Cause / fix |
|---|---|
| `CODEUP_TOKEN not set and no saved token` | Run `save-token <pat>` or export `CODEUP_TOKEN=…`. |
| `no SSH key found` | Run `save-ssh-key <path>` or set `CODEUP_SSH_KEY_PATH=…` / mount at `$CODEUP_SECRETS_DIR/ssh_key`. |
| `no known_hosts file found` | Re-run `save-ssh-key <path>` (populates `known_hosts` via `ssh-keyscan`). In a container, mount `$CODEUP_SECRETS_DIR/known_hosts`. |
| `repo <path> not found among ListRepositories results` | PAT lacks access to that repo, or the `<path-slashes>_repo_id` cache is stale. Delete `~/.codeup_tokens/<slug>_repo_id` and retry. |
| HTTP 401 / `InvalidAccessToken` | PAT expired / revoked. Issue a new one and `save-token`. |
| HTTP 403 `Current token has no permission to api` | PAT scope is missing. The skill needs `代码仓库 读` + `合并请求 读写` at minimum (others are nice-to-have). |
| HTTP 400 `UnsupportedInCurrentEnv` | The called endpoint isn't enabled for this Codeup enterprise's deploy tier. For `whoami`, the skill already works around this by parsing the SSH banner; for `create-mr`, contact the Codeup enterprise admin. |
| HTTP 404 on `resolve-repo` | Wrong `CODEUP_ORG_ID`, or PAT belongs to a different org. |
| HTTP 409 on `create-mr` | Duplicate MR for the same source→target, or `source == target`. |
| `Host key verification failed` | Host keys rotated. Re-run `save-ssh-key <path>` to refresh `known_hosts`. |
| `git push` prompts for password | Something is pulling ambient keys — confirm you're invoking via `pr-creation.sh git-push`, not plain `git push`. |
