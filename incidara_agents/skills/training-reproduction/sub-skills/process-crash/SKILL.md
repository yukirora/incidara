---
name: training-reproduction-process-crash
description: "Safe controlled process crash replay with complete experiment, decision, action and proof rules per hypothesis."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# Process crash reproduction playbook

## Trigger

Use with upstream goal, precise crash signature/phase, first PID/Rank evidence, candidates, retained state and safety budget.

## Goal

Capture a named missing Core/Stack fact or distinguish input/version/race/host-memory/Physical GPU/scheduler causes without intentionally recreating Fatal hardware in production.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| `CRASH-OBS` / No hypothesis | Crash precise but source missing and replay can capture Core/Stack | Settings fixed; enable approved core/stack/sanitizer only; bounded replay | Same crash+named artifact→Diagnosis；no artifact/no recurrence→Inconclusive | Job-level only |
| `CRASH-USER` | Deterministic input/operator candidate | Fixed healthy resource/version；failing vs Known-good input or current vs fixed code, one variable | Same exception follows input/code across healthy resources→support | Fix Job/Input/Commit |
| `CRASH-RACE` | Low-rate concurrency/lifecycle candidate | Equal-budget repeated trials preserving concurrency/order; current vs fixed version; collect core/sanitizer | Crash rate/signature follows condition with sufficient exposure→support | Block version/path；one success/failure insufficient |
| `CRASH-HOST-OOM` | Host memory producer unclear | Same workload/host class；one memory-producing setting/background condition; measure OOM trajectory | Host pressure and killed PID follow sole variable→support | Resource config/Host platform fix |
| `CRASH-GPU` | Physical ambiguity remains, no Fatal proof | After quarantine, failing workload on healthy GPU and Known-good test on suspect GPU | Only suspect physical resource fails and healthy passes→support | Isolate Node/GPU；Fatal evidence skips replay |
| `CRASH-SCHEDULER` | External termination ambiguity | Use persisted accounting or isolated scheduler replay; do not rerun expensive training merely to see preemption | Event/replay follows policy/walltime→support | Explain expected or fix policy |

## Minimal reproduction

Use only if a large MoE interaction is necessary for the crash; preserve concurrency/lifecycle/first signal. Never intentionally inject fatal physical faults.

## Interpretation

Supported requires same crash signature and control. Low-rate race needs equal exposure; different signal or no retained state is Inconclusive.

## Output

Return goal, hypothesis/gap, crash signature, fixed/changed condition, trials/exposure, artifact, status, action and proof limit.
