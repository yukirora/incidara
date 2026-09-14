# Check Monitoring Problems

Evaluate problems in `monitoring` status — determine if deployed patches are working.

## When to Use

- At the start of each `analyze-rma-cases` weekly run
- Manual: "check monitoring problems", "evaluate monitoring"

## Workflow

### 1. Fetch monitoring problems

```
get_problems(status="monitoring")
```

If none → skip this sub-skill, proceed to main analyze pipeline.

### 2. For each problem, check if enough data exists

Read `monitor_expectation` — it specifies:
- **What metric to measure** (e.g., "NvidiaSmiDoubleEccError NFF rate")
- **What behavior to watch for** (e.g., "agent checks row-remapper before classifying")
- **When enough data exists** (e.g., "after ≥5 new cases or 30 days")

Get the problem's monitoring start time from `get_problem_history(problem_id)`. Since `analysis_problems` is append-only, find the row where `status` first became `monitoring` — that row's `updated_at` is when monitoring started. Do NOT use the latest row's `updated_at`, because subsequent updates (diagnosis tweaks, case_ids added) would reset the timestamp and give a wrong monitoring window.

Count new cases since that monitoring start timestamp:

```
query_similar_cases(classification="<fault_type>", limit=100)
```

Filter results where `collected_at > monitoring_start_time`. Count them.

Also check historical frequency: how many total cases exist for this fault type, and what's the earliest `collected_at`? This gives you the baseline arrival rate (e.g., "12 cases over 90 days = ~4 cases/month").

Compare against `monitor_expectation` — it specifies the number of new cases needed, **derived from historical frequency** (set when the problem entered monitoring). For example:
- Historical: 8 cases/month → monitor_expectation: "6 cases (≈1 week)"
- Historical: 2 cases/month → monitor_expectation: "3 cases (≈2 weeks)"
- Historical: 1 case every 2 months → monitor_expectation: "2 cases (≈4 weeks)"

**Maximum monitoring period: 1 month.** After 1 month, evaluate with whatever data exists — even 1 new case is better than waiting indefinitely. Rare fault types with zero new cases after 1 month should be marked unresolved with "insufficient data, may need manual re-check."

| Condition | Verdict |
|---|---|
| New cases ≥ `monitor_expectation` count | Enough data → evaluate patch |
| Not enough new cases, but < 1 month since monitoring started | ⏳ Still monitoring |
| Any amount of new cases after 1 month | Evaluate with available data |
| Zero new cases after 1 month | ⚠️ Unresolved — "insufficient data, needs manual re-check" |

### 3. If enough data, evaluate the patch

Compare accuracy before and after the patch:

**Before** (from the problem's diagnosis): the accuracy that triggered the problem. E.g., "NodeCrash NFF rate was 68% (15/22 NFF)".

**After** (from current data): call `get_rule_stats()` to get current accuracy for that fault_type. Count how many of the new cases are wrong (NFF + MISCLASSIFIED) vs correct (REPAIR_CONFIRMED + MAINTENANCE_FIX + CONFIG_TASK).

**Decision:**

| Condition | Verdict | Action |
|---|---|---|
| Accuracy improved and meets `monitor_expectation` target | ✅ Resolved | `update_problem_tool(problem_id, status="resolved")` |
| Accuracy did not improve, or got worse | ❌ Unresolved | `update_problem_tool(problem_id, status="unresolved")` — the fix didn't work, needs re-diagnosis |
| Mixed — some improvement but not enough | ⚠️ Unresolved | `update_problem_tool(problem_id, status="unresolved")` — partial fix, needs iteration |
| Not enough data yet | ⏳ Still monitoring | No update |

### 4. For resolved problems

Record what the evidence shows:

```
update_problem_tool(
  problem_id=<id>,
  status="resolved",
  monitor_expectation="RESOLVED: <what improved>. Before: <before stats>. After: <after stats>."
)
```

### 5. For unresolved problems

Record what happened and propose next step:

```
update_problem_tool(
  problem_id=<id>,
  status="unresolved",
  monitor_expectation="UNRESOLVED: <what didn't improve>. Before: <before stats>. After: <after stats>. Needs re-diagnosis."
)
```

Unresolved problems should be picked up by the next `analyze-rma-cases` run as new problems (the main pipeline matches `unresolved` → creates new problem).

### 6. Summary

Show a table of all monitoring problems evaluated:

| Problem # | Fault Type | New Cases | Enough Data? | Before Accuracy | After Accuracy | Verdict |
|---|---|---|---|---|---|---|
| #19 | NvidiaSmiDoubleEccError | 3 | No | 87.5% | 100% (3/3) | Still monitoring |
| #6 | NVLinkFailure | 12 | Yes | 45% | 80% | ✅ Resolved |
| #8 | NodeCrash | 8 | Yes | 32% | 38% | ❌ Unresolved |

## Key Principles

- **Be conservative** — "some improvement" is not "resolved". The fix must actually meet the target stated in `monitor_expectation`.
- **Compare same metric** — if `monitor_expectation` said "NFF rate should drop from 68% to <40%", check exactly that NFF rate, not overall accuracy.
- **Don't over-claim** — if only 3 new cases exist and all happened to be correct, that's not statistically significant. Wait for more data.
- **Unresolved ≠ failure** — it means the first fix attempt didn't fully work. The next analyze-rma-cases run will create a new problem with fresh diagnosis.
