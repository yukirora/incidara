# Skill Optimize

Optimize agent skills based on diagnosed accuracy gaps. One session per problem: patch → test → commit.

## When to Use

- Triggered by `case-diagnosis` delegation (next_owner="skill-optimize")
- Manual: "optimize skill for NodeCrash NFF" or "resolve problem #3"

## Input

From `case-diagnosis` delegation prompt:
- Problem ID, diagnosis, gap, root cause, evidence, attribution
- **problem_id** — extract from the title (e.g. "Problem #19: ..." → `problem_id=19`)

**IMPORTANT: Use the existing problem_id from the delegation prompt.** Do NOT call `insert_analysis_problem_tool` — the problem already exists in the DB. All updates go through `update_problem_tool(problem_id, ...)` with the problem_id from the title.

## Architecture

```
Input: problem from case-diagnosis (diagnosis already complete)
       │
       ▼
  Phase 1: Read Diagnosis (from problem DB)
    - Load diagnosis from analysis_problems row
    - Verify gap + root cause + evidence are present
       │
       ▼
  Phase 2: Patch & Validate (sub-skills/patch-and-validate)
    - Clone repo → read skills → design fix → write diffs → validate
       │
       ▼
  ★ Gate: Present diff + validation to human → approve/reject
  ⛔ DO NOT commit or push before human approval.
       │
       ▼
  Phase 3: Commit & Push
    - Commit + push branch
    - Update problem → status="patch_created"
    - STOP.
```

> **Repo dir**: Always use `/tmp/repo-<problem_id>` (e.g. `/tmp/repo-19`). This prevents conflicts when multiple tasks run concurrently. Never use a fixed path like `/app/workspace/repo`.

## Phase 1: Read Diagnosis

The diagnosis was already completed by `case-diagnosis`. Load it from the problem record.

Call `get_problems(problem_id=<problem_id>)` to read the `diagnosis` field. It should contain:
- `attribution` (which agent is at fault)
- `fix_route` (skill code vs rule code)
- `gap` (what went wrong)
- `root_cause` (why)
- `evidence` (what the agent missed)
- `next_owner` (should be "skill-optimize")
- Any broken tools/scripts to validate

**If diagnosis is missing or incomplete**: Stop. Do NOT re-diagnose yourself.
Reply that the problem needs `case-diagnosis` first — the diagnosis phase must be
completed independently before patching.

**⛔ Feasibility check — verify the fix is possible from the agent's perspective.**
Before designing the patch, ask: can the agent actually do what the diagnosis prescribes,
given the context it operates in? Examples where a fix candidate is impossible:
- **"Add SSH check when node is SSH unreachable"** — the agent cannot SSH to a down node
- **"Check vendor verdict before deciding"** — triage/repair agents don't see vendor verdicts
- **"React to MAINTENANCE_FIX status"** — agents don't know RMA outcomes at decision time

If the primary fix candidate is impossible from the agent's perspective:
1. **Try secondary_gaps** — the diagnosis may list alternative candidates
2. **If no viable candidate exists**, set the problem to `wont_fix` via `update_problem_tool(problem_id, status="wont_fix")` and explain why
3. **Do NOT implement an impossible fix** — a skill change that requires information the agent doesn't have is worse than no change

Note: case-diagnosis should have already filtered infeasible candidates. If you find one, it's a diagnosis quality issue — but handle it here as a safety net.

**⛔ Do NOT re-investigate or re-diagnose.** That's `case-diagnosis`'s job, not yours.
You are the patch phase only.

## Phase 2: Patch & Validate

Read `sub-skills/patch-and-validate/SKILL.md` and execute all 6 steps.

Now — and only now — you clone the repo, read skill files, and design the fix.

**⛔ CRITICAL: Do NOT commit or push.** After validation and backtest, present the Gate to the human and STOP. The repo stays on a local branch until human approves.

## Phase 3: Commit & Push

**Only executed AFTER human explicitly approves the Gate.**

1. **Commit on feature branch** — use message format: `fix(skill): <problem title>`
2. **Push branch** — now it's approved
3. **Update problem** — set `status="patch_created"`, set `patch_commit` to the branch name, set `monitor_expectation`.

**⛔ Do NOT create a PR. Do NOT call `delegate_to_agent`. Do NOT set `monitoring`.**

## Types of Fixes

Not every fix is an edit to an existing skill file:

| Fix Type | When | Example |
|---|---|---|
| **Edit existing skill** | Missing check, wrong rule, missing decision branch | Add row-remapper check to investigation-methodology |
| **Create new skill** | A repeatable sub-procedure that multiple agents need | `job-check` for reading validation job logs |
| **Add MCP tool** | The agent needs a capability that no current tool provides | `get_row_remapper_state` tool |
| **Edit MCP tool** | A tool exists but has a bug, missing parameter, or wrong query | Fix `get_validation_job` to search `forceNodes` |
| **Fix broken script/tool** | A script or tool failed during the original investigation | `ltp.sh logs` returned error — fix the script |

**⚠️ If a tool/script failed during the original investigation, you MUST test it yourself in Phase 2.** Adding "use this tool" to the skill is pointless if the tool doesn't work.

## Key Principles

- **Diagnosis is done by `case-diagnosis`, not by you** — you receive a completed diagnosis and patch it
- **One problem, one session** — full context preserved from patch to commit
- **One human gate** — agent self-checks, then presents diff + validation for human approval. **NEVER push before human says "approved" or "LGTM".**
- **Clean workspace first** — remove only your own `/tmp/repo-<problem_id>` if it exists from a prior attempt
- **Fix the tool, not just the skill** — if a tool was broken, test it and fix it
