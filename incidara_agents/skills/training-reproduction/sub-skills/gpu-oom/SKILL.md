---
name: training-reproduction-gpu-oom
description: "Controlled GPU OOM replay with full entry, fixed/changed experiment, decision, action and proof rules per hypothesis."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# GPU OOM reproduction playbook

## Trigger

Use with upstream goal, exact OOM rank/stage/allocation, candidates, memory evidence, trusted checkpoint, resources and safety budget.

## Goal

Reproduce the target pressure/allocation signature and determine which memory component/config or Physical GPU carries it. Do not merely make the workload fit.

## Common fixed context

Use `reproduce-memory-pressure`: preserve first OOM, max Rank/Stage, failing Allocation size/order, peak-to-usable ratio and all non-memory protected Group/Shape/Message/Path/Schedule signals.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| No hypothesis | OOM精确但limiting component/Allocation history缺失；Replay能捕获 | Settings固定，只增加memory snapshot/peak/state ownership capture | 同一OOM+拿到命名字段→回Diagnosis；只“又OOM”无组件证据→Inconclusive | 不产生具体Config/Node结论 |
| `OOM-STATE` | State ownership suspected | 固定Model/Batch/Shape；只改变一个allowed State-sharding/parallel degree或资源数，对照原配置 | Peak/failed request按ownership预测移动→支持；改变protected Shape/Group则无效 | 调整State sharding/resources；不删相关State |
| `OOM-ACTIVATION` | Forward/Backward Activation limiting | 固定Model/Resource；Microbatch/Sequence/Recompute/CP/PP仅选择一个变量；原配置为故障对照 | OOM threshold/peak随该Shape变量预测变化且第一位置一致→支持 | 配置修复；若目标是原OOM，不用较小Microbatch冒充复现 |
| `OOM-WORKSPACE` | Expert/Dispatch/GEMM/Collective workspace suspected | 固定Group/Model/Resource；一次只改变Tokens/Expert、Router/Capacity、Expert TP/Kernel或buffer条件 | Workspace/Allocation与Tokens/Shape/Message同现，控制通过→支持 | Router/Kernel/Config；必须说明改变是否降低Fidelity |
| `OOM-FRAGMENTATION` | Fresh健康、Restore/重复后失败 | 同Checkpoint/Config/Resource/Allocation sequence；Fresh vs Restore/repeated equal exposure | Reserved/fragmentation/order差异先出现且只有目标生命周期OOM→支持 | Runtime/Allocator/Restore修复；一次重启成功不充分 |
| `OOM-CHECKPOINT` | Save/Restore staging candidate | 同Payload/Model/Resource；Checkpoint on/off或Writer Placement一项变化，保持目标并发 | OOM/peak只随Checkpoint/Writer条件→支持 | Checkpoint/Writer/Endpoint配置 |
| `OOM-NODE` | Physical GPU容量/健康未分 | 相同Checkpoint/Batch/Config在可疑与健康GPU；Known-good memory test在两边 | 只随Physical GPU失败且健康Control通过→支持Node；跨GPU同Stage失败→Config | 隔离Node/GPU only with evidence；Fatal已足够则不主动复现 |

## Minimal reproduction

Large MoE cases call `../minimal-reproduction` with memory mode `reproduce-memory-pressure`; it must not optimize away the target error.

## Interpretation

Supported requires the same first OOM/allocation signature and control. Different OOM location, changed protected signal or insufficient allocation exposure is Inconclusive.

## Output

Return goal, hypothesis/gap, memory target, fixed/changed settings, exact resource, measured component peaks, failed allocation/order, status, action and proof limit.
