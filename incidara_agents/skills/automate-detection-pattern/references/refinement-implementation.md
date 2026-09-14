# Refinement Implementation

After diagnosing the root cause (see `refinement-diagnosis.md`), follow this loop to implement the fix.

```
CHANGE one thing → VALIDATE → OBSERVE
     ↑                            |
     └────────────────────────────┘
```

## Step 1: CHANGE — Make ONE Change

**One change at a time.** If you change both the rule code and the collector config, you can't tell which change caused the result.

### Change order

1. **Rule code changes first** — cheaper to iterate, doesn't affect other rules
2. **Collector config changes second** — affects all bound rules, more expensive
3. **Structural changes last** — most disruptive (split/merge, see `structural-implementation.md`)

### Rule code change

```
update_rule_code(rule_id, new_code)
```

### Collector config change

**⛔ Collector changes require extra caution** — they affect ALL bound rules.

Before `update_collector`:
1. Check what rules bind to this collector: `list_rules(binds_to=collector_name)`
2. For each bound rule: `test_rule_once(rule_id, sample=5)` — verify it still works after the change
3. Only then: `update_collector(name, ...)`
4. After update: `test_rule_once` on ALL bound rules with full dataset

### ⛔ Rate limit yourself

| Action | Max frequency |
|---|---|
| `update_rule_code` | 1 per validation cycle |
| `update_collector` | 1 per validation cycle |
| Stage promotion | 1 per review cycle |
| Stage demotion | Immediate if harm proven |

**3+ changes without new verdict data = thrashing. STOP.** Delegate for more verdicts instead.

### Plan before executing

Write down before making any change:
- **What I'm changing**: e.g., "Add cascade exit code filter to analyze()"
- **Why**: e.g., "9/10 rejected findings have exit_code=-220; 0/3 confirmed have it"
- **What I'm protecting**: e.g., "Confirmed findings have exit_code=1 — this filter won't exclude them"
- **Expected result**: e.g., "Rejected findings should stop appearing"

**⛔ If you can't fill in all 4 fields, you're not ready to make the change.**

## Step 2: VALIDATE — Test After Change

### Quick smoke test

```
test_rule_once(rule_id, sample=5)
```
- No errors? Findings have `raw_output`? Expected finding count?
- If this fails → fix the change, don't make a new one.

### Full dataset test

```
test_rule_once(rule_id)  # no sample limit
```
- No errors on full target set?
- Finding count reasonable? (not 10x more or 10x fewer than before)

### Replay suite (for rule code changes)

```
create_rule_replay_cases_from_feedback(rule_id)
list_rule_replay_cases(rule_id)    # must have both positive + negative
run_rule_replay_suite(rule_id)     # must return passed=true
```

If zero replay cases → `list_rule_feedback_examples(rule_id)` to diagnose. If missing → route back to `analyze-rma-cases`/`case-diagnosis`.

### Persist and verify

```
test_rule_once(rule_id, dry_run=False)
list_findings(rule_id, limit=5)  # verify findings appeared
```

### Collector change validation (extra steps)

When you changed the collector:
1. `test_rule_once` on ALL bound rules (not just the one you're refining)
2. `run_collector_once(collector_name, sample=5)` — verify targets still collected
3. If a bound rule breaks → either fix the rule code or `rollback_collector(name)`

## Step 3: OBSERVE — Wait Before Next Change

After validating, **do not immediately make another change.** You need real-world signal.

| Situation | How long to observe | What to watch |
|---|---|---|
| Rule at `log_only` | At least 2 cron cycles | New finding count, any new verdicts |
| Rule at `create_task`+ | At least 1 cron cycle + human verdict | Verdict ratio on new findings |
| After collector change | 2 cron cycles minimum | All bound rules still producing findings |
| After stage promotion | 1 day | No spike in findings, no false cordons |

### What to check after observing

1. `get_rule_accuracy(rule_id)` — did accuracy improve?
2. `list_findings(rule_id, since_days=1)` — what do new findings look like?
3. `get_rule_rejection_reasons(rule_id)` — any new rejection reasons?

**If accuracy improved** → the change worked. Consider if another refinement cycle is needed.
**If accuracy didn't change** → the change didn't address the root cause. Re-diagnose.
**If accuracy got worse** → rollback: `rollback_rule(rule_id)` or `rollback_collector(name)`.

### When to stop refining

| Condition | Action |
|---|---|
| Accuracy ≥ 80% | Stop — rule is working well |
| Accuracy 50-80% and stable across 3+ cycles | Stop — diminishing returns, delegate for more verdicts |
| Accuracy < 50% after 3 refinement cycles | Stop — signal may not be predictive. Demote or change approach |
| Confirmed + rejected have identical raw data | Stop — signal isn't discriminative. No code change will fix this |

## Common Refinement Scenarios

### Scenario 1: Cascade contamination (most common for job-based rules)

**Symptom:** Many findings, most rejected, rejected findings show all nodes in a job failing.

**Fix in `analyze()`** (NOT the collector):
```python
# Identify root cause: the node whose task failed FIRST
genuine_failures = [t for t in tasks if t.exit_code not in {0, -210, -220}]
genuine_failures.sort(key=lambda t: t.completed_time)
if genuine_failures:
    root_cause = genuine_failures[0]  # first to fail = root cause
```

### Scenario 2: Transient vs persistent signal

**Fix in `analyze()`** using lifecycle thresholds. The rule emits observations; the engine persists counters and controls when findings open/close:
```python
lifecycle = {
    "kind": "condition",
    "open_after_consecutive": 2,
    "close_after_healthy": 2,
    "unknown_keeps_active": True,
}

observations.append(Observation(
    signal_key="stable_signal_name",
    target_id=hostname,
    status="bad" if signal_detected else "healthy",
    severity="critical",
    action="cordon_node",
    evidence=evidence if signal_detected else {"issue_clear": True},
    lifecycle=lifecycle,
))
```

Only use `RuleResult.state` for private rule memory such as previous counter values or seen event IDs. Do not store active finding IDs or consecutive open/close counters in rule state.

### Scenario 3: Wrong data source for the signal

**Evidence needed:** Source A identical for confirmed and rejected; Source B differs.
**Fix:** Add Source B to collector (one of the few valid collector changes for refinement).

### Scenario 4: Accuracy deadlock

**Fix:** Split rule into hard (deterministic) + soft (uncertain + extra filter).
See `structural-implementation.md` for the procedure.

### Scenario 5: Signal genuinely isn't predictive

**Fix:** Stop refining. Options: demote to `log_only`, add corroboration requirement, or accept the FP rate.

## Anti-Patterns

### 1. Thrashing (making many rapid changes)
3+ changes in one session without new verdict data = thrashing. Complete the full CHANGE → VALIDATE → OBSERVE cycle before making another change.

### 2. Narrowing the collector to fix false positives
Collector's job: collect data from all potentially relevant targets. Rule's job: decide which are real failures. Never narrow `target_filter` to exclude false-positive targets.

### 3. Adding sources without evidence
Each source addition changes payload shape and adds API load. Only add when evidence shows the source discriminates confirmed from rejected.

### 4. Promoting stage while changing code
Code changes must be validated at the CURRENT stage before any promotion.

### 5. Ignoring rejection reasons
`get_rule_rejection_reasons(rule_id)` returns human explanations. These are the most direct feedback you can get.

### 6. Treating NFF as "downstream issue"
High NFF rate means the rule fires on a signal that isn't specific enough to hardware failure. Fix the signal, don't blame repair.

## Self-Review Checklist

Before finalizing any refinement, verify:

- **Is the stage appropriate?** — new rules ALWAYS start at `log_only`. Never promote to `submit_alert` or `auto_cordon` without proven accuracy.
- **Did I change only one thing?** — rule code OR collector config, never both in one cycle.
- **Did I validate on ALL bound rules?** — if you changed the collector, every rule on that collector must still pass `test_rule_once`.
- **Did I observe before iterating?** — at least 1-2 cron cycles between changes. Thrashing = no learning.
