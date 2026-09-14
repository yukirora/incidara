---
name: training-reproduction-loss-nan
description: "Controlled Loss NaN replay. Each hypothesis row contains its entry condition, fixed context, one changed variable/control, experiment, decision rule, action, and proof limit; torch_xray is optional for operator localization."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# Loss NaN reproduction playbook

## Trigger

The completed upstream skill must supply goal `fast-isolation`, `deep-diagnosis`, or `validation`; precise NaN signature; last trusted checkpoint; failing Batch/Data Cursor and RNG when available; passive evidence/proof limit; and the action decision replay must change.

Zero hypothesis IDs are valid only for phenomenon capture with a named missing observation such as first non-finite Module/Rank. Do not replay when the failing state cannot be reconstructed or no result changes the action.

## Goal

Use one controlled comparison to determine whether the first bad value follows Data/Checkpoint, numerical settings, software Operator, Physical Hardware, Collective, or Optimizer, and map the result to a different isolation/RCA/validation action.

## Common fixed context

Keep fixed unless it is the one tested variable:

```text
Checkpoint and Parameter/Optimizer/Scaler state
Batch/Data Shard/Cursor and RNG
Model, Train/Eval mode and Dropout behavior
Precision/Autocast
Framework/Compiler/Kernel/Driver version
Microbatch, accumulation and Sequence Length
EP/TP/PP/DP groups, Rank placement and Collective order
Module invocation order, Input Shape/Dtype/Layout
```

A replay with a different first NaN stage is not equivalent.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| `NAN-DATA` | 被动证据显示故障随Batch/Shard，但缺少健康资源重放；候选为坏输入、预处理、Mask/Label/长度 | 固定Checkpoint/Seed/版本/资源配置。实验A：失败Batch在健康GPU；实验B：Known-good Batch在原资源。唯一变化为Input；需要时再单独切Preprocess版本 | 支持：失败Batch跨健康GPU产生相同第一输入/NaN，Known-good通过。排除：失败Batch在健康GPU通过而不同输入在固定GPU失败。证据不足：原Batch/Cursor不可恢复 | 支持后隔离Data/Job/Preprocess，不隔离Node。只能证明随输入，不能自动区分原始数据、预处理或Loss实现 |
| `NAN-CHECKPOINT` | Restore后首步异常且Checkpoint/兼容性仍不确定 | 固定健康资源、Batch、Seed、版本和并行配置。实验A：失败Checkpoint；实验B：前一可信Checkpoint。Forward前比较Parameter/Master Weight/Optimizer m/v/Scaler有限性 | 支持：只随失败Checkpoint出现且加载后状态先坏。排除：失败Checkpoint在健康资源通过、Known-good状态只在可疑GPU失败。证据不足：两Checkpoint并行映射不同且无法等价 | 隔离Checkpoint/Restore版本并回退。证明状态跟随性，不自动定位到Shard、Reshard或Optimizer加载代码 |
| `NAN-NUMERICAL` | 数值轨迹指向溢出/低精度/Scaler/Clip/高风险数学算子，但软件Operator与普通数学问题未区分 | 固定Checkpoint/Batch/RNG/资源/Microbatch/累积。每轮只改变一个：FP32、局部Autocast、Loss Scale、Clip顺序、一个超参数或一个Operator实现；使用原配置作对照 | 支持：第一NaN按数学预测随唯一变量消失/出现并跨健康GPU一致。排除：相同输入/配置只在固定GPU错，或Reference Input一致但Target Output首次偏离。一次FP32成功为证据不足 | 回滚数值配置/Batch/超参数。FP32同时改变Kernel，需局部A/B后才能给具体根因 |
| `NAN-OPERATOR` | 已缩小到Module/Operator，但缺少相同Input的Reference/Target精度证据；候选为Framework/Compiler/Fused Kernel/版本 | 固定Module Input、Parameter、Shape/Dtype/Layout、Seed、调用顺序和硬件类型。实验A：Reference Backend/健康版本；实验B：Target Backend/可疑版本。环境已有兼容`torch_xray`时Dump同Rank/Step；再单独比较Fused/Unfused或版本 | 支持：Module Input匹配，Target Output第一次偏离/NaN，Reference通过；故障随版本/Backend。排除：Input已偏离或Known-good不同输入只随Physical GPU失败。Forward一致、Backward先坏表示当前工具未覆盖 | 阻断软件版本/Kernel候选并生成最小Operator Case。它定位边界，不直接指出Kernel内部指令；Backward/Optimizer需其他数值检查 |
| `NAN-HARDWARE` | 多轮Job故障与一个Physical Node重复日志相关，或SDC仍是候选；需要区分Node、软件和输入 | 固定Checkpoint/Batch/Seed/版本。二维交叉：失败现场跑健康GPU；Known-good正确性用例跑可疑GPU；必要时在不同Logical Rank重复。唯一变化为Physical Resource | 支持Node：错误跨不同输入/Logical Rank稳定随Physical Node，健康GPU通过；排除Node后Job成功。支持SDC候选还要求Known-answer/Reference错误。排除：问题随Batch/版本/Logical Rank。低概率一次成功为证据不足 | 支持后隔离Node/GPU继续硬件诊断；无Known-answer不能说确认SDC。Fatal证据已足够时跳过主动复现 |
| `NAN-COLLECTIVE` | 被动证据无法判断某Rank输入先坏、Collective契约错误还是通信后结果错误 | 固定Group/Sequence/Op/Count/Shape/Dtype/Root/Message/Rank Mapping。构造每Rank已知有限输入和可解析结果，分别检查Pre/Post Collective；一次只改变Rank输入、软件版本或物理路径中的一个 | 支持输入Rank：该Rank在Collective前先坏。支持Desync：契约不一致。支持Collective实现：输入/契约一致但Post结果相对解析解/Reference首次错误。若Local Forward Loss在Collective前已NaN则排除 | 隔离Job/Rank输入/版本；只有错误随Physical Path并有网络证据才隔离HCA/Path。网络CRC通常检测传输损坏，不能无证据声称静默网络错误 |
| `NAN-OPTIMIZER` | Loss、Forward和Collective后Gradient有限，但Optimizer边界缺少Step前后状态证据；候选为AMP顺序、Adam状态、Fused Kernel、分布式State或硬件 | 固定Checkpoint/Batch/RNG/Gradient/Optimizer State/Microbatch/累积。同步检查Step前后Parameter、Master Weight、m/v、Found Inf、Scale和各Rank Step ID；每轮只切Fused/Unfused、当前/健康版本或Physical GPU | 支持：Optimizer前全部有限，`optimizer.step()`后首次非有限；只随Fused/版本→软件，只随GPU且Known-good更新失败→硬件候选。排除：Gradient此前已坏；加载后State已坏→Checkpoint；Collective后先坏→Collective | 回滚Optimizer/AMP/版本或隔离有充分证据的硬件。下一Step Loss NaN本身不能证明Optimizer；异步CUDA检查需在调试重放边界同步 |

## torch_xray details for NAN-OPERATOR

`torch_xray` is not stock PyTorch. Check import/CLI availability and use the same compatible package in Reference and Target; do not install an unapproved package during an incident.

It can dump selected `nn.Module` parameters/inputs/outputs and compare H5 results with Cosine/RMSE/IsClose/Max Error, then generate an Operator test. It does not directly identify an internal GPU instruction and the documented Module path primarily covers Forward.

```python
from torch_xray import PrecisionDebugger

debugger = PrecisionDebugger(
    dump_path="dump-target",
    hook_name="dump",
    rank=[suspect_rank],
    step=[replay_step],
    model=target_module,
    dump_torch_api=False,
)
debugger.start()
```

```bash
summary target.h5 target-summary.txt
compare reference.h5 target.h5 result.csv
```

After narrowing the Module, when supported:

```python
from torch_xray import begin_dump, end_dump
begin_dump(X_DEBUG=0x102, X_DEDUP=True, X_DUMP_NUM=5)
output = target_module(input)
end_dump(clear_context=True)
```

```bash
jprof --cpu_init --blacklist --factory=load dump.json
pytest --detail_compare_path=target.csv proc_xxx/pytests/ --seed 42
summary_diff_check target.csv reference.csv result.csv
```

Exact commands/schema are version-specific. Do not use Cosine alone; check finite status, Max/Relative Error, RMSE/IsClose and Operator-specific tolerance. Full-model/all-rank Dump may create very large H5 and I/O overhead; run one Rank, one Step and a narrowed Module after recovery.

## Interpretation

```text
supported
→ predicted first bad boundary follows the changed condition and not the control

refuted
→ controlled evidence contradicts the hypothesis with sufficient exposure

inconclusive
→ replay differs from original, required state is missing, or low-rate exposure is insufficient

unsupported
→ safe compatible tool/resource/Reference does not exist
```

## Output

Return goal, hypothesis, fixed/changed conditions, exact execution, first bad boundary, Reference/Target results, action enabled and proof limit to `/training-reproduction`; its parent hands fast-isolation to recovery, deep diagnosis to closeout/evidence, and validation to recovery or closeout.
