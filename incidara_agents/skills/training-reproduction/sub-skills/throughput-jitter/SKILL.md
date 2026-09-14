---
name: training-reproduction-throughput-jitter
description: "Controlled throughput Jitter replay with complete equal-exposure experiment, decision and action rules per hypothesis."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# Throughput Jitter reproduction playbook

## Trigger

Use with upstream goal, original jitter signature/period, sufficient cycles, surviving hypotheses and root metrics.

## Goal

Use equal-budget repeated trials so the predicted root metric changes before P99/CV/slow-step frequency.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| No hypothesis | Jitter精确但缺named event；Replay能捕获 | Settings固定，补高分辨率现有指标/stack across multiple periods | Capture repeated first event→Diagnosis；只见Jitter→Inconclusive | 无Root Cause动作 |
| `JITTER-CHECKPOINT` | Checkpoint correlation | Same workload/resource/cycles；Checkpoint on vs bounded off or one Writer setting | Spikes/root I/O follow checkpoint repeatedly→支持 | Expected or fix Checkpoint/Storage config |
| `JITTER-NETWORK` | Phased Path candidate | Same Message/Group/Load；suspect vs healthy Path/Group over equal windows | RDMA/Collective and jitter follow Path→支持 | Isolate Path/HCA/Node |
| `JITTER-LEAK` | Exposure-dependent leak | Fresh vs Restore/Feature-on, same duration/cycles | Resource growth precedes jitter only target condition→支持 | Runtime/Restore/Feature fix |
| `JITTER-HARDWARE` | Persistent Physical suspect remains | Historical first; then identical control on suspect/healthy resource | Root hardware metric+jitter follow physical object→支持 | Isolate object；do not recreate Fatal event |
| `JITTER-PROFILER` | Profile/Recompile event | Same run with trigger on/off | Spike only follows trigger→支持 | Adjust profiling/compile workflow |
| `JITTER-INPUT` | Fixed input candidate | Suspect input on healthy resource; good input on suspect resource | Follows input→Data；follows resource→Node/Storage | Isolate corresponding object |
| `JITTER-ROUTING` | MoE routing candidate | Same model/resource/cycles；one Input/Router/Capacity condition | Routing metric leads and jitter changes repeatedly→支持 | Router/Input/Config |
| `JITTER-PLACEMENT` | Policy or noisy load candidate | Choose one: Placement A-B or concurrent Load A-B; never both | Jitter/root metric follow sole variable→支持 | Rollback Policy or limit load |
| `JITTER-EXPECTED` | Planned phase candidate | Historical same-phase comparison; normally no active run | Magnitude/period match→expected | No isolation |

## Minimal reproduction

Preserve original period and exposure; never shrink observation below the cycle. Use minimal planner only for selected MoE Group/Topology/Load.

## Interpretation

Supported needs multiple cycles and root metric leading jitter. One clean run, unequal exposure or changed period is Inconclusive.

## Output

Return goal, hypothesis/gap, cycles/windows, fixed/changed conditions, root and P99/CV results, status, action and proof limit.
