# Patch and Validate

Take the diagnosis from case-diagnosis, design the fix, write the code diffs, and prove they work on real data.

**Prerequisite:** case-diagnosis completed (gap + root cause + evidence from re-investigation).

**This skill owns the fix.** It reads the current skill files, designs what to change, writes the actual diffs, and validates everything on real data. Case-diagnosis only diagnoses — this skill does the implementation.

---

## Step 1: Clone the Repo Fresh

Clone the repo now. Use `/tmp/repo-<problem_id>` to avoid conflicts with concurrent tasks.

```bash
GIT_SSH_COMMAND="ssh -i /root/.ssh/deploy_key -o StrictHostKeyChecking=no" \
  git clone git@codeup.aliyun.com:your-org/incidara.git /tmp/repo-${PROBLEM_ID}

cd /tmp/repo-${PROBLEM_ID}

# Create fresh branch
git checkout -b gap-<id>-<short-description>
```

## Step 2: Read Current Skills and Design the Fix

Read the skill files the diagnosis touches. You must see the exact current text before deciding what to change.

**Design the fix** based on the diagnosis from case-diagnosis:
- The gap tells you WHAT'S missing
- The root cause tells you WHY it happened (guides the fix style)
- The re-investigation evidence tells you WHAT the agent should have found

**Design answers:** "What specific change to which file would make the agent reach the correct conclusion next time?"

**Examples:**
- "In `repair-nodes/investigation-methodology.md` NVLink section: after the FabricManager row, add a row for reading validation job log via `ltp.sh logs`. Add a Decision: if direct NVLink checks clean AND job log shows IB errors, route to IB branch."
- "Copy `triage-nodes/sub-skills/job-check/ltp.sh` to `repair-nodes/sub-skills/job-check/` — original agent tried `ltp.sh logs` but script didn't exist. Also add `job-check` to repair-agent Makefile AGENT_SKILLS."

**Fix rules:**
- **Minimal** — add the missing check or decision, not new sections/hierarchies/prose
- **Each addition short** — one sentence per table row/checklist item
- **Evidence-based routing** — by what the evidence shows, not how the alert arrived
- **Investigation-result based** — "clean row-remap + single-PID burst" not "persistence signal present"
- **No invented thresholds** — collect evidence, route the known anti-pattern, don't hardcode gates
- **Anti-patterns from actual cases only** — observations, not guesses
- **When blocking an action, specify the alternative** — "do NOT file hardware RMA" must say what to do instead
- **Trace the full workflow** — categorization → investigation → classification → repair → revalidation → recovered
- **Every factual claim has a source** — URL or "unverified — needs human confirmation"

## Step 3: Write the Diffs

Apply the designed fix as code edits to the repo files.

**If the fix involves copying/creating files:**
```bash
mkdir -p /tmp/repo-${PROBLEM_ID}/incidara_agents/skills/repair-nodes/sub-skills/job-check/
cp /tmp/repo-${PROBLEM_ID}/incidara_agents/skills/triage-nodes/sub-skills/job-check/ltp.sh \
   /tmp/repo-${PROBLEM_ID}/incidara_agents/skills/repair-nodes/sub-skills/job-check/ltp.sh
chmod +x /tmp/repo-${PROBLEM_ID}/incidara_agents/skills/repair-nodes/sub-skills/job-check/ltp.sh
```

**If the fix requires updating Makefiles** (e.g., adding a skill to `AGENT_SKILLS`), edit the agent's Makefile too.

After editing, verify with `git diff`.

## Step 4: Validate on Real Data

**This is the most critical step. A patch is NOT validated until you prove it works on real data.**

For each change you made, walk through the patched skill's instructions on the original case:

1. **Find the node/job** from the case — use `get_validation_job`, `get_node_recent_jobs`, or case_memory
2. **Follow the patched skill step-by-step** — run the tools the skill says to run
3. **Show the raw output** and confirm the skill produces the correct conclusion

**Example:** If the fix added "when NVLink checks clean, read validation job log for IB errors":
- `get_validation_job(hostname="h200-000102")` → found `superbench_uaback_auto_tgVQULbU`
- `bash ${CLAUDE_SKILL_DIR}/sub-skills/job-check/ltp.sh logs demo.user~superbench_uaback_auto_tgVQULbU` → read actual output
- Grep for `ibv_*`, `mlx5_*`, `UCX ERROR` → confirmed `UCX ERROR ibv_create_ah on mlx5_8` IS extractable
- Conclusion: patched skill routes to IB branch ✓

### Test broken/fixed tools

If the fix involves copying, creating, or editing a script/tool that was broken in the original investigation:

1. **First: test on the original case data** — same node, same job. If it produces the expected output, the fix is validated.
2. **If original case can't be reproduced** (node repaired, job expired): **mock test or test on a similar schedulable/disabled node** — find a node in `validating`, `triaged_unknown`, `triaged_hardware`. Do NOT test on a running/production node. If no suitable node exists, use mock inputs.
3. **Do NOT stop at "the script runs"** — `ltp.sh --help` working ≠ `ltp.sh logs <job>` returning actual log content with error messages.

**If validation fails** (tool returns error, grep pattern too narrow, command not specific enough): fix the skill and re-test. Iterate until the patched skill reliably extracts the expected evidence from real data. Max 3 iterations.

**If all iterations fail:** `update_problem_tool(problem_id, status="blocked")` with explanation.

## Step 5: Backtest

Check REPAIR_CONFIRMED cases for the same fault_type. Would the fix break any correct classifications?

Use `query_similar_cases(classification="<fault_type>", limit=15)` and check:
- Would the new decision rule trigger on any correct cases?
- Would it route them away from hardware when they SHOULD be hardware?

If any correct case would break → refine the rule, re-validate (Step 4).

## Step 6: Present Gate

**DO NOT commit or push. Present to the human and STOP.**

After human approval, the branch will be pushed and a PR created. When the PR merges to main, the CI pipeline auto-deploys — skills are volume-mounted, so changes are live immediately. No manual deploy needed.

Show:
- **Diagnosis summary** — from case-diagnosis (what went wrong, root cause)
- **Fix design** — what you decided to change and why (Step 2)
- **Diff** — `git diff` output wrapped in a ` ```diff ` code block so the UI renders it with colored +/- lines. **Do NOT describe changes in prose.** Always show the actual before/after text.
- **Real-data validation** — what tools you ran, on what data, what output, whether the patched skill leads to the correct conclusion
- **Tool/script test results** — any copied/fixed tools tested end-to-end
- **Backtest results** — any correct cases that would break

**Include the Patch Rules Checklist:**
```
[ ] Minimal and concise — no new sections, hierarchies, or prose
[ ] Each addition short — one sentence per table row/checklist item
[ ] Evidence-based routing — by what evidence shows, not how alert arrived
[ ] Investigation-result based — not invented categories (e.g., "clean row-remap + single-PID burst" not "persistence signal present")
[ ] No invented thresholds — don't hardcode gates we don't know vendor thresholds for
[ ] No anti-pattern narratives in the skill code — those belong in diagnosis, not skill
[ ] When blocking, alternative specified — "do NOT file hardware RMA" must say what to do instead
[ ] Full workflow traced — for this case type, every branch is patched:
    - Triage: categorization rule updated? (Y/N/NA)
    - Triage: investigation steps updated? (Y/N/NA)
    - Classification decision updated? (Y/N/NA)
    - Platform repair path: actions specified? (Y/N/NA)
    - Hardware repair path: ticket content updated? (Y/N/NA)
    - Recurrence handling: what if fault recurs after platform repair?
[ ] Routing is not statistical — no "default to hardware because X% confirmed"
[ ] Every factual claim has source — URL or "unverified — needs human confirmation"
[ ] Fresh branch from main (old branch deleted)
[ ] All tools/scripts tested end-to-end on real data (not just --help)
```

**Good Decision vs bad Decision example:**
- ❌ Bad: "Persistence signal present (Pending/Failure rows > 0, OR Xid 63, OR sustained Xid, OR high DRAM Correctable) → hardware. No persistence signal → platform." — Invents a "persistence signal" category and pretends we know the vendor's threshold.
- ✅ Good: "Collect row-remap state + Xid burst timing + full ECC breakdown. Known anti-pattern (clean row-remap + single-PID burst + low aggregate) → revalidation first; if recurs, escalate to hardware." — Adds evidence collection, routes the proven anti-pattern, doesn't speculate about the rest.

**STOP AND WAIT for human approval.** "Continue" is NOT approval. Only "approved", "proceed", or "LGTM" counts.

If human says revise → make changes, re-validate (Step 4), present again.
