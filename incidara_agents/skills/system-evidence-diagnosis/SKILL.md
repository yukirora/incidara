---
name: system-evidence-diagnosis
description: "Read-only cross-source evidence orchestrator for job incidents. Executes failure-mode evidence playbooks, queries TSDB through its sub-skill, correlates rank/process/GPU/node/HCA/path and time, and returns hypothesis pattern matches and evidence gaps."
allowed-tools: Agent Bash Read Grep Glob
---

# system-evidence-diagnosis

Execute the semantic evidence request produced by `/job-log-triage`. Query and correlate evidence; do not perform recovery or active reproduction.

## Input

```text
job and attempt identity
failure mode and stage
last healthy step and incident window
allocated ranks/processes/nodes/GPUs
canonical hypothesis IDs, incident-scoped provisional candidates, and log evidence
requested evidence and healthy control
rapid or deep diagnosis budget
```

## 1. Select the failure-mode evidence playbook

```text
Job Hang              → sub-skills/job-hang
NCCL/RCCL timeout     → sub-skills/nccl-timeout
GPU OOM               → sub-skills/gpu-oom
Loss NaN/Inf          → sub-skills/loss-nan
sustained Slowdown    → sub-skills/throughput-slowdown
throughput jitter     → sub-skills/throughput-jitter
checkpoint failure    → sub-skills/checkpoint-failure
process crash/death   → sub-skills/process-crash
```

The failure-mode sub-skill defines required data, expected metric/log/stack patterns, support and exclusion gates, and the maximum conclusion allowed by those patterns.

## 2. Query only requested existing sources

Use available read-only tools and persisted artifacts. Reuse evidence already collected by log triage.

```text
job/scheduler       job details, events, persisted pod/task state
training progress   authoritative worker/rank logs, raw TensorBoard events
runtime wait        training child/process stack and process state
communication       NCCL RAS, flight recorder, NCCL/RCCL logs
TSDB                sub-skills/tsdb-diagnosis
GPU/host            Xid/RAS/PCIe, OOM/PSI, runnable/blocked, dmesg
network             RDMA/HCA/port/path and switch/control-plane evidence
storage             read/write/latency/queue/metadata and service logs
change/topology      image/framework/driver/config/placement and rank mapping
```

Do not create new per-rank telemetry when existing evidence is merely missing. Return the exact evidence gap.

## 3. Normalize identities

Build only mappings supported by sources:

```text
incident → job → attempt → step
rank → PID → GPU UUID/index → node → HCA → physical path
model/data/checkpoint → image/framework/driver/config/change
```

A missing mapping limits the conclusion. Do not infer first rank from the last timeout reporter.

## 4. Normalize time

Map completed step and slow-step anchors to wall clock. Account for log buffering, TSDB sample interval, raw TensorBoard timestamps, timezone, and known clock uncertainty.

Order facts across sources:

```text
first source event
→ first progress change
→ propagation across ranks/jobs
→ timeout/cancellation/recovery
```

Correlation reports precedence and co-location; it does not by itself prove causation.

## 5. Evaluate evidence patterns

Apply the selected sub-skill's explicit gates. For each candidate hypothesis return:

```text
supported at the observed layer
weakened/refuted by contradictory evidence
not distinguishable with current evidence
not evaluated because required source is missing
```

Never force one hypothesis when several survive. In rapid mode, stop once evidence supports the immediate isolation scope. In deep mode, use wider/cross-job controls only when they can improve root-cause proof.

## Provisional-candidate evaluation

Accept incident-scoped `PROVISIONAL_CANDIDATES` from log triage only when it recorded why every canonical row was contradicted. If current passive evidence fills the earlier gap and itself contradicts all canonical rows, this stage may propose 1–3 candidates using the same schema. Reject a candidate as speculation if it lacks a causal mechanism, distinct prediction, refuting observation, or named existing passive source.

Evaluate accepted candidates with the same `supported | refuted | inconclusive | not evaluated` outcomes and proof limits as canonical hypotheses. A provisional candidate remains `NOVEL-*`; do not add it to a failure-mode table or use it alone to authorize Node/Rack isolation, RMA, or repair. If evidence is missing rather than contradictory, return the named Evidence Gap instead.

## Output Format

```text
## System Evidence Result

Verified job/attempt/allocation and source coverage
Step-to-time mapping and clock uncertainty
Normalized cross-source timeline
Affected rank/process/GPU/node/HCA/path mapping
Data quality, missing/stale series, and counter resets

Pattern result for every canonical hypothesis and provisional candidate
Earliest relevant anomaly and propagation chain
Maximum justified isolation scope
Confidence and proof boundary
Exact evidence gaps
Next evidence request or active-experiment question
NEXT_SKILL and HANDOFF_GOAL
```

## Handoff

Include exact source/query/artifact references, then hand directly according to incident stage:

```text
online evidence supports an isolation scope
→ NEXT_SKILL: /job-recovery
→ HANDOFF_GOAL: isolate the supported object and restore the job

online evidence cannot select isolation, but one safe replay can obtain a named gap or partition actions
→ NEXT_SKILL: /training-reproduction
→ REPRODUCTION_GOAL: fast-isolation
→ pass precise phenomenon, optional hypothesis IDs, unresolved isolation decision, protected observations/resources/safety budget

online evidence remains insufficient and no safe reproduction exists
→ NEXT_SKILL: /job-recovery
→ HANDOFF_GOAL: isolate only the job and recover on disjoint healthy resources; preserve the evidence gap

post-recovery passive evidence remains insufficient for RCA
→ NEXT_SKILL: /training-reproduction
→ REPRODUCTION_GOAL: deep-diagnosis
→ pass completed isolation/recovery and diagnosis context

post-recovery evidence proves RCA without active reproduction
→ NEXT_SKILL: /rca-closeout
→ HANDOFF_GOAL: persist proof and create validated knowledge candidates
```

Append the system-evidence result and proof limit to the incident context.

## Sub-skills

- `sub-skills/tsdb-diagnosis` — TSDB access, identity, coverage, metric semantics, and time-series queries
- `sub-skills/job-hang`
- `sub-skills/nccl-timeout`
- `sub-skills/gpu-oom`
- `sub-skills/loss-nan`
- `sub-skills/throughput-slowdown`
- `sub-skills/throughput-jitter`
- `sub-skills/checkpoint-failure`
- `sub-skills/process-crash`
