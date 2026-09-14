---
name: pr-list
description: "Query Codeup MR state: list MRs by branch/state, get MR detail. Trigger: 'check MRs', 'list open MRs'."
---

# PR List

Query Codeup merge request state. Used by deploy-feedback-agent-patches and any skill that needs to check if a branch has an open/merged/closed MR.

## Subcommands

All via `scripts/pr-list.sh`:

### list-mrs

List MRs for the current repo. Filters are optional.

```bash
# List all open MRs for this repo
pr-list.sh list-mrs --state opened

# List MRs from a specific source branch
pr-list.sh list-mrs --source gap-6-nvlinkfailure-ib-nic-counter-check

# Combine filters
pr-list.sh list-mrs --state opened --source gap-6-nvlinkfailure-ib-nic-counter-check

# List all MRs (no filter)
pr-list.sh list-mrs
```

Output: one JSON object per line with `{localId, state, sourceBranch, targetBranch, hasConflict, detailUrl, title}`.

`--state` values: `opened`, `merged`, `closed`. Server-side filter.
`--source` filters client-side (API has no sourceBranch param).

### get-mr

Get full detail for a single MR.

```bash
pr-list.sh get-mr 28
```

Output: JSON with `{localId, state, sourceBranch, targetBranch, hasConflict, conflictCheckStatus, allRequirementsPass, mergedRevision, mergedAt, detailUrl, title}`.

MR states in detail endpoint: `UNDER_REVIEW`, `TO_BE_MERGED`, `MERGED`, `CLOSED`, `UNDER_DEV`.

## Credentials

Uses the same env vars as pr-creation: `CODEUP_TOKEN`, `CODEUP_ORG_ID`. Resolved from env → `/run/secrets/codeup/` → `~/.codeup_tokens/`.
