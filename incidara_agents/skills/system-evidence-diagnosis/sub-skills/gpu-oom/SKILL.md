---
name: system-evidence-gpu-oom
description: "GPU OOM evidence playbook with complete memory evidence, time/object pattern, decision rule, isolation scope and proof limit per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# GPU OOM system-evidence playbook

## Trigger

Use after OOM log triage supplies the failed Allocation, Rank/Stage/Phase, config and candidates.

## Goal

Find the limiting per-Rank/Stage component and decide whether pressure follows logical ownership/config/checkpoint or Physical GPU/Node.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `OOM-STATE` | Per-Rank/Stage Weights/Gradients/Master/Optimizer；Expert/Shared/Dense ownership；EP/TP/PP/FSDP/ZeRO；usable VRAM/reserve | State dominates before Forward and same heavy Stage exceeds usable memory across equivalent resources | 支持：ownership calculation+measured peak agree. 排除：state fits and Activation/Workspace first grows. Aggregate average is insufficient | Job/Config/Stage；adjust only allowed sharding or add resources, not Node |
| `OOM-ACTIVATION` | Per-step/layer peak；Microbatch/Sequence；PP/CP schedule；Recompute；max Rank/Stage | Peak follows Forward/Backward shape and changes predictably with Microbatch/Sequence/Schedule | 支持：Activation is limiting component and same Shape reproduces. 排除：OOM before Forward or Workspace/State dominates | Job/Config；for OOM reproduction lock causal Shape/Microbatch/Sequence |
| `OOM-WORKSPACE` | Tokens/Expert/skew；Dispatch/Combine bytes；Expert GEMM Shape；Collective/Kernel workspace；Rank peak | Router/Expert/Collective workspace rises immediately before OOM on max Rank | 支持：workspace target and failing Allocation match. 排除：tokens/shape normal and another component dominates | Job/Router/Kernel/Config；not Node absent physical follow evidence |
| `OOM-FRAGMENTATION` | Allocated/Reserved/Inactive split；memory snapshot/history；failed request/order；Fresh vs Restore/repeat exposure | Reserved-to-allocated gap/fragmentation grows; modest request fails; Fresh healthy, repeated/Restore fails | 支持 needs allocator/order evidence. 排除：deterministic same peak from first run or physical capacity difference. No snapshot→Gap | Job/Runtime/Restore version；restart may recover but is not proof |
| `OOM-CHECKPOINT` | Checkpoint phase；Writer/Replica/Stage；staging buffers；Host/GPU memory timeline；successful checkpoint control | Memory peak and OOM align only with Save/Restore staging/ownership | 支持：phase+owner+peak repeat. 排除：same pressure outside checkpoint or follows GPU | Checkpoint/Writer/Restore config；protect previous checkpoint |
| `OOM-NODE` | Actual GPU VRAM/reserve/capacity；Rank→GPU UUID→Node；Xid/ECC/PCIe；same workload on healthy GPU；Known-good memory test | Equivalent logical workload fits elsewhere; failure/health evidence follows one Physical GPU/Node | 支持Physical only with follow/control evidence. 排除：same logical Stage fails across healthy GPUs. One OOM line is insufficient | Node/GPU only with strong evidence; otherwise Job/config |

## Interpretation

Use `max_rank_stage(M_peak)`, not aggregate State/GPU count. Identify limiting component: Expert, Shared, Optimizer, Activation, Workspace or Runtime reserve. Empty allocator/metric evidence is a gap.

## Output

Return per-Rank/Stage memory breakdown, usable capacity/reserve, failed request/order, limiting component, every hypothesis decision, maximum isolation scope, memory mode (`fit-with-headroom` or `reproduce-memory-pressure`), rejected adjustments and handoff.
