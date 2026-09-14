---
name: rca-closeout
description: "Close a recovered job incident by recording the evidence-backed RCA and converting validated findings into log patterns, system-evidence patterns, reproduction cases, recovery runbooks, detection rules, and guarded automation candidates."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# rca-closeout

Use after impact is controlled and the completed upstream major skill supplies the accumulated incident record.

## Required record

```text
impact and failure mode
first anomaly and propagation
trigger, root cause, and contributing factors
log/system/reproduction evidence with proof limit
isolation and recovery actions
checkpoint/progress loss and recovery outcome
fix and release-validation result
recurrence and remaining evidence gaps
provisional candidates and their validation disposition, when present
```

Do not close as known root cause when evidence supports only a layer or correlation.

## Convert validated knowledge

```text
stable log signature/order
→ patch the matching job-log-triage failure-mode sub-skill

stable metric/cross-source pattern
→ patch the matching system-evidence-diagnosis sub-skill

necessary controlled experiment
→ patch the matching training-reproduction sub-skill

safe deterministic isolation/recovery action
→ patch recovery/repair runbook with pre/post conditions

proactive repeatable signal
→ propose detection rule

fix or automation regression
→ preserve original failure case plus healthy guards
```

Each patch must include source evidence, scope, counterexample/guard, validation result, owner, expiry/review condition, and rollback where applicable.

## Promote a novel candidate

A `NOVEL-*` candidate becomes canonical only when evidence or a controlled comparison supports a reusable causal pattern and its refuting control fails as predicted. Recovery success or model confidence alone is insufficient.

Promotion is one atomic knowledge patch: assign the failure-mode ID, then add exactly one row with that same ID to the matching `job-log-triage`, `system-evidence-diagnosis`, and `training-reproduction` tables and run the ID/table contract tests. Preserve the original candidate, counterexample, proof limit, and healthy guard in the closeout record. If the result is one-off, correlated only at a layer, or inconclusive, keep it as case evidence and do not expand the canonical tables.

## Automation graduation

A candidate action progresses only after:

```text
historical replay
→ non-actioning observation
→ limited automatic execution
→ outcome/override/recurrence review
→ broader autonomy when justified
```

Escalate low confidence, correlated/batch faults, excessive impact, failed actions, and policy exceptions to people.

## Output Format

```text
## RCA Closeout Result

Incident classification
Trigger, root cause, contributing factors, and propagation
Evidence chain and proof boundary
Isolation/recovery/fix outcome
Log/system/reproduction/recovery knowledge patches
Novel-candidate disposition: promoted canonical ID | retained case evidence | refuted
Regression and healthy-guard cases
Automation candidate and current trust level
Owner, deadline, validation, and expiry/review condition
Decision: close | keep open
NEXT_SKILL and HANDOFF_GOAL when kept open
```

## Handoff

```text
fix lacks original-case/healthy-guard proof
→ NEXT_SKILL: /training-reproduction
→ REPRODUCTION_GOAL: validation
→ VALIDATION_TARGET: fix

automation candidate lacks safety/outcome proof
→ NEXT_SKILL: /training-reproduction
→ REPRODUCTION_GOAL: validation
→ VALIDATION_TARGET: automation

RCA, recovery outcome, knowledge patches, and required validation are complete
→ NEXT_SKILL: CLOSED
```

Knowledge patches go to their named failure-mode sub-skills only after validation. Automation candidates then continue through non-actioning observation, limited execution, and outcome review rather than direct production activation.
