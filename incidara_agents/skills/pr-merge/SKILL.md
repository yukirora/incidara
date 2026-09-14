---
name: pr-merge
description: "Merge a pr-integration-produced integration branch into its target on Aliyun Codeup, after an external CI agent has signaled pass. Opens an integration MR, merges it, then closes each contributing MR with a comment link, deletes merged source branches and the integration branch. Trigger on phrases like 'merge integration', 'ship integration branch', 'pr merge', '合并 integration', '把 integration 合进 target', 'CI 过了合进去', '完成合并'."
---

# Codeup PR (MR) Merge

Third of the three code/repo skills. Takes an integration branch built by pr-integration and — **assuming external CI has verified the tree** — merges it into its target, then performs the administrative cleanup of the original contributing MRs, their source branches, and the integration branch itself.

This skill does NOT run CI. It trusts its caller. It must only be invoked when CI on the integration commit has already passed.

**Trigger phrases:** "merge integration", "ship integration branch", "pr merge", "合并 integration", "把 integration 合进 target", "CI 过了合进去", "完成合并".

---

## Why merge the integration branch itself (and not each original MR)

Auto-resolved conflicts produced during `pr-integration` live as commits on the integration branch, not on any original MR's source branch. Merging individual MRs into target after CI passed on integration would re-run git's 3-way merge without those resolutions — producing either a different tree or fresh conflicts. The **only** tree whose semantics CI verified is the integration branch's tree, so that is the tree we merge. Contributing MRs then get closed administratively as "merged via integration MR #N".

---

## API endpoints and auth

Same Yunxiao OpenAPI base and `x-yunxiao-token` header as the other code/repo skills. This skill's script is **self-contained** — it duplicates auth/SSH scaffolding rather than sourcing sibling skills.

| Operation | Method + path |
|---|---|
| Create MR | `POST /repositories/{id}/changeRequests` |
| Get MR detail | `GET /repositories/{id}/changeRequests/{localId}` |
| Merge MR | `POST /repositories/{id}/changeRequests/{localId}/merge` (body `{"mergeType":"no-fast-forward"\|"fast-forward"}`) |
| Close MR | `POST /repositories/{id}/changeRequests/{localId}/close` |
| Comment MR | `POST /repositories/{id}/changeRequests/{localId}/comments` (body `{"content":"…","commentType":"GLOBAL_COMMENT","resolved":false}`) |
| Delete branch | git push over SSH: `git push origin --delete <branch>` |

---

## Subcommands (helpers Claude calls)

All from `${CLAUDE_SKILL_ROOT}/scripts/pr-merge.sh`:

| Subcommand | Purpose |
|---|---|
| `prepare <integration_branch> <target>` | Fetch origin, read `integration/merge_log_<ts>.jsonl` from the integration branch's tree, classify entries. Emits JSON: `{integrationBranch, target, contributingMrs:[{localId,sourceBranch,status}], alreadyInTargetMrs:[…], skippedMrs:[…]}`. |
| `create-integration-mr --source <b> --target <b> --title <t> --body-file <f>` | Opens the integration MR. Echoes `{localId, detailUrl}`. |
| `merge-mr --mr-id <id> [--method auto\|no-ff\|ff]` | GETs MR detail to validate `state` / `hasConflict` / `allRequirementsPass`, then POSTs merge. `auto` picks `fast-forward` iff `supportMergeFastForwardOnly: true`, else `no-fast-forward`. |
| `comment-mr --mr-id <id> --body <text>` | POST a GLOBAL_COMMENT on an MR. |
| `close-mr --mr-id <id>` | Close an MR (idempotent; Codeup may already have auto-closed after integration merge). |
| `delete-branch <branch>` | Scoped-SSH delete of `origin/<branch>`. |
| `strip-audit <integration_branch>` | Fast-forward push that removes `integration/merge_log_<ts>.jsonl` from the integration tip, so the audit log doesn't propagate to target via the integration MR. Idempotent. Run AFTER `prepare` and BEFORE `create-integration-mr`. |

---

## End-to-end flow

The agent executes these steps **in order**. Abort (`RESULT.json status=failed`) only before step 4; after step 4 any cleanup failure is WARN-level and goes into `failed_cleanup` in the result.

### 1. Discover the integration artifact

```bash
SCRIPT=${CLAUDE_SKILL_ROOT}/scripts/pr-merge.sh
PLAN=$(bash "$SCRIPT" prepare "$INTEGRATION_BRANCH" "$TARGET_BRANCH")
echo "$PLAN" | jq
```

If `contributingMrs` is empty, stop — nothing to merge. Print the no-op sentinel:

```
{"status":"nothing_to_merge","integrationBranch":"$INTEGRATION_BRANCH","target":"$TARGET_BRANCH"}
```

### 2. Pre-flight conflict check against target

Make sure `integration_branch` still merges cleanly into current `origin/<target>`. If someone landed a conflicting change between CI and this run, bail out:

```bash
git merge-tree origin/"$TARGET_BRANCH" origin/"$INTEGRATION_BRANCH" \
    | grep -qE '^<<<<<<< |\\+<<<<<<< ' && {
    echo "pre-flight conflict: target has drifted since CI; rerun pr-integration first" >&2
    exit 1
}
```

If conflict detected, emit a failure sentinel and stop:

```
{"status":"pre_flight_conflict","integrationBranch":"…","target":"…"}
```

### 2.5. Strip the per-run audit log from the integration tip

`prepare` (step 1) has already captured the audit log content into `$PLAN`. The file (`integration/merge_log_<ts>.jsonl`) was useful as evidence on the integration branch, but it should NOT propagate into target — target accumulates audit logs from every prior integration round otherwise.

Push a fast-forward deletion commit to the integration branch BEFORE opening the integration MR, so the MR's tree no longer contains the file:

```bash
bash "$SCRIPT" strip-audit "$INTEGRATION_BRANCH" | jq
```

The subcommand is idempotent (`status:"already_absent"` if a previous attempt already stripped) and uses a temporary worktree so the caller's HEAD/index are untouched. Push is fast-forward only — if the integration tip moved since `prepare`'s fetch, the push fails and the script aborts.

### 3. Compose + open the integration MR

Title: `Integration - <N> MRs to <target> (<ts>)` where `<N>` = `contributingMrs | length`.

Body (write to `/tmp/integration_mr_body.md`):

```md
## Integrated MRs (merging this brings them into `<target>`)

- #<localId> — `<sourceBranch>` — <status>  ← for each contributing MR
  - conflict_count: N  ← only when status=auto_resolved

## Skipped MRs (NOT included, must be handled separately)

- #<localId> — `<sourceBranch>` — <reason>

## Already-in-target MRs (informational)

- #<localId> — `<sourceBranch>`

Built by pr-integration on <ts>. CI has passed on the integration commit.
```

Then:

```bash
RESP=$(bash "$SCRIPT" create-integration-mr \
    --source "$INTEGRATION_BRANCH" \
    --target "$TARGET_BRANCH" \
    --title  "$TITLE" \
    --body-file /tmp/integration_mr_body.md)
INTEGRATION_MR_ID=$(echo "$RESP" | jq -r .localId)
INTEGRATION_MR_URL=$(echo "$RESP" | jq -r .detailUrl)
```

### 4. Merge the integration MR

```bash
bash "$SCRIPT" merge-mr --mr-id "$INTEGRATION_MR_ID" --method auto
```

**From this point on, the target has the integration tree.** Cleanup failures below are WARN-level and recorded in `failed_cleanup`.

### 5. Per contributing MR: comment + close + delete source branch

For each `contributingMrs[i]`:

```bash
LOCAL_ID="${mr.localId}"
SRC="${mr.sourceBranch}"

bash "$SCRIPT" comment-mr --mr-id "$LOCAL_ID" \
    --body "Merged via integration MR #$INTEGRATION_MR_ID: $INTEGRATION_MR_URL" \
  || FAILED_CLEANUP+="comment:#$LOCAL_ID "

bash "$SCRIPT" close-mr --mr-id "$LOCAL_ID" \
  || FAILED_CLEANUP+="close:#$LOCAL_ID "

bash "$SCRIPT" delete-branch "$SRC" \
  || FAILED_CLEANUP+="delete:$SRC "
```

Codeup may have already auto-closed the MR (source fully in target); `close-mr` is idempotent either way.

**Do not touch `skippedMrs`.** Their source branches stay intact.

### 6. Delete the integration branch itself

```bash
bash "$SCRIPT" delete-branch "$INTEGRATION_BRANCH" \
  || FAILED_CLEANUP+="delete:$INTEGRATION_BRANCH "
```

### 7. Final output

Emit a single JSON object as the **LAST line** of your response. The entrypoint parses it:

```json
{"status":"success","integrationMrId":42,"integrationMrUrl":"https://github.com/example/.../change/42","targetBranch":"dev","mergedSourceBranches":["feat/add-alpha","feat/add-beta"],"closedSourceMrs":[15,16],"deletedBranches":["feat/add-alpha","feat/add-beta","integration/dev/20260511-130000"],"failedCleanup":[]}
```

Keys:

- `status ∈ {success, partial_success, failed, nothing_to_merge, pre_flight_conflict}`
- `partial_success` = integration merged but ≥1 cleanup step failed
- `failedCleanup` = list of strings (e.g. `"delete:feat/add-alpha"`) — empty on full success

---

## Safety rules

- **Never** `git push --force` — branches are only deleted via `git push --delete`.
- **Never** `git config --local/--global` — commit identity comes from env vars (but this skill almost doesn't make commits; only Codeup's own merge-commit is created server-side).
- **Never** touch a `skipped` MR or its source branch. These need human attention.
- **Never** proceed past step 2 if the pre-flight conflict check fails.
- **Do not retry** a failed integration-MR merge automatically — Codeup's error body tells you why (branch protection, reviewer requirement, new conflict). Surface it and stop.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `prepare` says "no integration/merge_log_&lt;ts&gt;.jsonl" | Integration branch wasn't produced by pr-integration (or audit log was manually removed). Rebuild via `make pr-integration`. |
| `merge-mr` → 405 "该状态下的评审不允许合并" | Integration MR isn't in a mergeable state. GET its detail; check `state`, `hasConflict`, `conflictCheckStatus`. |
| `merge-mr` → "allRequirementsPass=false" | Repo enforces approvals or checks that aren't met. The PAT can't bypass branch protections. Either get approvals or change the policy — don't try to force-merge. |
| pre-flight conflict detected | `target` advanced after CI passed. Re-run pr-integration to rebuild on top of the new target, then re-run pr-merge. |
| `delete-branch` fails on a `feat/*` source | Branch was protected or already gone. Record in `failedCleanup` and continue; not fatal. |
| Cleanup fully failed but target is merged | `status: partial_success`. The code shipped; sweep up residual branches / MR comments manually. |
