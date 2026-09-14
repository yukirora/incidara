---
name: job-log-triage-loss-nan
description: "Read-only log playbook for training Loss NaN/Inf. Locates the first non-finite stage/rank and maps complete log patterns to data, checkpoint, numerical, operator, hardware, collective, and optimizer hypotheses."
allowed-tools: Bash Read Grep Glob
---

# Loss NaN log-triage playbook

## Trigger

Use when logs report NaN/Inf in input, activation, logits, local loss, gradients, optimizer state, overflow/finite checks, or a job terminates after numerical divergence.

## Goal

Find the last finite step and first logged non-finite tensor/stage/rank, distinguish origin from multi-rank propagation, and determine what passive system evidence is required. Loss NaN alone never proves GPU SDC.

## Scope and stage

Return:

```text
IMPACT_SCOPE: single-rank | single-job | same-node-multi-job | multi-attempt | unknown
FAILURE_STAGE: input | forward | local-loss | backward | pre-collective | post-collective | optimizer | checkpoint/restore | unknown
```

For repeated jobs, preserve Job/Attempt, Allocation and Rank→GPU→Node; state whether candidate nodes are a union, intersection, or frequency/exposure result.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `NAN-DATA` | 输入/标签含NaN/Inf；预处理溢出；空Mask；标签越界；极端长度；坏Batch/Data Shard；Loss分母为0 | 第一非有限值在Forward前/第一层输入；跨健康GPU随Batch/Shard/Logical Rank复现；Known-good输入在同GPU正常 | 关键字：`non-finite input`、`invalid label`、`index out of range`、`empty mask`、`divide by zero`、`DataLoader`、`shard/sample id`。顺序：读取固定Batch→Input/Label检查失败→Activation/Loss NaN | 支持：日志明确输入先坏。排除：输入有限、某Module Output才先坏。输出失败Batch/Shard、Cursor、Seed、Preprocess版本；没有原Batch则标Evidence Gap，不隔离Node |
| `NAN-CHECKPOINT` | 分片/Manifest/Checksum损坏；Partial Commit；World Size/并行映射不兼容；Parameter/Optimizer/Scaler/RNG恢复不一致 | Fresh Start正常；Restore后首个/前几个Step NaN；同Checkpoint跨资源失败；前一Checkpoint正常；加载后状态已非有限 | 关键字：`restore/load checkpoint`、`reshard`、`manifest/shard/checksum/committed`、`missing/unexpected key`、`shape mismatch`、`optimizer/scaler/RNG state`。顺序：Restore完成→加载状态异常→首个Forward/Step NaN | 支持：第一异常紧跟Restore且状态/兼容日志吻合。排除：同Checkpoint在健康资源通过、Known-good Checkpoint只在固定GPU失败。输出Checkpoint ID、所有Shard/Commit结果、并行映射和前一可信Checkpoint |
| `NAN-NUMERICAL` | 除零、Log/Exp/Softmax溢出；全空Mask；Gradient Explosion；低精度范围；Loss Scale、Unscale或Clip顺序；超参数发散 | Loss/Activation/Gradient Norm在NaN前增长；跨健康GPU复现；FP32或局部关闭Autocast后可能消失；无固定Node关联 | 关键字：`NaN/Inf/non-finite`、`overflow/underflow`、`GradScaler/found_inf/skip step`、`gradient norm/clip_grad`、`log/exp/divide/softmax/norm/mask`、`learning rate/update ratio`。顺序：数值范围先恶化→高风险算子输出Inf/NaN→Loss/Gradient NaN | 支持：日志有NaN前数值轨迹和数学机制。排除：相同输入仅固定GPU失败，或Input相同但Target Operator Output首次偏离。输出首个数值边界、精度、Scaler、LR、Clip和Batch/Shape |
| `NAN-OPERATOR` | Framework/Compiler/Kernel/Fused Operator错误；Shape/Dtype/Layout特定实现错误；版本回归 | 某Module Input有限，Output第一次偏离/NaN；跨健康节点随Image/Kernel版本；只在特定Shape/并行配置出现 | 关键字：Module/Operator名、`forward`、`kernel`、`compile`、`fused`、`illegal value`、`numerical mismatch`、Shape/Dtype、版本/Commit。顺序：上游Output正常→目标Operator Input正常→目标Output首次异常 | 支持：日志/已有Validator指向明确Operator边界。排除：Input已经异常；或错误稳定随Physical GPU而不随版本。输出Module、调用顺序、Input/Output Shape/Dtype、Rank/Step和Reference缺口 |
| `NAN-HARDWARE` | GPU计算单元/HBM/PCIe/NVLink错误；静默数据损坏；Node局部状态；同一Physical Node跨Job复发 | 不同Job/Logical Rank在同一Physical Node重复错误；Known-good正确性用例可能失败；ECC/Xid可有可无；排除Node后Job成功 | 关键字：`Xid`、`ECC`、`AER/PCIe`、`NVLink`、`memory access fault`、`GPU lost`以及跨Job重复错误文本。顺序：若显式硬件Log在NaN前出现则为强证据；NaN后出现只能作传播结果 | 支持：跨Attempt重复日志与同一Physical Node映射；排除Node后成功。排除：问题随Batch/版本/Logical Rank走。输出各轮异常节点集合（并集/交集必须明确）、重复Node证据和最大Node级结论；无Known-answer不能直接写SDC |
| `NAN-COLLECTIVE` | 某Rank向Collective输入坏Tensor；Rank Desync；Shape/Count/Dtype/Root不一致；Collective/通信实现问题 | Tensor在Collective前有限、后面非有限；多Rank随后同步报NaN；Flight Recorder显示契约不一致或某Rank先提供坏输入 | 关键字：`allreduce/reduce-scatter/allgather`、`ProcessGroupNCCL`、`sequence/count/dtype/root/shape`、`watchdog/timeout`。顺序：Pre-collective有限→Collective契约/输入异常→Post-collective或后续Gradient NaN | 支持：有通信前后边界或契约证据。排除：Local Forward Loss在任何Gradient Collective前已NaN。输出Process Group、Sequence、各Rank Start/End、Input有限性和缺失Rank |
| `NAN-OPTIMIZER` | Adam m/v溢出；Master Weight损坏；AMP Unscale/Clip/Step顺序错误；应Skip却更新；Fused Optimizer Kernel；分布式State不一致 | Step N Forward/Gradient有限；`optimizer.step()`后Parameter或m/v第一次非有限；Step N+1 Forward Loss NaN；Restore后首Step可能触发 | 关键字：`optimizer.step`、`Adam`、`exp_avg/exp_avg_sq`、`master weight`、`GradScaler/found_inf/unscale/skip step`、`clip_grad`、`state_dict`。顺序：Loss有限→Gradient及Collective后有限→Optimizer执行→Parameter/State非有限→下一Step Loss NaN | 支持：第一非有限值明确在Optimizer更新后。排除：Input/Activation/Gradient此前已NaN；加载后State已坏则转`NAN-CHECKPOINT`；Collective后Gradient先坏则转`NAN-COLLECTIVE`。输出Step前后Parameter/m/v/Scale状态和各Rank是否同步Step |

## Interpretation

```text
input invalid before forward
→ NAN-DATA

state invalid immediately after restore
→ NAN-CHECKPOINT

module input finite, output first bad
→ NAN-OPERATOR or NAN-NUMERICAL

gradient first bad after collective
→ NAN-COLLECTIVE

gradient finite, optimizer update makes state bad
→ NAN-OPTIMIZER

failure repeatedly follows one Physical Node across jobs
→ NAN-HARDWARE candidate
```

## Output

Return scope/stage, last finite and first NaN step/tensor/rank, ordered log evidence, propagation, Job/Attempt→Rank→GPU→Node, every hypothesis support/exclusion result, all required passive metrics/artifacts already named in the matching table row, last trusted checkpoint, evidence gaps and `NEXT_SKILL=/system-evidence-diagnosis` unless immediate job-level containment is required.
