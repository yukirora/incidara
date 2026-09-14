---
name: deploy-feedback-agent-patches
description: "Autonomous deploy: check branches, create/update PRs, monitor merges. CI auto-deploys on merge. Trigger: 'deploy patches', 'check PRs'."
---

# Deploy Skill Patches

Periodic skill: check problem branches against git/codeup, advance them toward merge. No stored status transitions — git IS the source of truth.

**CI auto-deploys on merge.** When a PR merges to main, the CI pipeline rsyncs code to the deployment machine. Skills are volume-mounted from `repo_dir/incidara_agents/skills/`, so changes are immediately visible to all agent containers — no rebuild, no restart needed. New sessions pick up the updated skills automatically.

## Step 1: Find Problems with Patches

```
get_problems(status="patch_created")  # problems where branch is pushed but not yet deployed
```

`patch_commit` contains the branch name.

If none, stop.

## Step 2: Review & Check Branch State

Clone repo to a temporary directory to avoid conflicts with concurrent tasks:
```bash
REPO_DIR=$(mktemp -d /tmp/repo-XXXXXX)
git clone git@codeup.aliyun.com:your-org/incidara.git "$REPO_DIR"
cd "$REPO_DIR"
```
Fetch all branches.

For each problem, review the diff against main. **Reject** if:
- Fabricated thresholds
- Invented references without URLs
- Non-minimal diffs
- Dangerous decision rules

Rejected → problem back to `open`, clear `patch_commit`, note reason. Skip.

For each remaining branch, check its state on codeup using the pr-list script:

```bash
# List MRs for a specific source branch (client-side filter)
pr-list.sh list-mrs --source <branch_name>

# List MRs filtered by state (server-side filter: opened/merged/closed)
pr-list.sh list-mrs --state opened

# Get detailed state of a specific MR
pr-list.sh get-mr <mr_local_id>
```

| Branch state | MR state | Action |
|---|---|---|
| Branch exists, no MR found | — | Create MR via `pr-creation.sh create-mr`. Title format: **`[agent:feedback] <descriptive title>`** |
| MR exists, UNDER_REVIEW/TO_BE_MERGED, no conflicts | — | Skip, wait |
| MR exists, UNDER_REVIEW, has conflicts | — | Rebase branch, force-push, resolve conflicts |
| MR merged (MERGED state + mergedRevision set) | — | Get merge commit SHA, mark monitoring |
| MR CLOSED/REJECTED | — | Problem → `open`, clear `patch_commit` |
| Branch missing on origin | — | Problem → `open`, clear `patch_commit` |

## Step 3: Process Branches in Order

**Step 3a: Map overlaps.** Check which branches touch the same files. Branches with no overlap can have PRs created in parallel. Branches with overlap need sequential handling.

**Step 3b: Non-overlapping branches.** Review diff, create PR. These are independent.

**Step 3c: Overlapping branches.** For branches touching the same file(s), determine merge order by reading diffs:
- Tool/script fixes go first (other branches may depend on the tool working)
- Skill rule changes go second
- If no clear dependency, use problem_id order (oldest first)

Create PR for the first branch. For subsequent overlapping branches, first check if the earlier PR is merged yet. If not, wait — don't create a PR that will immediately conflict with an open PR on the same file.

If earlier PR is merged and the branch now conflicts with main, rebase on main first, then create PR.

**Conflict resolution during rebase:**
- Markdown skill files → accept both (additive, different sections)
- Python files → understand both, write correct merged code
- Cannot resolve → problem back to `open`, note reason, skip that branch

## Step 4: Mark Monitoring for Merged PRs

**No manual deploy needed.** CI pipeline auto-syncs merged code to the deployment machine. Skills are volume-mounted, so changes are live immediately.

For any problems whose PRs are now merged:

1. Get the merge commit SHA from main (the commit that the PR merge created on main)
2. Update the problem:

```
update_problem_tool(
    problem_id=<id>,
    status="monitoring",
    patch_commit="<main-branch commit SHA from merge>",
    monitor_expectation=...
)
```

**Important:** `patch_commit` MUST be the commit SHA on main after merge (e.g., `d64bc1ad2d1b053d31f9accfb924370affc70735`), NOT the branch name. The guard blocks `patch_created → monitoring` if `patch_commit` is still a branch name.

**How to get the merge commit SHA:**
```bash
git fetch origin
git log origin/main --oneline -5  # find the merge commit
```

## Key Principles

- **Git/codeup is source of truth** — branch state drives actions, not status fields
- **CI auto-deploys on merge** — no manual SCP/SSH/rebuild needed
- **Skills are volume-mounted** — changes visible immediately after CI rsync
- **No agent restart needed** — new sessions pick up updated skills automatically
- **Auto-resolve markdown conflicts** — additive, accept both
- **Handle stale PRs** — rebase if main advanced, force-push
- **Reject bad branches** → back to `open`, don't block others
- **Idempotent** — safe to re-run anytime
