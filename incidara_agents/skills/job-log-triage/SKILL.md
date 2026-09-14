---
name: job-log-triage
description: "Read-only job status, progress, and log-pattern triage. Establishes what happened, the first log anomaly and propagation, candidate hypotheses, and the next system-evidence request; it does not isolate, recover, or actively reproduce."
allowed-tools: Agent Bash Read Grep Glob
---

# job-log-triage

Receive the initial incident context from `/job-incident-response`, produce the first evidence result, and hand it directly to the next allowed major skill.

## Scope

Use job events, details, scheduler state, authoritative worker/rank logs, raw TensorBoard progress when needed, and persisted job artifacts to answer:

```text
What state is the job in?
Did training make forward progress?
What failed first in the logs?
Which later messages are propagation or termination mechanisms?
Which candidate hypotheses remain?
What system evidence is required next?
```

Do not query broad system evidence, mutate production, start a benchmark, or declare a physical root cause from a peer timeout.

## Common workflow

1. Resolve job and attempt identity, job state, allocation, config, image/version, and artifact locations.
2. Read job events and the authoritative log. For multi-rank jobs, identify which rank/node produced each line.
3. Determine the observed impact scope before choosing a subsystem. Do not infer cross-job/rack/fabric scope from one job log.
4. Determine the lifecycle stage and recent change before expanding root-cause candidates.
5. For buffered launchers, prefer direct worker/rank logs for training progress; cross-check the primary log.
6. Determine completed-step progress rather than log modification time.
7. Build a timestamped log sequence from the first diagnostic event through timeout/cancellation.
8. Select exactly one failure-mode sub-skill below.
9. Append scope, stage, candidate hypotheses and a semantic evidence request, then set `NEXT_SKILL` and `HANDOFF_GOAL`.

For a live job, compare the last completed step with projection from recent healthy step time. For a finished job, compare completed-step time sum with wall time and identify unexplained gaps. Do not assume a gap is a Hang without matching evidence.

## Mandatory impact-scope and stage gates

Every failure-mode sub-skill must report both fields before its hypothesis list:

```text
IMPACT_SCOPE: single-rank | single-job | same-node-multi-job | rack/rail | multi-independent-job | unknown
FAILURE_STAGE: admission | initialization | first-operation | steady-state | checkpoint/restore | teardown | change-correlated | unknown
```

The log layer may prove rank/job scope from current artifacts. Claims about other jobs, rack/rail, switch/fabric, or a shared change require persisted multi-job events or a handoff request to `/system-evidence-diagnosis`. If unknown, say so; do not silently promote a single-job symptom to a shared incident.

Scope controls where diagnosis starts: broader scope raises shared dependencies earlier. Stage controls which candidates are possible: initialization config differs from a steady-state process exit, and teardown errors can be downstream artifacts.

## Failure-mode routing

```text
waiting, image, mount, barrier, admission
→ sub-skills/startup-scheduling

completed steps stopped while job remains alive
→ sub-skills/job-hang

NCCL/RCCL/watchdog collective timeout
→ sub-skills/nccl-timeout

GPU allocation/resource-exhausted memory failure
→ sub-skills/gpu-oom

Loss NaN/Inf or numerical divergence
→ sub-skills/loss-nan

steady-state mean/P50 throughput regression
→ sub-skills/throughput-slowdown

P99/CV/slow-step frequency regression
→ sub-skills/throughput-jitter

checkpoint read/write/restore/finalization failure
→ sub-skills/checkpoint-failure

traceback, signal, segfault, unknown death, node exit
→ sub-skills/process-crash
```

If several signatures exist, route by the earliest event that can explain the rest. Report every downstream signature but do not let it replace the first anomaly.

## Common log-quality gates

```text
correct job and attempt
correct failing rank/node
complete enough time range before the terminal error
authoritative progress source
clock/timezone known when correlating sources
cancel/timeout distinguished from preceding failure
```

Empty or truncated logs produce an evidence gap, not a healthy conclusion.

## Novel-candidate gate

After evaluating every canonical table row, handle zero matches explicitly:

```text
known pattern is plausible but its required observation is missing
→ return a named Evidence Gap; do not invent a cause

mechanism matches a canonical row but the exact signature is new
→ keep the canonical hypothesis ID and report a pattern gap

adequate log evidence contradicts all canonical rows
→ propose 1–3 incident-scoped PROVISIONAL_CANDIDATES
```

Each provisional candidate must include `NOVEL-<failure-mode>-<n>`, observed first anomaly, causal mechanism, why known rows do not fit, a prediction, a refuting observation, next passive evidence, action difference, and proof limit. Candidates must be mutually distinguishable and are `unverified`, not new canonical table IDs. Do not propose one without a causal observation and a safe way to test or refute it.

## Output Format

```text
## Job Log Triage Result

Job/attempt and state
Observed impact scope, supporting source, and proof limit
Failure mode, lifecycle stage, and recent change
Authoritative log sources and coverage
Last healthy and expected step
First log anomaly with rank/node/time
Propagation sequence
Candidate hypotheses supported by logs
Hypotheses weakened by logs
Provisional candidates, or why none may be proposed
Harmless/expected messages excluded
Requested system evidence by candidate
Last valid checkpoint
Log evidence gaps
NEXT_SKILL and HANDOFF_GOAL
```

## Handoff

Choose the next allowed stage and pass the complete result directly:

```text
runtime training failure needs passive system evidence
→ NEXT_SKILL: /system-evidence-diagnosis
→ HANDOFF_GOAL: query/correlate the requested sources and evaluate candidate patterns

clear user/config/startup error with no production mutation
→ NEXT_SKILL: /job-incident-response/sub-skills/reply-user
→ HANDOFF_GOAL: explain the first error and user action

continued execution is unsafe but logs justify only job-level containment
→ NEXT_SKILL: /job-recovery
→ HANDOFF_GOAL: capture bounded evidence, stop only the job, and recover on disjoint healthy resources
```

Do not hand directly to `/training-reproduction`: exhaust the requested passive system evidence first. Append the log result, next skill, goal, required input, and proof limit to the incident context.

## Sub-skills

- `sub-skills/startup-scheduling`
- `sub-skills/job-hang`
- `sub-skills/nccl-timeout`
- `sub-skills/gpu-oom`
- `sub-skills/loss-nan`
- `sub-skills/throughput-slowdown`
- `sub-skills/throughput-jitter`
- `sub-skills/checkpoint-failure`
- `sub-skills/process-crash`
