---
name: job-incident-response
description: "Top-level lifecycle orchestrator for job incidents. Coordinates log triage, bounded system-evidence diagnosis, evidence-backed isolation and recovery, optional controlled reproduction, post-recovery RCA, release validation, and knowledge closeout."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# job-incident-response

Define the incident state machine, allowed transitions, and shared context. This skill initializes the chain; after that, each completed major skill selects an allowed `NEXT_SKILL` and hands the accumulated context directly to it.

## Lifecycle

```text
DETECTED
→ RAPID_DIAGNOSIS
→ ISOLATION_DECISION
→ RECOVERING
→ RECOVERED
→ DEEP_DIAGNOSIS
→ FIX_VALIDATION
→ LEARNING
→ CLOSED
```

The online priority is bounded diagnosis sufficient for safe isolation, followed by recovery. Full root-cause proof happens after impact is controlled.

## 1. Establish incident context

Obtain:

```text
job and attempt identity
live/finished state
single-job or correlated multi-job impact
allocated nodes/GPUs and failure domain
last healthy step and last valid checkpoint
current progress loss and user impact
recent image/framework/driver/firmware/data/topology/policy changes
safety and rapid-diagnosis time budget
```

Immediate job-level stop is allowed when continued execution risks data, checkpoints, or wider impact. Node/path/version/storage isolation still requires supporting evidence.

## 2. Run log triage

Load `/job-log-triage` with the job context. It selects a failure-mode log runbook and returns:

```text
job state and failure mode
last healthy/expected step
first log anomaly and propagation
affected rank/process/node
candidate hypotheses and exclusions
requested system evidence
last valid checkpoint
```

Do not infer a physical fault from the last rank that reports a timeout.

## 3. Run bounded system-evidence diagnosis

The completed `/job-log-triage` hands the failure mode, candidate hypotheses, incident window, affected objects, and requested evidence directly to `/system-evidence-diagnosis`.

It queries and correlates existing logs, stacks, communicator dumps, TSDB, kernel/GPU, network, storage, scheduler, topology, and change data. It returns a normalized timeline, pattern matches, supported/weakened hypotheses, maximum justified isolation scope, confidence, and evidence gaps.

Limit rapid diagnosis to evidence that can change the immediate action. Each additional round must eliminate a hypothesis or change the isolation scope.

## 4. Decide whether fast reproduction is necessary

The completed `/system-evidence-diagnosis` hands directly to `/training-reproduction` with goal `fast-isolation` only when one short safe replay can change the immediate action without delaying checkpoint protection or job containment. Valid modes are:

```text
zero hypotheses
→ precise phenomenon capture can obtain a named missing observation needed for isolation

one hypothesis
→ a known-good/null control can validate it

two or more hypotheses
→ one experiment can partition them into different isolation actions

fix/action exists
→ original failure plus healthy guards can validate release
```

Skip reproduction when fatal job/process/GPU/PCIe/RDMA evidence already supports isolation. If no safe distinguishing evidence or experiment exists, isolate only the job and recover on disjoint known-good resources.

## 5. Isolate and recover

The completed system-evidence or fast-reproduction stage hands the supported scope, evidence, safety boundary, and checkpoint directly to `/job-recovery`.

Only `job-recovery` may perform production mutations. Before mutation it captures a bounded evidence set. It then isolates only the supported object, aborts the whole communicator/job when required, validates the checkpoint, restores on allowed healthy resources, and verifies forward progress plus a newly committed checkpoint.

Recovery controls user impact; it does not by itself prove root cause.

## 6. Continue after recovery

When root cause, permanent disposition, or an automation candidate remains unresolved, the completed recovery stage hands its result and accumulated diagnosis context directly onward:

1. Run `/system-evidence-diagnosis` with a wider incident window and healthy/cross-job controls.
2. Load source/profile/core-specific existing skills only when the evidence points there.
3. Load `/training-reproduction` in `deep-diagnosis` stage when passive evidence cannot prove the mechanism.
4. Record exact proof limits; local or partial reproductions cannot refute a larger interaction.

## 7. Validate a fix or automated action

The completed recovery/fix/closeout stage hands directly to `/training-reproduction` with goal `validation` and target `recovery`, `fix`, or `automation`. Replay the original first-anomaly condition plus healthy guard cases. Require correctness, progress, throughput/tail, action safety, and rollback checks before limited rollout.

## 8. Close and learn

The completed deep-diagnosis or validation stage hands the complete incident record directly to `/rca-closeout`. It records trigger, root cause, contributing factors, propagation, isolation, recovery, outcome, recurrence, and evidence gaps, then proposes updates to:

```text
job-log-triage log patterns
system-evidence-diagnosis metric/correlation patterns
training-reproduction experiments
recovery/repair runbooks
detection rules
regression cases
```

New automation must pass historical replay, non-actioning observation, limited rollout, and outcome review before gaining more autonomy.

## Stop and route rules

```text
clear user/config error
→ reply; no node action

fatal physical evidence
→ bounded capture; isolation/recovery; no active reproduction

only job-level evidence
→ isolate job; disjoint recovery; preserve evidence gap

safe experiment changes isolation
→ fast reproduction, then recovery

job recovered but RCA unresolved
→ deep evidence/reproduction

fix validated and knowledge persisted
→ close
```

## Output Format

```text
## Initial Job Incident Context

Incident identity and current lifecycle stage
Impact, progress loss, and safety budget
Known job/allocation/checkpoint/change context
Initial goal: contain-and-recover
Allowed transition and time budget
NEXT_SKILL: /job-log-triage
```

## Handoff

Initial handoff:

```text
NEXT_SKILL: /job-log-triage
HANDOFF_GOAL: establish failure mode, first log anomaly, candidates, and next evidence request
CONTEXT: job/attempt, allocation, state, artifacts, checkpoint/change and time/progress anchors
```

Allowed direct transitions after initialization:

```text
job-log-triage → system-evidence-diagnosis | job-recovery | user reply
system-evidence-diagnosis → job-recovery | training-reproduction | rca-closeout
training-reproduction → job-recovery | system-evidence-diagnosis | rca-closeout
job-recovery → system-evidence-diagnosis | training-reproduction | rca-closeout
rca-closeout → training-reproduction(validation) | CLOSED
```

Every handoff appends its result to the same incident context and must include `NEXT_SKILL`, `HANDOFF_GOAL`, entry reason, required input, and proof limit. A transition not listed here returns `unsupported` instead of inventing a workflow.

## Sub-skills

- `sub-skills/reply-user` — format the final user-facing diagnosis and action without sending a message
