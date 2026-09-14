---
name: system-evidence-loss-nan
description: "Read-only evidence playbook for Loss NaN. Each hypothesis defines all required metrics, cross-source patterns, decision rules, isolation scope, and proof limits in one row."
allowed-tools: Bash Read Grep Glob
---

# Loss NaN system-evidence playbook

## Trigger

Use when the completed Loss NaN log-triage handoff includes scope/stage, last finite and first NaN boundary, candidate hypotheses, Job/Attempt and requested objects.

## Goal

Use existing metrics/artifacts to support, exclude or leave each hypothesis unresolved and determine the maximum safe isolation scope. Do not request a second hypothesis definition elsewhere; every required passive signal and decision rule is below.

Use the TSDB sub-skill only to discover/query/validate the named metrics. Empty metrics are Evidence Gaps, not zero. This skill does not run instrumented replay or `torch_xray`.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `NAN-DATA` | Batch/Shard/Sample ID；Input/Label finite count、Min/Max/Range；长度/Mask/标签范围；Preprocess版本；失败与健康Job输入映射；Rank→Node | `same input across healthy GPUs → same first invalid input/NaN`；Known-good输入在可疑资源正常；无更早GPU/Host/Network错误 | 支持：第一非有限值在Input/Preprocess且故障随Batch/Shard。排除：失败输入在健康资源通过、不同输入在固定GPU失败；或Input有限、Module Output先坏。证据不足：原Batch/Cursor/RNG缺失 | 隔离Data Shard/Job/Preprocess版本；保护最后可信Checkpoint；不得隔离Node |
| `NAN-CHECKPOINT` | Checkpoint ID/Step；Shard/Manifest/Checksum/Commit；加载前后Parameter/Optimizer/Scaler有限性；World Size/EP/TP/PP映射；Fresh/Restore和前一Checkpoint对照 | `failed checkpoint across healthy nodes → NaN`；`previous trusted checkpoint + same batch/version → healthy`；加载后状态在Forward前已非有限 | 支持：故障跟Checkpoint、不跟Node，且第一异常在加载后/首Step。排除：Checkpoint在健康资源通过而Known-good状态只在固定GPU失败。证据不足：只有Restore时间相关，无状态/前版对照 | 隔离Checkpoint/Restore版本；回退最后完整兼容Checkpoint；不隔离Node |
| `NAN-NUMERICAL` | Per-component Loss；Activation/Logit范围；Gradient/Parameter Norm；Update Ratio；LR；AMP Scale/Found Inf/Skip Step；Precision/Autocast；Batch/Shape；健康阶段基线 | NaN前Loss/Norm/Range持续恶化；跨健康GPU同配置复现；FP32或局部关闭Autocast时预测性改善；无Physical Node复发 | 支持：数学机制和时间顺序一致，随超参数/精度/Batch走。排除：同输入只在固定GPU错误；或Input相同、Target Operator Output首次偏离Reference。证据不足：只有最终NaN、无前序数值轨迹 | Job/Config/Batch级；停止污染Checkpoint；回滚数值配置或转Operator验证 |
| `NAN-OPERATOR` | Module调用顺序；Input/Output finite、Shape/Dtype/Layout；Framework/Compiler/Kernel/Image版本；健康Reference已有结果；Rank/GPU映射 | 上游Output和目标Input仍匹配Reference，目标Module Output第一次偏离/NaN；错误跨健康GPU随版本或特定Shape出现 | 支持：已有Validator/数值Dump证明第一分歧边界且随软件版本。排除：Input已坏；或不同输入都稳定随Physical GPU失败。证据不足：只有Operator名/Stack，没有相同Input的Reference/Target结果 | 最多隔离软件版本/Kernel候选；若缺Reference，交给Reproduction运行`torch_xray`或等价工具；不直接RMA |
| `NAN-HARDWARE` | 多轮Job的Allocation与异常节点集合；Rank→GPU UUID→Node；每Node参与失败/成功Job次数；跨Job重复错误日志；Known-good正确性结果；Xid/ECC/RAS/PCIe/NVLink；排除Node后的Job结果 | 必须声明7个候选是多轮异常节点**并集**；只有同一Physical Node跨不同Job/Logical Rank重复错误且排除后Job成功，才支持Node关联。Xid/ECC在NaN前出现是强旁证，为0不能排除SDC | 支持Node：重复错误日志+Physical Node一致+排除后成功。支持SDC候选还需Known-good输入在可疑GPU错、健康GPU对照通过。排除：故障随Batch/版本/Logical Rank。证据不足：只有并集或异常分 | Node级隔离仅在上述Node判定满足时；否则只隔离Job。无Known-answer/Reference不得写“已确认SDC” |
| `NAN-COLLECTIVE` | 各Rank Collective前后Tensor有限性/数学不变量；Process Group/Sequence/Op/Count/Shape/Dtype/Root；Flight Recorder Start/End；Rank退出/Xid；通信前后时间 | `pre-collective finite + contract一致 + post-collective first bad`支持通信/Rank输入分支；Sequence/Shape不一致支持Desync；某Rank输入先坏说明Collective只是传播 | 支持：明确通信前后边界或契约错误。排除：Local Forward Loss在任何Gradient Collective前已NaN；或输入Rank已先坏。证据不足：只看到所有Rank最终NaN/NCCL Timeout | 首错Rank/Job/Config级；有物理路径证据才扩大到Node/HCA；不要因多Rank NaN批量隔离Node |
| `NAN-OPTIMIZER` | Step N Loss/Gradient；Collective后Gradient；Unscale/Clip后Gradient；Found Inf/Skip Step；Parameter/Master Weight；Adam m/v；Optimizer Step ID；Restore状态；各Rank同步性 | `loss finite → gradient finite → optimizer state before finite → optimizer.step → parameter/m/v first non-finite → next-step loss NaN` | 支持：第一非有限边界明确在Optimizer Update。排除：Input/Activation/Gradient此前已坏；加载后State已坏转Checkpoint；Collective后先坏转Collective。证据不足：没有Step前后状态快照 | Job/Optimizer配置或软件版本级；若只随Physical GPU再转Hardware；不凭下一Step Loss NaN直接归Optimizer |

## Interpretation

1. Locate the earliest non-finite boundary, not the loudest final Loss NaN.
2. Prefer direct state/input/output evidence over GPU utilization correlation.
3. Separate Logical Rank/Data/Version from Physical GPU/Node using cross-attempt mapping.
4. A candidate union maximizes recall but is not actionable isolation.
5. Return several unresolved hypotheses when required evidence is absent; never force one class.

## Output

Return source/coverage/time alignment; exact Metric/query/artifact used; first non-finite stage; Job/Attempt→Rank→GPU→Node matrix; per-hypothesis support/exclusion/inconclusive result using the complete table rule; maximum safe isolation scope; trusted checkpoint; missing Reference/Target or state evidence; and direct handoff:

```text
passive evidence supports isolation/recovery
→ NEXT_SKILL=/job-recovery

passive evidence cannot distinguish but controlled replay can
→ NEXT_SKILL=/training-reproduction
→ include goal, candidate hypotheses, fixed context and decision required

RCA already proved after recovery
→ NEXT_SKILL=/rca-closeout
```
