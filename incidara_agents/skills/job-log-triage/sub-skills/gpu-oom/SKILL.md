---
name: job-log-triage-gpu-oom
description: "GPU OOM log playbook with complete cause, symptom, keyword/order and decision output per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# GPU OOM log-triage playbook

## Trigger

Use when the first failure is GPU out-of-memory/resource-exhausted or peers later timeout after one Rank allocation failure.

## Goal

Extract failing allocation, Rank/GPU/Node, Step/Phase and memory values, then distinguish state, activation, workspace, fragmentation, checkpoint overlap and Physical GPU candidates.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `OOM-STATE` | Weights/Gradients/Master/Optimizer或Shared/Dense/Expert状态在某Stage过重 | Init/Load时或固定PP Stage先OOM；尚未进入大Activation；同逻辑Stage跨Node复现 | `allocating parameters/optimizer/model state`、`state_dict`、`parameter`、`RESOURCE_EXHAUSTED/OOM`。顺序：状态初始化→大Allocation→OOM | 输出requested/allocated/reserved/free、Rank/Stage和EP/TP/PP/FSDP/ZeRO配置；有Forward前Activation则不能只归State |
| `OOM-ACTIVATION` | Microbatch、Sequence、PP/CP Schedule、Recompute设置导致Activation峰值 | Forward/Backward特定层/微步OOM；随Batch/Sequence变化；Init正常 | `forward/backward/activation/remat/recompute/microbatch/sequence`→目标Layer→OOM | 输出Step/Layer/Stage/Microbatch/Sequence和First Allocation；不能为复现直接调小因果Microbatch/Sequence |
| `OOM-WORKSPACE` | MoE Tokens/Expert、Dispatch/Combine Buffer、Expert GEMM或Collective Workspace峰值 | Router/Expert/Collective附近OOM；只在负载偏斜/Shape/Message出现 | `router/expert/dispatch/combine/GEMM/workspace/NCCL buffer`、Tokens/Expert或Shape→OOM | 输出Expert/Rank、Tokens、Shape、Message和Workspace请求；无这些日志只为候选 |
| `OOM-FRAGMENTATION` | Reserved远大于Allocated；Allocation顺序/缓存碎片；Restore/反复循环积累 | 有名义Free但小请求失败；运行一段时间或Restore后才出现；重启可能消失 | `reserved/allocated/free`、`fragmentation`、`memory snapshot`、`empty_cache`、失败Allocation。顺序：Reserved增长/空洞→请求失败 | 输出Allocator数值、运行周期、Fresh/Restore；仅“重启后好”不能证明Fragmentation |
| `OOM-CHECKPOINT` | Save/Restore Staging、Writer/Rank额外状态或D2H/H2D重叠 | OOM与Checkpoint/Restore周期重合，特定Writer/Stage出现 | `checkpoint/save/restore/staging/load state`→Memory Peak→OOM | 输出Checkpoint Step、Writer/Stage和所有Rank结果；只有时间相关时不充分 |
| `OOM-NODE` | 实际VRAM/保留不同、固定GPU/Node异常、Xid/PCIe或硬件状态 | 相同逻辑Rank在其他GPU能Fit，只有固定Physical GPU失败；可能伴随Xid | `Xid/ECC/GPU lost`、设备容量/保留差异和OOM。顺序：物理异常/容量差→Allocation失败 | 输出Logical Rank→GPU UUID→Node和健康GPU对照需求；一条OOM不能判坏卡 |

## Interpretation

First allocation failure is primary; later NCCL Timeout is propagation. Aggregate model bytes/total GPUs cannot prove per-Rank fit. Report maximum Rank/Stage and the suspected memory component.

## Output

Return scope/stage, first OOM line, requested/allocated/reserved/free, Rank/GPU/Node/Layer/Stage/Step, peer propagation, each hypothesis decision, required state/activation/workspace/allocator/checkpoint/hardware evidence, trusted checkpoint and handoff.
