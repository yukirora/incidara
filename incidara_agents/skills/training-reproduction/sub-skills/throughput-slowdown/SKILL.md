---
name: training-reproduction-throughput-slowdown
description: "Controlled sustained Slowdown replay with complete experiment, decision and action rules per hypothesis."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# Sustained Slowdown reproduction playbook

## Trigger

Use with upstream goal, comparable lower steady plateau, surviving hypotheses, root metric, exact windows and resource/safety budget.

## Goal

Use one controlled A-B/cross-check to make both TGS/P50 and the candidate root metric move as predicted.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| No hypothesis | Slowdown精确但缺named root metric；Replay能Profile/capture | Settings固定，只增加同窗口Profile/metric capture | 拿到first persistent cost→Diagnosis；只再次变慢→Inconclusive | 无Root Cause动作 |
| `SLOW-NETWORK` | Path/Group候选 | 固定Model/Message/Load；suspect vs healthy Group/Path，一次只换Path/Node group | TGS/Collective/RDMA同时随Path恢复/恶化→支持 | 隔离Path/Node；单TGS变化不足 |
| `SLOW-RESTORE` | Restore leak候选 | Same Image/Config/Nodes/steps；Fresh Start vs same-Checkpoint Restore | Only Restore shows extra resource+lower TGS→支持 | Disable/fix Restore path/version |
| `SLOW-HOST` | Background/Data/Storage condition | Same workload/resource；one background/data/checkpoint/storage condition on/off | Root Host/I/O metric and TGS co-move→支持 | Remove contention or isolate endpoint/policy |
| `SLOW-HARDWARE` | Fixed slow Node/GPU | Identical Known-good workload on suspect vs healthy Node | Regression follows physical resource and control metric→支持 | Isolate Node/GPU；Fatal evidence skips replay |
| `SLOW-EXPECTED` | Phase may explain | Same-phase historical control；usually no active run | Magnitude/duration match and recover→expected | No isolation |
| `SLOW-VERSION` | Software regression | Same Input/Resource/window；current vs healthy version/config；then equivalent Profile | TGS and predicted Kernel/Stage cost follow version→支持 | Block/Rollback version |
| `SLOW-ROUTING` | Expert imbalance | Same model/resource；one Input/Router/Capacity condition, preserve equal steps | Tokens/Expert first then Expert/Collective/TGS moves→支持 | Router/Input/Config |
| `SLOW-PLACEMENT` | Allocation topology | Same workload/Image；fixed healthy vs Scheduler Placement or old/new Policy | Regression only follows Placement while resources pass→支持 | Rollback Policy; do not quarantine nodes |

## Minimal reproduction

Use minimal planner only when selected MoE Group/Shape/Message/Path/Load/steady window cannot fit complete nodes.

## Interpretation

Supported requires steady TGS/P50 and candidate root metric to move together. Throughput-only change, non-comparable windows or changed workload is Inconclusive.

## Output

Return goal, hypothesis, fixed/changed conditions, windows, exact runs, TGS/P50/root metric results, action and proof limit.
