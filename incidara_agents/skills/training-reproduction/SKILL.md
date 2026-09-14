---
name: training-reproduction
description: "Controlled-experiment orchestrator for fast isolation, post-recovery deep diagnosis, and recovery/fix/automation validation. Accepts zero or more hypotheses according to reproduction mode and calls minimal-reproduction only when a selected MoE experiment needs a smaller complete-node configuration."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# training-reproduction

Execute active experiments. Log-pattern classification belongs to `/job-log-triage`; passive metric/cross-source pattern matching belongs to `/system-evidence-diagnosis`.

## Entry contract

The completed upstream skill must set the reproduction goal; this skill must not infer it.

Every request must provide:

```text
reproduction goal: fast-isolation | deep-diagnosis | validation
validation target when applicable: recovery | fix | automation
failure mode
reproduction mode
observed failure phenomenon and known first anomaly/propagation, if available
unresolved decision or named evidence gap
diagnosis context, existing evidence, completed isolation/recovery when applicable, and proof limit
available launchers, observations, resources, topology, safety, retry, and time budget
```

The mode determines the additional input:

```text
phenomenon-capture
→ zero hypotheses is valid
→ require a precise observable failure signature and the named missing evidence replay can collect
→ no changed variable or causal control is required; settings stay fixed

hypothesis-validation
→ require one surviving hypothesis, its prediction, and a known-good/null/before-change control

hypothesis-discrimination
→ require two or more surviving hypotheses and the different action each implies
→ the failure-mode sub-skill selects one experiment and one changed variable

release-validation
→ require the implemented fix/action, original failure signature, and healthy guard cases
→ the changed condition is before-fix versus after-fix
```

Do not invent a hypothesis in reproduction. Use only canonical hypothesis IDs or incident-scoped `NOVEL-*` candidates supplied and passively reviewed upstream. Do not run phenomenon capture when replay cannot collect a named missing observation, or an experiment whose outcomes lead to the same action.

## Reproduction goals

### fast-isolation

Set by `/system-evidence-diagnosis` when online impact remains, passive evidence cannot select the isolation object, and a short safe experiment can distinguish scopes without delaying containment. Output hands directly to `/job-recovery`.

### deep-diagnosis

Set after successful isolation/recovery when diagnosis context exists but mechanism, recurrence condition, or permanent node/path/version disposition remains unproved. Output hands to `/rca-closeout`, or back to `/system-evidence-diagnosis` only when the experiment produced a new passive-evidence request.

### validation

Set after a recovery method, fix, or automation exists. The upstream handoff must name the validation target and provide the original failure signature plus healthy guards. Output hands to `/job-recovery` for a recovery-plan correction, or `/rca-closeout` for fix/automation acceptance or rejection.

## Failure-mode routing

```text
Job Hang              → sub-skills/job-hang
NCCL/RCCL timeout     → sub-skills/nccl-timeout
GPU OOM               → sub-skills/gpu-oom
Loss NaN/Inf          → sub-skills/loss-nan
sustained Slowdown    → sub-skills/throughput-slowdown
throughput jitter     → sub-skills/throughput-jitter
checkpoint failure    → sub-skills/checkpoint-failure
process crash         → sub-skills/process-crash
```

Each sub-skill defines only active reproduction patterns: entry condition, method, one changed variable, fixed conditions, control, expected observation, and action mapping.

## Hypothesis count

```text
0 hypotheses
→ phenomenon capture only when replay obtains a named missing observation

1 hypothesis
→ test against a known-good/null/before-change control

2+ hypotheses
→ choose one experiment that best partitions hypotheses into different actions

fix exists
→ original-case regression plus healthy guards
```

A passively reviewed `NOVEL-*` candidate counts toward the same 0/1/2+ rule but remains provisional. Its experiment must test its recorded prediction against its refuting control and lead to a different action. Return `inconclusive` rather than changing the candidate during execution; new causal ideas go back through log/evidence diagnosis.

## Minimal MoE planning

Use the selected failure-mode experiment directly when it is already a bounded retry, node/pair test, artifact check, historical comparison, or small A/B.

Load `/training-reproduction/sub-skills/minimal-reproduction` only when the already-selected experiment cannot fit available complete-node resources and its group, memory, topology, load, and exposure must be recalculated. The planner must not choose another hypothesis.

## Execute and interpret

Before execution state the upstream reproduction goal and why the experiment is needed. For phenomenon capture, name the missing observation and keep settings fixed. For hypothesis validation/discrimination or release validation, state the hypothesis/fix, method, one changed condition, fixed conditions, control, predictions, action mapping, and safety/stop limits.

Use only launchers, benchmarks, metrics, dumps, topology, and resource controls that exist. Record exact commands, resources, settings, times, and artifacts.

Return:

```text
supported      failure and control behavior match the prediction
refuted        observation contradicts the hypothesis
inconclusive   execution completed but evidence/exposure cannot distinguish
unsupported    required safe resource/tool/topology/observation does not exist
```

## Output Format

```text
## Training Reproduction Result

Reproduction goal, validation target when applicable, failure mode, and reproduction mode
Canonical hypothesis IDs or incident-scoped NOVEL candidate IDs when present, or precise phenomenon/evidence gap when none
Why active reproduction was needed
Experiment, prerequisite, changed variable, fixed conditions, and control
Exact commands/resources/settings/artifacts
Expected and actual first anomaly
Result: supported | refuted | inconclusive | unsupported
Isolation/RCA/release action enabled by the result
Proof limit and remaining evidence gap
```

## Handoff

```text
fast-isolation result
→ NEXT_SKILL: /job-recovery
→ pass supported isolation scope, evidence, checkpoint/safety boundary, and proof limit

deep-diagnosis result with RCA proof
→ NEXT_SKILL: /rca-closeout
→ pass trigger/root-cause proof, reproduction result, isolation/recovery context, and remaining gap

deep-diagnosis result that only produced a new evidence request
→ NEXT_SKILL: /system-evidence-diagnosis
→ pass exact source/object/time query and experiment artifacts

validation of recovery finds a problem
→ NEXT_SKILL: /job-recovery
→ pass failed post-condition and rollback/correction request

validation of fix/automation completes
→ NEXT_SKILL: /rca-closeout
→ pass original-case and healthy-guard results plus rollout/rollback decision
```

Do not mutate production or close the incident directly.

## Sub-skills

- `sub-skills/job-hang`
- `sub-skills/nccl-timeout`
- `sub-skills/gpu-oom`
- `sub-skills/loss-nan`
- `sub-skills/throughput-slowdown`
- `sub-skills/throughput-jitter`
- `sub-skills/checkpoint-failure`
- `sub-skills/process-crash`
- `sub-skills/minimal-reproduction`
