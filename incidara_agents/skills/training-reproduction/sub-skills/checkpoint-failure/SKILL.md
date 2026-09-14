---
name: training-reproduction-checkpoint-failure
description: "Controlled checkpoint replay with complete experiment, decision, recovery action and proof rules per hypothesis."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# Checkpoint failure reproduction playbook

## Trigger

Use with upstream goal, original artifact/payload/writer/endpoint context, candidates, trusted fallback and safety budget.

## Goal

Use one cross-check to determine whether failure follows artifact, endpoint, writer, load, head placement or restore lifecycle.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| No hypothesis | Failure precise but missing Worker/artifact evidence can be captured | Same payload/config; replay only in isolated/safe endpoint with added capture | Same first file error+new named evidence→Diagnosis；only failure→Inconclusive | No broad isolation |
| `CKPT-ARTIFACT` | Content vs endpoint unclear | Same artifact on healthy/local; Known-good same-size artifact on original endpoint; separately change artifact | Failure follows artifact and integrity check fails→support | Quarantine checkpoint/Job, use previous trusted |
| `CKPT-ENDPOINT` | Endpoint candidate | Same payload/writer on healthy/local vs suspect endpoint | Both failed and Known-good artifacts fail only endpoint→support | Isolate endpoint/storage domain |
| `CKPT-WRITER` | Writer/sharding/replica candidate | Same payload/endpoint; change one writer count/assignment/sharding setting | Failure follows writer setting/Rank→support | Fix writer/config or Node if physical control fails |
| `CKPT-LOAD` | Concurrency candidate | Same payload/endpoint; only writer/background concurrency changes and load measured | Error/latency appears/disappears with load repeatedly→support | Limit/admit load or expand storage; not node RMA |
| `CKPT-HEADNODE` | Service-node contention | Same writer/payload/endpoint on head vs equivalent non-head placement | Failure/pressure only head placement→support | Move writer/service or isolate head role issue |
| `CKPT-RESTORE` | Restore-created persistent effect | Same config/nodes/window: Fresh vs same-checkpoint Restore | Extra threads/memory/communicators and symptom only Restore→support | Fix/disable Restore path/version |

## Minimal reproduction

Use only when full payload/state/group ownership is required and cannot fit available complete nodes; preserve payload, writer concurrency and first error.

## Interpretation

Supported means failure follows sole variable and control passes. Artifact missing/incomplete before run needs no active reproduction. Different file error is not equivalent.

## Output

Return goal, hypothesis, payload/artifact, fixed/changed variable, exact endpoint/writer/load, expected/actual first error, status, safe checkpoint/action and proof limit.
