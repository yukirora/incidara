---
name: automate-detection-pattern
description: "Manage detection rules: review, create, promote/demote/refine based on accuracy. Auto-trigger when asked to create rules, review rules, or manage detection patterns."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# automate-detection-pattern

Manage detection rules through their lifecycle: review what exists, validate the pipeline, create rules for gaps, promote/demote/refine based on accuracy.

## Available MCP Servers

- **patrol-cron** — collectors, rules, findings, verdicts, accuracy, replay, raw data
- **node-ops** — `delegate_to_agent(agent_id, title, prompt, completion_mode="manual")`
- **agent-evidence** — RMA feedback examples, investigation evidence

**Reference files:**
- `references/schema.md` — collector source format, target filter, RuleResult/Observation lifecycle protocol, working examples
- `references/rule-writing-principles.md` — 8 code quality rules for analyze() + common false-positive fixes
- `references/refinement-diagnosis.md` — 🟠 root cause diagnosis: READ evidence → identify root cause (for Step 3)
- `references/refinement-implementation.md` — 🟠 refinement execution: CHANGE → VALIDATE → OBSERVE loop (for Step 4)
- `references/structural-diagnosis.md` — structural issues: accuracy deadlock, duplicate collectors, wrong cadence (for Step 3)
- `references/structural-implementation.md` — split/merge/add source procedures (for Step 4)
- `references/pipeline-diagnosis.md` — 🔴 pipeline broken: find where data is blocked (for Step 3)
- `references/pipeline-implementation.md` — 🔴 pipeline broken: fix collector/config/analyze (for Step 4)

## Rule Stages

| Stage | Behavior | Purpose |
|-------|----------|---------|
| `log_only` | Findings logged. No action. | Observe and validate |
| `create_task` | Findings → investigation tasks | Agent investigates each |
| `submit_alert` | Findings → alert (no cordon) | Agent spot-checks |
| `auto_cordon` | Findings → auto-cordon | Fully automated, agent monitors |

## Workflow

### Step 1: Review — What Exists

Survey all collectors and rules. Understand current state.

**If triggered with a new pattern** — first check:
- `list_collectors` → collector exists for this data?
- `list_rules` → rule exists for this pattern?

**For all collectors** — `list_collectors`:
- Which collectors exist? What does each collect?
- Any collector not bound to a rule? → wasted collection

**For all rules** — `list_rules`:
- What stage is each rule at?
- Which are enabled / disabled?

### Step 2: Flag — Which Rules Need Attention

For each rule from Step 1, walk this decision tree and assign a flag.

**Gather per rule:** `get_collector_health(collector_name)`, `list_findings(rule_id, current_code_only=True, limit=5)`, `get_rule_accuracy(rule_id)`, `get_rule_accuracy(rule_id, since_code_update=False, window_days=30)`

**Decision tree:**

```
1. Collector running on schedule?
   ├── Overdue → 🔴 Pipeline broken (cron dead)
   └── Yes → 2

2. Targets resolved? (total > 0)
   ├── 0 targets → 🔴 Pipeline broken (no targets)
   └── Yes → 3

3. Targets collecting successfully? (ok vs failed)
   ├── All failed → 🔴 Pipeline broken (collector execution)
   ├── Some ok, some failed → normal (unreachable targets), continue
   └── All ok → continue

4. Findings from current code? (list_findings current_code_only=True)
   ├── 0 findings → test_rule_once(rule_id):
   │   ├── Error → 🔴 Pipeline broken (analyze crashes)
   │   ├── No findings → 🔴 Pipeline broken (analyze no match)
   │   └── Findings produced → 🟡 No signal (fleet clean)
   ├── 100+ unresolved → 🟠 Flooding
   └── Normal count → 5

5. Verdicts exist? (judged > 0)
   ├── 0 judged → 🟡 Starved
   └── Yes → 6

6. Accuracy? (confirmed / judged)
   ├── Below stage threshold → 🟠 Accuracy bad
   │   (create_task <50%, submit_alert <80%, auto_cordon <70%)
   ├── Current-code < 30-day accuracy → 🟠 Regression
   ├── Meets promotion threshold → 🟢 Graduate
   └── Otherwise → ⚪ Fine
```

**Notes:**
- A rule can have multiple flags (e.g., 🟠 regression + 🟠 accuracy bad)
- `from_old_code=true` on all findings → current code has never fired; don't analyze old findings, `run_collector_once(collector_name)` to test

### Step 3: Diagnose — What's the Root Cause?

For each flagged rule from Step 2, answer: **WHY** is it flagged? Output one root cause label per rule.

| Flag | Diagnosis question | Possible root causes | Deep diagnosis |
|---|---|---|---|
| 🔴 **Pipeline broken** | Where in the pipeline is data blocked? | `pipeline: cron dead`, `pipeline: no targets`, `pipeline: all targets fail`, `pipeline: analyze crashes`, `pipeline: analyze no match`, `pipeline: db error`, `pipeline: missing config` | `references/pipeline-diagnosis.md` |
| 🟡 **No signal** | Pipeline works, but nothing to detect? | `fleet clean` | Quick: `test_rule_once(rule_id)` → if findings, pipeline works |
| 🟡 **Starved** | (No diagnosis — just needs verdicts) | `no verdicts` | — |
| 🟠 Flooding | Why so many findings? | `rule too broad`, `cascade contamination` | `references/refinement-diagnosis.md` |
| 🟠 Accuracy bad | What distinguishes confirmed from rejected? | `cascade contamination`, `wrong signal`, `missing filter`, `accuracy deadlock`, `non-predictive signal` | `references/refinement-diagnosis.md` |
| 🟠 Regression | Which change caused the accuracy drop? | `recall loss`, `precision loss from rule change`, `collector change`, `sample composition` | `references/refinement-diagnosis.md` |
| 🟢 Graduate | (No diagnosis — confirm thresholds) | `ready for graduation` | — |
| Structural | What structural issue? | `duplicate collectors`, `wrong cadence`, `missing source` | `references/structural-diagnosis.md` |
| ⚪ Fine | (No diagnosis needed) | `fine` | — |

**Output per flagged rule:** one root cause label, e.g., `large_job_failure_v1: cascade contamination`

### Step 4: Act — Execute in Priority Order

**Priority: pipeline → verdicts → accuracy → promote.** Fix blockers before refinements.

#### 1. Fix pipeline (🔴 root causes)

| Root cause | Action | Reference |
|---|---|---|
| Pipeline: cron dead | Restart collector cron | — |
| Pipeline: no targets | Fix `target_filter` or verify hosts exist | `update_collector` |
| Pipeline: collector execution | Fix collector bug (auth, command, timeout) | `references/pipeline-implementation.md` |
| Pipeline: analyze crashes | Fix syntax error or missing field in analyze() | `update_rule_code` |
| Pipeline: analyze no match | Fix logic mismatch between analyze() and payload | `update_rule_code` → `references/schema.md` |
| Pipeline: db error | Check DB connection, credentials | — |
| Pipeline: missing config | Add missing env vars (SSH_AUTH_SOCK, DB_URL, etc.) | — |

#### 2. Get verdicts (🟡 Starved)

Delegate for investigation (Step 5). Without verdicts, you can't diagnose accuracy.

#### 3. Fix accuracy (🟠 root causes)

| Action | Root causes | How | Reference |
|---|---|---|---|
| **Refine rule code** | Cascade contamination, wrong signal, missing filter, rule too broad, precision loss from rule change | `update_rule_code` | `references/refinement-implementation.md` |
| **Split rule** | Accuracy deadlock | Create hard + soft rule, re-link findings | `references/structural-implementation.md` |
| **Rollback rule** | Recall loss | `rollback_rule(rule_id)` | — |
| **Rollback collector** | Collector change caused regression | `rollback_collector(name)` | — |
| **Demote + refine** | Flooding | `update_rule_stage` → then refine code | — |
| **Stop refining** | Non-predictive signal | Demote or add corroboration requirement | — |
| **Watch** | Sample composition, fleet clean | No action — don't chase noise | — |
| **Fix structural** | Duplicate collectors, wrong cadence, missing source | Merge/split/add source collector | `references/structural-implementation.md` |

Simple code fix (1-2 lines) → do yourself. Complex → delegate to detection agent.

#### 4. Promote (🟢 Graduate)

**Graduation table:**

| Stage | Condition | Action |
|-------|-----------|--------|
| `log_only` | ≥ 48h, no volume spike (≤ 200 findings/day) | → `create_task` |
| `log_only` | Volume spike (> 200/day) | Refine before promoting |
| `create_task` | judged ≥ 5, accuracy ≥ 50% | → `submit_alert` |
| `create_task` | accuracy < 30% AND confirmed < 2 | Refine code (don't demote) |
| `submit_alert` | judged ≥ 5, accuracy ≥ 80%, no false cordons | → `auto_cordon` |
| `submit_alert` | accuracy < 40% | → `create_task` |
| `auto_cordon` | accuracy < 70% | → `submit_alert` |
| Any stage | Recall loss: confirmed cases no longer caught | Fix urgently |

#### Validate after every action

`test_rule_once(sample=5)` → `test_rule_once()` → `run_rule_replay_suite` → persist. See `references/refinement-implementation.md` Step 2 (VALIDATE) for full procedure.

**Before `update_rule_code(..., mode="finalize")`:** must pass replay suite. If zero replay cases, generate first: `create_rule_replay_cases_from_feedback(rule_id)`. If still zero → route back to `analyze-rma-cases`/`case-diagnosis`.

**For new or migrated rules:** use the structured lifecycle output in `references/schema.md`. Rules return `RuleResult(observations=[...], state=new_state)`, not raw findings. `Observation.lifecycle` tells the engine whether a signal is a persistent condition or an event, when to open a finding, and when to deactivate it.

### Step 5: Close the Loop — Delegate

**1. Unjudged findings → detection agent** (for investigation/verdict):
- `list_findings(rule_id=..., verdict="", since_days=7)` — get ONLY unjudged findings
- **Always delegate to `"detection"`**, never `"repair"`. Detection investigates via `/inspect-infra-issue`, determines root cause, then forwards to repair if hardware confirmed.
- `delegate_to_agent("detection", title, prompt, completion_mode="manual")`
- Title: `Finding: {target_id} {brief description}`
- Prompt: Finding ID, rule context, evidence summary, "Use /inspect-infra-issue to investigate"
- Without verdicts, rules can never graduate from `log_only`

**2. Rules needing refinement → delegate per rule**:
- `delegate_to_agent("detection", title, prompt, completion_mode="manual")`
- Title: `Refine rule: {rule_id} — {brief problem}`
- Prompt: rule ID, accuracy, root cause label, what fix should do, "Use /automate-detection-pattern to refine"
- Simple fix (1-2 lines) → do it yourself with `update_rule_code` + `test_rule_once`. Complex → delegate.
- **Every rule needing refinement must have a delegate task.**

### Step 6: Self-Review

Review every change against the self-review checklist in the relevant implementation reference file:
- Rule code changes → `references/rule-writing-principles.md`
- Refinement changes → `references/refinement-implementation.md`
- Structural changes → `references/structural-implementation.md`
- Pipeline fixes → `references/pipeline-implementation.md`

## Important Rules

- **Always start at `log_only`** — never skip stages
- **Accuracy = confirmed / judged** — only count verdicts
- **No false cordons for auto_cordon** — even one blocks promotion
- **0 bad observations/findings with healthy collector = fine** — not every rule must produce bad observations
- **NEVER disable a rule** — if accuracy is low: (1) refine code, (2) demote stage, (3) only disable if completely wrong. Disabled rule → re-enable + demote to `log_only`, then refine.
- **This skill closes the feedback loop** — unjudged findings must be delegated for investigation, otherwise rules stay stuck at log_only forever
- **This skill does NOT investigate** — that's `inspect-infra-issue`
- **This skill does NOT scan** — that's `scan-cluster`

**Detection principles:**
- **Recall is non-negotiable** — don't demote to fix precision if it misses real failures
- **Maximize precision within recall** — too many NFF findings waste RMAs
- **Promote confidently, demote cautiously** — false positives can be refined in-place
- **`log_only` is a spam check, not an accuracy gate** — promote to `create_task` to get verdicts

**Vertex and anti-pattern — REQUIRED before creating any new rule:**
- **Vertex**: the exact transition point where the signal crosses from normal to abnormal. You must establish this by comparing the **normal baseline** (before the issue) vs the **abnormal pattern** (during the issue). The threshold for the rule should be set at or just above the vertex.
- **Anti-pattern**: the condition that is **absent during normal operation** but present during the issue. If the signal also appears during normal operation (even at lower values), you need a threshold — and the threshold must be derived from the vertex, not guessed.
- **Without before/after comparison, don't create a rule** — a rule without a known baseline will fire on normal traffic and cause massive false positives. Always ask: "What was this signal's value for X days before the issue? What is it during the issue?"
- **Example**: "ERR 1F07 was 0 for 7 days (normal baseline), then jumped to 61,597 in 1 hour (abnormal). Vertex = first non-zero ERR 1F07. Threshold = delta > 0. Anti-pattern = any non-zero ERR 1F07 (absent during normal operation)."
- **Counter-example (overfitted)**: "SM activity rate was 1-2K/s normally, spiked to 14K/s. Threshold = >5K/s." — This is risky because 5K/s might occur during legitimate heavy fabric reconfiguration. The vertex is not clean. A better signal would be ERR 1F07 (binary: 0 vs non-zero) which has a clean vertex.

**Concrete signal identification — REQUIRED when creating a new rule:**
- The rule must detect **one specific, collectable signal** — not "check logs for errors"
- The signal must be obtainable via SSH command, sysfs path, or existing collector
- The signal must be **binary or thresholdable** with a vertex-derived threshold
- The signal must have **lead time** — ideally detectable before impact occurs
- Present as: "Signal: `{exact command}` → normal: `{value for X days}`, vertex: `{transition point}`, threshold: `{value}`"

## Refinement Discipline

⛔ **READ BEFORE REFINING ANY RULE.** All guardrails and procedures are in:
- `references/refinement-diagnosis.md` — evidence requirements, root cause catalog, collector vs rule decision
- `references/refinement-implementation.md` — one change at a time, CHANGE→VALIDATE→OBSERVE loop, anti-patterns, self-review
