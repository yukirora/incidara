# Design: Detection Feedback Loop — RMA → Rule & Agent Optimization

## Problem

When patrol-cron rules produce findings that lead to repairs, some repairs come back as NFF (No Fault Found) or MISCLASSIFIED from the vendor. Today, this signal dies in `case_memory` — it never feeds back to `patrol_findings`, rule accuracy, or detection agent skills. Rules keep firing false positives, inspectors keep wrongly confirming, and automation keeps missing the problem.

## Current State

```
patrol-cron rule fires → finding (verdict=NULL)
    → inspect-infra-issue → verdict="confirmed"
        → execute_node_action(cordon/drain)
            → repair → RMA → vendor_verdict="NO_FAULT_FOUND"
                → case_memory row written
                    → ❌ signal stops here
```

**Broken links:**
- `patrol_findings.verdict` stays "confirmed" even when RMA says NFF
- `get_rule_accuracy()` counts NFF findings as "confirmed" — accuracy is inflated
- `automate-detection-pattern` never sees the NFF signal — can't refine the rule
- `inspect-infra-issue` skill never gets corrected — keeps making same judgment mistakes

## Target State

Two feedback loops closing simultaneously:

### Loop 1: Rule Optimization

Fix the **rule code** — it detects noise that should be filtered, or mislabels real events.

```
RMA (NFF/MISCLASSIFIED) → reconcile_finding() → records RMA outcome
    → case-diagnosis attributes root cause
        → if attribution=detection and confidence is sufficient:
            → reconciliation_dirty = TRUE on rule_reconciliation_state
                → automate-detection-pattern checks dirty rules
                    → get_rule_bad_feedback_rate() shows detection-attributed bad reconciliation count
                        → delegate refinement with autonomous repair depth
                            → replay suite → test_rule_once → update_rule_code → fixed
```

### Loop 2: Agent Optimization

Fix the **agent skills** — they misjudged or failed to act.

```
RMA (NFF/MISCLASSIFIED) → case-diagnosis mines detection pipeline transcripts
    → root cause identified:
        ├─ inspect-infra-issue wrongly confirmed → skill patch
        ├─ automate-detection-pattern failed to catch bad rule → skill patch
        └─ triage-nodes wrongly delegated → skill patch
```

---

## Schema Changes

### `patrol_findings` — add repair outcome

```sql
ALTER TABLE patrol_findings ADD COLUMN IF NOT EXISTS
    repair_outcome TEXT CHECK (repair_outcome IS NULL OR repair_outcome IN
        ('REPAIR_CONFIRMED', 'NO_FAULT_FOUND', 'MISCLASSIFIED',
         'MAINTENANCE_FIX', 'CONFIG_TASK'));

ALTER TABLE patrol_findings ADD COLUMN IF NOT EXISTS
    repair_outcome_at TIMESTAMPTZ NULL;

ALTER TABLE patrol_findings ADD COLUMN IF NOT EXISTS
    collector_snapshot_id TEXT NULL;

ALTER TABLE patrol_findings ADD COLUMN IF NOT EXISTS
    raw_evidence_hash TEXT NULL;
```

| Column | Type | Purpose |
|--------|------|---------|
| `repair_outcome` | TEXT NULL | Vendor verdict from RMA |
| `repair_outcome_at` | TIMESTAMPTZ NULL | When reconciliation happened |
| `collector_snapshot_id` | TEXT NULL | Pointer to frozen collector input used to create the finding |
| `raw_evidence_hash` | TEXT NULL | Hash of raw evidence so replay can detect drift or missing snapshots |

`collector_snapshot_id` and `raw_evidence_hash` are populated atomically when the finding is created by the rule engine. The `collector_snapshot_id` links to the `rule_replay_cases.frozen_input` used as a replay fixture. The `raw_evidence_hash` is a SHA-256 hash of the raw collector output in the evidence field, allowing replay tools to detect if a snapshot has drifted from its original evidence.

`patrol_findings` stores only lightweight replay pointers and hashes. The frozen replay payload belongs in `rule_replay_cases`, not in the operational finding row.

### `patrol_findings.verdict` — add `rejected_nff`

Three verdicts:

| Verdict | Meaning |
|---------|---------|
| `confirmed` | Inspector confirmed, not yet reconciled with RMA |
| `rejected` | Inspector rejected during investigation (no RMA happened) |
| `rejected_nff` | Inspector confirmed, but RMA returned NFF |

This lets accuracy reporting distinguish:
- `confirmed` → still believed correct
- `rejected` → caught at inspection time
- `rejected_nff` → missed at inspection, caught by RMA (stronger signal — both rule AND inspector missed)
- `MISCLASSIFIED` is tracked in `repair_outcome`, but does not change `verdict`: something was wrong, but the rule/agent labeled or routed it incorrectly.

### `rule_reconciliation_state` — rule-level reconciliation tracking

`rule_state` is per `(rule_id, target_id)`, so it is the wrong place for rule-level feedback-loop state. Reconciliation tracking gets its own one-row-per-rule table.

```sql
CREATE TABLE IF NOT EXISTS rule_reconciliation_state (
    rule_id TEXT PRIMARY KEY REFERENCES patrol_rules(rule_id),
    reconciliation_dirty BOOLEAN DEFAULT FALSE,
    nff_baseline_at TIMESTAMPTZ NULL,
    reconcile_attempts INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

This table tracks only cases attributed to detection-rule problems. Triage, inspect, repair, automation, vendor-uncertain, and unknown cases are tracked in the feedback DB for skill optimization, observation, or attention, but they do not drive rule demotion/refinement.

| Column | Type | Purpose |
|--------|------|---------|
| `reconciliation_dirty` | BOOLEAN DEFAULT FALSE | Event flag: new bad reconciliation happened, automation must re-check |
| `nff_baseline_at` | TIMESTAMPTZ NULL | When rule code was last updated for NFF/MISCLASSIFIED reasons. Bad findings before this are from old code. |
| `reconcile_attempts` | INT DEFAULT 0 | Autonomous repair depth for this rule since the last verified clean window |

**Who sets/clears what:**

| Who | `reconciliation_dirty` | `nff_baseline_at` | `reconcile_attempts` |
|-----|----------------------|-------------------|----------------------|
| `case-diagnosis` with detection attribution | Sets TRUE | Never | Never |
| `update_rule_code()` after replay-gated NFF/MISCLASSIFIED fix | Clears FALSE | Sets NOW() | Increments |
| Clean verification window | Clears FALSE | Sets NOW() | Resets to 0 |
| Attention resolution for unsafe ambiguity | Clears FALSE when appropriate | Sets NOW() when appropriate | Resets only if the rule is rewritten or retired |

### `rma_finding_reconciliations` — idempotent case/finding mapping

Reconciliation needs a durable mapping from vendor outcome cases to patrol findings. This prevents repeated reconciliation scans from relying on a short time window.

This table lives in the same DB as `case_memory` (agent-feedback DB), because `case_id` is a `case_memory` identifier. `finding_id` is an external patrol-cron finding identifier, so it is not a foreign key.

It stores all matched RMA outcomes, not only NFF/MISCLASSIFIED. Good outcomes provide the denominator for rule feedback rates. Attribution fields are required only for bad outcomes that may need optimization.

```sql
CREATE TABLE IF NOT EXISTS rma_finding_reconciliations (
    case_id INT NOT NULL,
    finding_id INT NOT NULL,
    rule_id TEXT NOT NULL,
    repair_outcome TEXT NOT NULL,
    attribution TEXT CHECK (attribution IS NULL OR attribution IN
        ('detection', 'triage', 'inspect', 'repair', 'automation', 'vendor_uncertain', 'unknown')),
    attribution_confidence TEXT CHECK (attribution_confidence IS NULL OR attribution_confidence IN
        ('high', 'medium', 'low')),
    fix_route TEXT CHECK (fix_route IS NULL OR fix_route IN
        ('rule_code', 'triage_skill', 'inspect_skill', 'repair_skill', 'automation_skill', 'attention', 'observe')),
    reconciled_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (case_id, finding_id)
);
```

`case_id` refers to the `case_memory.id` row created by the feedback agent.

### `rule_replay_cases` — frozen regression cases for autonomous fixes

Replay cases are immutable fixtures created from reconciled RMA feedback and known-good counterexamples. They let automation prove that a rule update fixes the exact bad case without breaking real positives.

This table lives with the patrol-cron rule tooling because it is consumed by rule evaluation. `case_id` is still an agent-feedback identifier and is not a foreign key.

```sql
CREATE TABLE IF NOT EXISTS rule_replay_cases (
    replay_case_id BIGSERIAL PRIMARY KEY,
    case_id INT NULL,
    finding_id INT NULL,
    rule_id TEXT NOT NULL REFERENCES patrol_rules(rule_id),
    source TEXT NOT NULL CHECK (source IN
        ('rma_bad_feedback', 'confirmed_counterexample', 'manual_counterexample')),
    repair_outcome TEXT NULL CHECK (repair_outcome IS NULL OR repair_outcome IN
        ('REPAIR_CONFIRMED', 'NO_FAULT_FOUND', 'MISCLASSIFIED',
         'MAINTENANCE_FIX', 'CONFIG_TASK')),
    attribution TEXT NULL CHECK (attribution IS NULL OR attribution IN
        ('detection', 'triage', 'inspect', 'repair', 'automation', 'vendor_uncertain', 'unknown')),
    rule_version_at_detection TEXT NULL,
    collector_snapshot_id TEXT NULL,
    raw_evidence_hash TEXT NULL,
    frozen_input JSONB NOT NULL,
    expected_behavior JSONB NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

`expected_behavior` must be explicit enough for automation to assert the result:

```json
{
  "should_fire": false,
  "max_stage": "log_only",
  "expected_action": null,
  "expected_severity": null,
  "reason": "NFF case: transient Xid while job stayed healthy."
}
```

For a positive counterexample:

```json
{
  "should_fire": true,
  "expected_action": "drain",
  "expected_severity": "critical",
  "reason": "Confirmed GPU failure must still trigger."
}
```

Replay cases are created by `case-diagnosis` after attribution:
- Detection-attributed NFF → failed-case replay where the updated rule should not fire, or should only fire in safe/log-only mode.
- Detection-attributed MISCLASSIFIED → failed-case replay where the rule may fire, but category/action/severity must change.
- Confirmed examples for the same rule → positive counterexamples that must still fire.
- Triage/inspect/repair/automation-attributed cases → skill replay inputs for the owning optimization route, not rule replay gates.

### New MCP Tools: rule replay

**Server:** patrol-cron

```python
@mcp.tool()
def create_rule_replay_case(
    rule_id: str,
    finding_id: int | None,
    case_id: int | None,
    source: str,
    frozen_input: dict,
    expected_behavior: dict,
) -> int:
    """Create an immutable replay fixture for a rule."""

@mcp.tool()
def list_rule_replay_cases(rule_id: str, include_counterexamples: bool = True) -> list[dict]:
    """Return failed-case and counterexample replay fixtures for a rule."""

@mcp.tool()
def replay_rule_case(rule_id: str, replay_case_id: int, analyze_code: str | None = None) -> dict:
    """Run the current or candidate rule code against one frozen replay fixture."""

@mcp.tool()
def run_rule_replay_suite(rule_id: str, analyze_code: str | None = None) -> dict:
    """Run all replay fixtures for a rule and return pass/fail details."""
```

`test_rule_once()` remains the live sanity check against current collector data. `run_rule_replay_suite()` is the regression gate against frozen bad and known-good cases.

**Why event-driven dirty flag, not time-window query:**

A time-window query like `WHERE repair_outcome_at > NOW() - INTERVAL '7 days'` can miss cases — if automate-detection-pattern doesn't run within the window, or if RMA returns late, the signal is lost. The dirty flag is a contract:

- `case-diagnosis` sets it TRUE after attributing the bad outcome to detection — **rule issue happened**
- Flag stays TRUE forever until acknowledged — **no missed cases**
- `update_rule_code()` clears it only after replay-gated fix finalization — **acknowledged**
- Safe/shadow containment can reduce blast radius before replay passes, but keeps the rule dirty so automation continues self-repair.
- No arbitrary cutoffs, no false negatives

---

## New MCP Tool: `reconcile_finding`

**Server:** patrol-cron

```python
@mcp.tool()
def reconcile_finding(finding_id: int, repair_outcome: str) -> str:
    """Reconcile a patrol finding with RMA repair outcome.

    Called by feedback agent after RMA vendor verdict arrives.
    Flips verdict for NFF cases and records repair_outcome.
    Does not decide whether the rule was at fault.

    Args:
        finding_id: patrol_findings.finding_id
        repair_outcome: REPAIR_CONFIRMED | NO_FAULT_FOUND | MISCLASSIFIED |
                        MAINTENANCE_FIX | CONFIG_TASK
    """
```

**Behavior:**

| repair_outcome | verdict change | resolved | rule dirty |
|---------------|---------------|----------|------------|
| NO_FAULT_FOUND | confirmed → rejected_nff | TRUE | not here |
| MISCLASSIFIED | stays confirmed, repair_outcome set | FALSE | not here |
| CONFIG_TASK | stays confirmed, repair_outcome set | TRUE | not set |
| REPAIR_CONFIRMED | stays confirmed, repair_outcome set | TRUE | not set |
| MAINTENANCE_FIX | stays confirmed, repair_outcome set | TRUE | not set |

- `reconcile_finding()` only records the RMA outcome on the finding.
- `case-diagnosis` decides whether the bad outcome was caused by detection, triage, inspect, repair, automation, vendor uncertainty, or unknown cause.
- `reconciliation_dirty` is set only for NFF/MISCLASSIFIED cases attributed to detection with sufficient confidence. REPAIR_CONFIRMED, MAINTENANCE_FIX, and CONFIG_TASK confirm that the detection found a real actionable issue — no need to re-check rule correctness.
- **Idempotent:** skip if `repair_outcome` already set on this finding.

---

## New MCP Tool: `list_dirty_reconciliation_rules`

**Server:** patrol-cron

```python
@mcp.tool()
def list_dirty_reconciliation_rules(limit: int = 50) -> str:
    """Return rules with detection-attributed RMA feedback requiring repair.

    Called by automate-detection-pattern Step 2 to find rules where
    reconciliation_dirty = TRUE in rule_reconciliation_state.

    Args:
        limit: max number of dirty rules to return
    """
```

Sorted by `rule_reconciliation_state.updated_at ASC` so oldest unresolved bad feedback gets priority.

---

## New: `get_rule_bad_feedback_rate()`

Separate from `get_rule_accuracy()` because RMA feedback uses a **different dataset** with a **baseline, not a window**. It counts all reconciled RMA outcomes for the rule as the denominator, and only counts NFF/MISCLASSIFIED cases attributed to detection as bad feedback.

### Why `get_rule_accuracy()` (updated_at window) doesn't work for NFF

```
Day 1:  rule code updated → updated_at = Day 1
Day 2:  finding created → verdict="confirmed"
Day 10: rule code tweaked → updated_at = Day 10  ← window resets!
Day 30: RMA returns NFF → reconcile_finding() flips to rejected_nff
Day 31: get_rule_accuracy(rule_id) only counts findings AFTER updated_at (Day 10)
        → the Day 2 finding is OUTSIDE the window → NFF signal lost
```

RMA verdicts always arrive weeks/months after the finding, always after any rule code change. **The NFF signal always falls outside the updated_at window.**

### `nff_baseline_at` solves this

`nff_baseline_at` is set by `update_rule_code()` — specifically when rule code is updated to fix NFF/MISCLASSIFIED. It's NOT set by every small code change (only `updated_at` is).
For rules that have never had an NFF/MISCLASSIFIED fix, NULL means "count from the beginning."

```sql
SELECT
    COUNT(*) AS rma_total,
    COUNT(*) FILTER (WHERE rfr.repair_outcome = 'REPAIR_CONFIRMED') AS repair_confirmed,
    COUNT(*) FILTER (WHERE rfr.repair_outcome = 'CONFIG_TASK') AS config_task,
    COUNT(*) FILTER (
        WHERE rfr.repair_outcome = 'NO_FAULT_FOUND'
          AND rfr.attribution = 'detection'
          AND rfr.attribution_confidence IN ('high', 'medium')
    ) AS detection_nff,
    COUNT(*) FILTER (
        WHERE rfr.repair_outcome = 'MISCLASSIFIED'
          AND rfr.attribution = 'detection'
          AND rfr.attribution_confidence IN ('high', 'medium')
    ) AS detection_misclassified
FROM patrol_findings pf
LEFT JOIN rule_reconciliation_state rrs ON rrs.rule_id = pf.rule_id
JOIN rma_finding_reconciliations rfr ON rfr.finding_id = pf.finding_id
WHERE pf.rule_id = %s
  AND rfr.repair_outcome IS NOT NULL
  AND pf.detected_at > COALESCE(rrs.nff_baseline_at, '-infinity'::timestamptz)
```

Only counts RMA-verified outcomes **after the last NFF/MISCLASSIFIED-driven code fix**. Old bad findings from previous code are excluded. Triage/inspect/repair/automation/vendor-uncertain failures do not lower rule accuracy because they are not detection-rule failures.

Returns:
```json
{
    "rule_id": "b300_gpu_xid_v1",
    "rma_total": 5,
    "repair_confirmed": 3,
    "config_task": 0,
    "detection_nff": 1,
    "detection_misclassified": 1,
    "bad_count": 2,
    "bad_feedback_rate": 0.4
}
```

Where:
- `bad_count = detection_nff + detection_misclassified`
- `bad_feedback_rate = bad_count / rma_total`
- `repair_confirmed` and `config_task` are denominator/context fields only. They do not count against the rule.

---

## Autonomous Repair Ladder

**Repair depth is derived from `rule_reconciliation_state.reconcile_attempts`, not from `bad_count`.** `bad_count` tells us whether the current code still has detection-attributed bad feedback after `nff_baseline_at`; `reconcile_attempts` tells us how much autonomous repair has already been tried.

The default goal is self-improvement. Human attention is only for unsafe ambiguity, missing evidence, policy decisions, or repeated auto-fixes that cannot pass verification.

| `reconcile_attempts` before action | Meaning | NFF action | MISCLASSIFIED action |
|-----------------------------------|---------|-----------|---------------------|
| 0 | First rule-fix attempt | Delegate refinement | Delegate refinement |
| 1 | Second failed attempt | Demote stage → `log_only` + delegate refinement | Demote action/severity + delegate refinement |
| 2 | Deep autonomous repair | Keep in safe/shadow mode, run case-diagnosis replay, request alternate rule proposal, expand tests | Keep demoted action/severity, run case-diagnosis replay, request alternate categorization/routing proposal, expand tests |
| ≥3 | Contained autonomous loop | Keep rule contained, continue periodic self-diagnosis and generate an attention item only if evidence is unsafe or blocked | Keep rule contained, continue periodic self-diagnosis and generate an attention item only if evidence is unsafe or blocked |

**Why the actions differ on the second attempt:**
- **NFF second attempt**: the finding was completely wrong, so stop real actions first (unnecessary cordon/drain/RMA)
- **MISCLASSIFIED second attempt**: something is wrong, but the categorization is wrong, so stop the aggressive action (drain → alert, cordon → notify)

**By the third attempt, the system does not stop at human escalation.** It contains blast radius, gathers stronger evidence, asks for alternative fixes, and only creates attention if it cannot safely proceed.

### Why `bad_count` alone is insufficient

```
Day 1:  finding confirmed, RMA → NFF
        → dirty=TRUE

Day 2:  automation: dirty=TRUE, get_rule_bad_feedback_rate() → bad_count=1
        → first attempt → delegate refinement
        → dirty stays TRUE

Day 5:  update_rule_code() → dirty=FALSE, nff_baseline_at=NOW()

Day 15: new finding confirmed, RMA → NFF (detected_at > nff_baseline_at)
        → dirty=TRUE

Day 16: automation: dirty=TRUE, get_rule_bad_feedback_rate() → bad_count=1 (after baseline)
        → first attempt again → delegate refinement
        → (baseline reset, so bad_count restarts from 1)

Day 20: update_rule_code() → dirty=FALSE, nff_baseline_at=NOW()

Day 30: new finding confirmed, RMA → NFF (detected_at > nff_baseline_at)
        → dirty=TRUE

Day 31: automation: dirty=TRUE, get_rule_bad_feedback_rate() → bad_count=1
        → first attempt → delegate refinement (again)
        → but this is the 3rd time we've tried to fix this rule...

--- Wait. The baseline resets the count each time. How do we know it's the 3rd attempt?
```

### Problem: baseline reset hides repeat failures

`nff_baseline_at` resets after every code update, so `bad_count` restarts from 0. We lose the signal that this rule has been a persistent problem.

### Fix: Also track autonomous repair depth

| Column | Who increments | Who resets |
|--------|---------------|-----------|
| `rule_reconciliation_state.reconcile_attempts` | `update_rule_code()` when called for NFF/MISCLASSIFIED | Clean verification window after no detection-attributed bad outcomes |

**Updated escalation logic:**

```python
feedback = get_rule_bad_feedback_rate(rule_id)
attempts = rule_reconciliation_state.reconcile_attempts

if feedback.bad_count == 0:
    # Current code is fine, nothing to do
    pass

elif attempts == 0:
    # First time: delegate refinement
    delegate_refinement(rule_id, feedback)

elif attempts == 1:
    # Second attempt: demote + delegate refinement
    if feedback.detection_nff > 0:
        demote_stage(rule_id, to="log_only")
    else:
        demote_action(rule_id)
    delegate_refinement(rule_id, feedback)

elif attempts == 2:
    # Third attempt: contain blast radius and run deeper autonomous repair
    keep_rule_safe_or_shadowed(rule_id)
    run_case_diagnosis_replay(rule_id, feedback)
    request_alternate_rule_proposal(rule_id, feedback)
    expand_replay_tests(rule_id, feedback)

elif attempts >= 3:
    # Later attempts: stay contained, keep improving if evidence is safe
    keep_rule_safe_or_shadowed(rule_id)
    if evidence_is_safe_and_actionable(feedback):
        request_alternate_rule_proposal(rule_id, feedback)
        expand_replay_tests(rule_id, feedback)
    else:
        create_attention_item(rule_id, reason="unsafe_or_blocked_autonomous_repair")
```

**Full lifecycle with attempts:**

```
Day 1:  finding → NFF → dirty=TRUE
Day 2:  automation: bad_count=1, attempts=0 → delegate refinement
Day 5:  update_rule_code() → dirty=FALSE, nff_baseline_at=NOW(), attempts=1

Day 15: finding → NFF → dirty=TRUE
Day 16: automation: bad_count=1, attempts=1 → demote to log_only + delegate refinement
Day 20: update_rule_code() → dirty=FALSE, nff_baseline_at=NOW(), attempts=2

Day 30: finding → NFF → dirty=TRUE
Day 31: automation: bad_count=1, attempts=2 → contain + deep replay + alternate rule proposal
Day 35: update_rule_code() → dirty=FALSE, nff_baseline_at=NOW(), attempts=3

Day 60: clean verification window with no detection-attributed bad outcomes
        → attempts reset to 0
```

---

## Phase 1: `analyze-rma-cases` — Reconcile Findings

### New sub-skill: `reconcile-findings`

**Trigger:** runs as part of `analyze-rma-cases` daily schedule (after case_memory is updated with new RMA verdicts)

**Steps:**

1. Query unreconciled `case_memory` rows for completed RMA outcomes:
   ```sql
   SELECT cm.id AS case_id,
          cm.hostname,
          cm.vendor_verdict,
          cm.rma_ticket_id,
          cm.rma_completed_at,
          cm.fault_type,
          cm.our_classification
   FROM case_memory cm
   WHERE cm.vendor_verdict IN (
         'REPAIR_CONFIRMED', 'NO_FAULT_FOUND', 'MISCLASSIFIED',
         'MAINTENANCE_FIX', 'CONFIG_TASK'
     )
     AND cm.rma_completed_at IS NOT NULL
     AND NOT EXISTS (
         SELECT 1
         FROM rma_finding_reconciliations rfr
         WHERE rfr.case_id = cm.id
     )
   ORDER BY cm.rma_completed_at ASC
   ```

   This query has no arbitrary time window. A missed daily run does not lose the reconciliation signal.

2. For each case, find candidate patrol findings for the same hostname:
   ```sql
   SELECT finding_id, rule_id, detected_at, evidence, action, action_params
   FROM patrol_findings
   WHERE target_id = %(hostname)s
     AND verdict = 'confirmed'
     AND repair_outcome IS NULL
     AND detected_at <= %(rma_completed_at)s
   ORDER BY detected_at DESC
   ```

3. Match candidates conservatively:
   - Prefer findings whose `task_id` or session evidence links to the repair/RMA path.
   - Otherwise require fault/action compatibility between `case_memory.fault_type` or `our_classification` and the finding `rule_id`, `action_params`, or evidence.
   - If multiple unrelated findings remain, do not reconcile automatically. Report the ambiguous case to `case-diagnosis`.

4. For each matched finding:
   - `reconcile_finding(finding_id, repair_outcome=vendor_verdict)`
   - Insert `(case_id, finding_id, rule_id, repair_outcome)` into `rma_finding_reconciliations`

5. Skip cases where no safe match exists. Bad outcomes still feed `case-diagnosis` as "unmatched RMA feedback" so the transcript miner can explain why no finding link exists. Good outcomes can be recorded as unmatched denominator gaps for weekly autonomous review.
	
6. Report reconciliation summary:
   ```
   Reconciled 3 findings from 2 hostnames:
   - b300-000019: finding #42 → NFF (rule: b300_gpu_xid_v1)
   - b300-000019: finding #43 → NFF (rule: b300_gpu_xid_v1)
   - h200-000795: finding #67 → MISCLASSIFIED (rule: h200_nvlink_health_v1)
   ```

7. This output feeds into `case-diagnosis` as input. Reconciliation alone does not route rule fixes.

**Tools needed:** `agent-feedback` (case_memory read, `list_unreconciled_rma_outcomes`), `patrol-cron` (list_findings, reconcile_finding)

### New MCP Tool: `list_unreconciled_rma_outcomes`

**Server:** agent-feedback

```python
@mcp.tool()
def list_unreconciled_rma_outcomes() -> str:
    """Return completed RMA outcomes from case_memory that have no matching
    reconciliation row in rma_finding_reconciliations.

    Called by reconcile-findings sub-skill to find new RMA verdicts to reconcile.
    No time window — a missed run does not lose the signal.
    """
```

Implements the LEFT JOIN anti-join from Step 1 above. Returns all unreconciled outcomes across all verdict types (good and bad).

---

## Phase 2: Attribution Gate (`case-diagnosis`)

`case-diagnosis` is the gate between bad RMA outcomes and optimization. NFF/MISCLASSIFIED means the end-to-end pipeline was wrong; it does not prove the detection rule was wrong.

For each reconciled case, `case-diagnosis` reviews:
- original patrol finding evidence
- scanner output/context as evidence, not as an attribution target
- triage transcript and delegation decisions
- inspect-infra-issue transcript and evidence checks
- repair transcript and submitted RMA evidence
- automate-detection-pattern transcript and follow-up decisions
- vendor answer quality and repair details

It outputs:
```json
{
  "case_id": 123,
  "finding_id": 42,
  "rule_id": "b300_gpu_xid_v1",
  "repair_outcome": "NO_FAULT_FOUND",
  "attribution": "detection",
  "attribution_confidence": "medium",
  "fix_route": "rule_code",
  "reason": "Rule fired on transient Xid 79 while job was still healthy."
}
```

Attribution values:

| Attribution | Meaning | Fix route |
|-------------|---------|-----------|
| `detection` | Raw rule evidence was too broad or wrong | `rule_code` |
| `triage` | Rule finding was plausible, but triage routed or delegated wrong | `triage_skill` |
| `inspect` | Triage route was plausible, but inspection confirmed/rejected with bad judgment or incomplete checks | `inspect_skill` |
| `repair` | Detection and triage were plausible, but repair submitted weak/wrong RMA | `repair_skill` |
| `automation` | Case diagnosis or dirty-rule follow-up should have acted but automation missed/escalated wrong | `automation_skill` |
| `vendor_uncertain` | Vendor answer is too weak or contradictory to trust | `attention` or `observe` |
| `unknown` | Evidence cannot safely assign blame | `attention` |

Only `attribution='detection'` with `attribution_confidence IN ('high', 'medium')` sets `rule_reconciliation_state.reconciliation_dirty = TRUE` and contributes to `get_rule_bad_feedback_rate()`.

Low-quality NFF (`vendor_answer_quality IN ('MINIMAL', 'NONE')`) should default to `vendor_uncertain` unless local evidence clearly proves the rule was wrong.

---

## Phase 3: Rule Optimization

### `automate-detection-pattern` — three independent steps

```
Step 1: Handle unjudged findings (existing, unchanged)
    list_findings(verdict="") → delegate to inspector

Step 2: Handle reconciliation-driven accuracy (NEW, event-driven)
    list rules WHERE rule_reconciliation_state.reconciliation_dirty = TRUE
    for each dirty rule:
        feedback = get_rule_bad_feedback_rate(rule_id)   ← uses nff_baseline_at, not updated_at
        attempts = rule_reconciliation_state.reconcile_attempts

        if feedback.bad_count == 0:
            → current code is fine (old dirty flag from pre-baseline)
            → dirty=FALSE

        elif attempts == 0:
            → first attempt: delegate refinement with detection-attributed NFF/MISCLASSIFIED pattern

        elif attempts == 1:
            → second attempt: demote + delegate refinement
            → NFF: demote stage to log_only
            → MISCLASSIFIED: demote action/severity

        elif attempts == 2:
            → third attempt: contain in safe/shadow mode
            → replay case evidence, expand tests, request alternate rule proposal

        elif attempts >= 3:
            → later attempts: stay contained and keep autonomous diagnosis running
            → create attention only when evidence is unsafe or blocked

Step 3: Handle inspector accuracy (existing, unchanged)
    for each rule:
        accuracy = get_rule_accuracy(rule_id)   ← still uses updated_at window
        if accuracy.low:
            → delegate refinement
```

Steps 2 and 3 serve different purposes:
- **Step 2**: RMA ground truth — "this rule's confirmed findings keep turning into NFF/MISCLASSIFIED"
- **Step 3**: Inspector signal — "inspector keeps rejecting this rule's findings immediately"

Both can trigger refinement independently.

### NFF/MISCLASSIFIED pattern analysis in delegation

When automate-detection-pattern delegates refinement via Step 2, it first checks the Chat UI for an existing open task targeting the same `rule_id` with `automate-detection-pattern` skill and `nff_refinement` or `misclassified_refinement` reason. If an active task exists, it does not create another task. `reconciliation_dirty` clears only when the active task finalizes a replay-gated `update_rule_code()` or an attention resolution explicitly clears it; safe/shadow containment keeps the flag TRUE so autonomous repair continues.

When it does delegate, it includes context:

```
Rule: b300_gpu_xid_v1
Attempt: 1
Bad reconciliations: 2 NFF, 0 MISCLASSIFIED (after baseline)
NFF findings: #42 (Xid 79, job running), #43 (Xid 79, job running)
Confirmed findings: #10 (Xid 95, GPU failed), #15 (Xid 44, GPU failed)
Pattern: NFF = Xid 79, confirmed = Xid 95/44
Fix: add Xid 79 to TRANSIENT_XIDS, or add job-running check in analyze()
```

### `update_rule_code()` integration

When rule code is updated to fix NFF/MISCLASSIFIED (triggered by Step 2 delegation), finalization is gated by replay. A candidate rule update can be evaluated without clearing dirty state.

```python
def evaluate_rule_update(rule_id, analyze_code, reason=""):
    if reason in ("nff_refinement", "misclassified_refinement"):
        replay_result = run_rule_replay_suite(rule_id, analyze_code=analyze_code)
        live_result = test_rule_once(rule_id, analyze_code=analyze_code, dry_run=True)

        if not replay_result.passed:
            return {
                "accepted": False,
                "reason": "replay_failed",
                "details": replay_result.failures,
            }

        if not live_result.passed:
            return {
                "accepted": False,
                "reason": "live_test_failed",
                "details": live_result.failures,
            }

    return {"accepted": True}


def update_rule_code(rule_id, analyze_code, reason="", mode="finalize"):
    # ... existing validation and write logic ...

    # Containment is allowed to reduce blast radius even before a full replay fix.
    if mode in ("safe_mode", "shadow_mode"):
        apply_rule_containment(rule_id, mode)
        UPDATE rule_reconciliation_state SET
            reconciliation_dirty = TRUE,
            updated_at = NOW()
        WHERE rule_id = %(rule_id)s
        return

    evaluation = evaluate_rule_update(rule_id, analyze_code, reason)
    if not evaluation["accepted"]:
        return evaluation

    # Only a replay-gated finalized fix acknowledges the dirty state.
    if reason in ("nff_refinement", "misclassified_refinement"):
        UPDATE rule_reconciliation_state SET
            reconciliation_dirty = FALSE,
            nff_baseline_at = NOW(),
            reconcile_attempts = reconcile_attempts + 1,
            updated_at = NOW()
        WHERE rule_id = %(rule_id)s
```

Acceptance requirements for NFF/MISCLASSIFIED rule fixes:
- failed replay cases now meet their expected behavior
- positive counterexamples still meet their expected behavior
- live `test_rule_once(..., dry_run=True)` completes without obvious new noise
- only then can dirty be cleared, baseline reset, and attempts incremented

Safe/shadow containment is different: it can be applied immediately to reduce risk, but it does not reset `nff_baseline_at` or `reconcile_attempts`.

---

## Phase 4: Agent Optimization

### `case-diagnosis` extension

**Current scope:** mine repair/triage agent transcripts → find agent mistakes → propose skill patches

**Extended scope:** also mine detection pipeline transcripts when triggered by NFF/MISCLASSIFIED reconciliation, and assign attribution before routing fixes.

**Input from reconcile-findings:**
```
{
  "rule_id": "b300_gpu_xid_v1",
  "bad_feedback_rate": 0.4,
  "bad_finding_ids": [42, 43, 89],
  "session_ids": ["5363", "5401", "5442"]
}
```

**Mine 3 sets of transcripts:**

#### 3a. scan-cluster transcript

| Question | What to Look For |
|----------|-----------------|
| Did it see the collector failure? | Was the finding created by rule or missed? |
| Did it delegate? | If finding existed but wasn't delegated, why? |
| Was the delegation prompt adequate? | Did inspector get enough context? |

**Typical finding:** scan-cluster delegated correctly — not the bottleneck.

#### 3b. inspect-infra-issue transcript

| Question | What to Look For |
|----------|-----------------|
| Did it check job health? | Xid with job still running → should reject |
| Did it do impact assessment? | "System working as designed" patterns |
| Did it self-review? | Step 4 in skill — blast radius, workload impact |
| Was it a known "no impact" pattern? | UFM restart, transient Xid, idle port CRC |

**Typical finding:** inspector confirmed without checking if the error had real impact.

**Proposed fix examples:**
- "Add Xid 79 to 'check job health before confirming' list in inspect-infra-issue"
- "Step 3: if GPU Xid is transient type, must run nvidia-smi and check job status"
- "Self-review Q1: did this problem have real impact? If job still running → reject"

#### 3c. automate-detection-pattern transcript

| Question | What to Look For |
|----------|-----------------|
| Did it check this rule's accuracy? | Daily run should have caught NFF rate |
| If accuracy was low, why no action? | "Only delegate unjudged findings" blindspot |
| Did it delegate refinement? | If yes, was the delegation specific enough? |
| How long has the rule been bad? | Days between first NFF and any action |

**Typical finding:** automation saw the rule but didn't act because all findings were "judged" — it only checks unjudged findings.

**Proposed fix examples:**
- "After case-diagnosis attributes reconciled RMA feedback to detection, re-examine affected rules even if findings are judged"
- "Add Step 2: query rule_reconciliation_state WHERE reconciliation_dirty = TRUE"
- "When bad_feedback_rate > 30%, delegate refinement as HIGH PRIORITY"

---

## Routing Fixes

Case-diagnosis produces an attribution and fix route. Each fix route goes to an autonomous owner when confidence is high enough. Human attention is a fallback for unsafe ambiguity, not the default repair path.

| Fix Type | Route | Owner | Urgency |
|----------|-------|-------|---------|
| Detection rule bug | Delegate to automate-detection-pattern | Detection agent | High — keeps firing |
| Triage routing bug | Propose/test triage-nodes skill patch | Triage optimization agent | Medium |
| Inspector judgment bug | Propose/test inspect-infra-issue skill patch | Inspect optimization agent | Medium |
| Repair judgment/evidence bug | Propose/test repair skill patch or evidence-gate fix | Repair optimization agent | Medium |
| Automation logic bug | Propose/test automate-detection-pattern skill patch | Automation optimization agent | Medium |
| Vendor uncertain / unknown | Observe or create attention if blocked | Attention queue | Low/Medium |

**Rule code bugs are delegated (not patched)** because:
- They're urgent — the rule fires every cron cycle
- automate-detection-pattern has replay tooling plus live TDD tooling (`test_rule_once`) to validate the fix
- The fix is code, not skill logic

**Skill patches can be autonomous when they are narrow and evidence-backed:**
- High-confidence attribution can create a task directly for the owning optimization agent.
- The patch must include replay evidence from the failed case and at least one counterexample it must not break.
- Broad reasoning changes, weak evidence, or policy changes create an attention item instead of silently changing behavior.

---

## Full Flow Diagram

```
RMA completes
    │
    ▼
analyze-rma-cases / reconcile-findings
    │  - query case_memory for completed RMA outcomes
    │  - call reconcile_finding() to flip verdicts
    │  - output: case/finding links + session IDs
    │
    ├──▶ Attribution Gate (case-diagnosis)
    │        classify root cause:
    │        detection | triage | inspect | repair | automation | vendor_uncertain | unknown
    │        │
    │        ├─ detection + medium/high confidence
    │        │      → set rule_reconciliation_state.reconciliation_dirty = TRUE
    │        ├─ triage/inspect/repair/automation
    │        │      → skill/evidence-gate fix route
    │        └─ vendor_uncertain/unknown
    │               → attention / observe
    │
    ├──▶ Loop 1: Rule Optimization (event-driven, hourly check)
    │        automate-detection-pattern Step 2:
    │        find rules WHERE rule_reconciliation_state.reconciliation_dirty = TRUE
    │        │
    │        ├─ bad_count=0 → stale dirty, clear
    │        ├─ attempts=0 → delegate refinement
    │        ├─ attempts=1 → demote + delegate refinement
    │        ├─ attempts=2 → contain + replay + alternate proposal
    │        └─ attempts≥3 → stay contained, self-diagnose, attention only if blocked/unsafe
    │
    │        replay suite + live dry-run pass →
    │          update_rule_code() finalizes:
    │          dirty=FALSE, nff_baseline_at=NOW(), attempts+1
    │
    └──▶ Loop 2: Agent Optimization
             triage/inspect/repair/automation attribution
             → propose skill or evidence-gate patch
```

---

## Implementation Phases

### Phase 1: Data Loop (Week 1)

- [ ] Add `repair_outcome`, `repair_outcome_at` columns to `patrol_findings`
- [ ] Add `rejected_nff` to valid verdicts
- [ ] Add `rule_reconciliation_state` table (without `last_delegated_at` — dedup is via Chat UI task check)
- [ ] Add `rma_finding_reconciliations` table in the agent-feedback DB (same EVIDENCE_DB_URL Postgres as patrol-cron)
- [ ] Add `collector_snapshot_id` and `raw_evidence_hash` pointers to `patrol_findings`, populated atomically on finding creation
- [ ] Add `rule_replay_cases` table for immutable frozen replay fixtures
- [ ] Implement `reconcile_finding` MCP tool in patrol-cron (records repair outcome)
- [ ] Implement `get_rule_bad_feedback_rate()` in patrol-cron
- [ ] Implement `list_dirty_reconciliation_rules()` MCP tool in patrol-cron
- [ ] Implement `list_unreconciled_rma_outcomes()` MCP tool in agent-feedback
- [ ] Implement rule replay MCP tools: create, list, single-case replay, suite replay
- [ ] Deploy schema migration

### Phase 2: Reconciliation (Week 1-2)

- [ ] Create `reconcile-findings` sub-skill in `analyze-rma-cases`
- [ ] Wire into daily schedule
- [ ] Test: create completed RMA outcomes in case_memory → `list_unreconciled_rma_outcomes()` returns them → matched findings reconciled
- [ ] Test: create NFF case → reconcile finding → verify finding flips to rejected_nff
- [ ] Test: verify reconciliation alone does not set `rule_reconciliation_state.reconciliation_dirty`

### Phase 3: Attribution Gate (Week 2)

- [ ] Extend `case-diagnosis` to assign attribution: detection, triage, inspect, repair, automation, vendor_uncertain, unknown
- [ ] Store attribution, confidence, and fix_route in `rma_finding_reconciliations`
- [ ] Only set `rule_reconciliation_state.reconciliation_dirty` for detection-attributed medium/high-confidence cases
- [ ] Create failed-case replay fixture for each detection-attributed NFF/MISCLASSIFIED case
- [ ] Create or attach positive counterexample replay fixture before delegating rule update
- [ ] Test: NFF caused by bad raw rule evidence → attribution=detection → dirty set
- [ ] Test: NFF caused by triage misjudgment → attribution=triage → dirty not set
- [ ] Test: NFF caused by inspection misjudgment → attribution=inspect → dirty not set
- [ ] Test: missed dirty-rule follow-up → attribution=automation → dirty not set
- [ ] Test: low-quality vendor NFF → attribution=vendor_uncertain → dirty not set

### Phase 4: Rule Optimization (Week 2)

- [ ] Update `automate-detection-pattern` skill: add Step 2 (reconciliation-driven check)
- [ ] Step 2: query `rule_reconciliation_state` WHERE reconciliation_dirty = TRUE
- [ ] Step 2: before delegating, check Chat UI for existing open task targeting the same `rule_id` with `automate-detection-pattern` skill — skip if active
- [ ] Step 2: call get_rule_bad_feedback_rate(), which counts all reconciled RMA outcomes as denominator and detection-attributed medium/high-confidence bad outcomes as numerator, then apply repair depth by attempts
- [ ] Update `update_rule_code()`: finalize nff/misclassified fixes only after replay suite and live dry-run pass
- [ ] Add safe/shadow containment path that reduces blast radius without clearing dirty, resetting baseline, or incrementing attempts
- [ ] Test: detection-attributed reconciled findings → verify automation catches bad rate → delegates refinement
- [ ] Test: candidate rule update fails replay → dirty remains TRUE, baseline unchanged, attempts unchanged
- [ ] Test: candidate rule update passes failed case, counterexample, and live dry-run → dirty clears, baseline resets, attempts increments
- [ ] Test: second reconciliation → verify demotion + second delegation
- [ ] Test: third reconciliation → verify safe/shadow containment, replay expansion, and alternate rule proposal
- [ ] Test: blocked/unsafe autonomous repair → verify attention item is created with evidence and required decision

### Phase 5: Agent Optimization (Week 2-3)

- [ ] Add routing logic: detection → rule code, triage → triage skill, inspect → inspect skill, repair → repair skill/evidence gate, automation → automation skill
- [ ] Test: triage-attributed NFF case → case-diagnosis → create triage optimization task
- [ ] Test: inspect-attributed NFF case → case-diagnosis → create inspect-infra-issue optimization task
- [ ] Test: repair-attributed misclassification → case-diagnosis → create repair evidence-gate optimization task

---

## Resolved Operating Defaults

1. **Trigger threshold:** any detection-attributed bad feedback (`bad_count >= 1`) triggers first-attempt rule refinement. Do not wait for a percentage threshold; RMA feedback is sparse and delayed.
2. **Minimum sample size:** no minimum sample size for first action. Use `rma_total` and `bad_feedback_rate` for priority ordering, but a single high-confidence NFF/MISCLASSIFIED case is enough to create replay and start repair.
3. **Schedule:** reconciliation runs after `case_memory` receives completed RMA outcomes. `case-diagnosis` runs on newly reconciled bad outcomes. `automate-detection-pattern` checks dirty rules hourly so rule feedback does not wait for a daily sweep.
4. **MISCLASSIFIED second attempt:** demote action first when the wrong classification caused over-action (`drain`/`cordon` → `alert` or `log_only`). Demote severity only when the action was already safe but priority/category was wrong.
5. **Ambiguous RMA/finding matches:** record as unlinked feedback for autonomous weekly review when evidence is incomplete. Create an attention item immediately only when the ambiguous match could cause unsafe action, duplicate repair, or incorrect attribution.
