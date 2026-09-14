---
name: training-reproduction-job-hang
description: "Controlled Job Hang replay. Each hypothesis row defines entry evidence, fixed/changed conditions, experiment/control, decision rule, action, and proof limit."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# Job Hang reproduction playbook

## Trigger

The upstream handoff must provide reproduction goal, confirmed no-progress signature, wait point/first anomaly if known, passive evidence, optional hypotheses, unresolved action decision, resources and safety budget.

## Goal

Capture a named missing observation when no hypothesis exists, or use one controlled experiment to distinguish Job/Data/Version/Node/Path/Storage/Placement actions. Never delay online containment for replay.

## Common fixed context

Preserve the target Step/Phase, Image/Config, Process Groups, Collective/Message, Rank Mapping, Input, Checkpoint, Path/Load and observation exposure whenever they carry the failure. One successful low-rate retry is inconclusive.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| No hypothesis | Hang定义精确但缺First Rank/Wait Point/Path；已有捕获能力能补指定字段 | Settings保持不变，只启用已有RAS/Flight Recorder/Child Stack/TSDB；一个有界重放 | Recurrence+拿到指定字段→回Diagnosis；只复现最终Hang但First Anomaly不同→不支持；无可捕获字段→Unsupported | 只能证明现象与新证据，不证明Root Cause |
| `HANG-OBS` | 权威进度源冲突 | 不运行训练实验；比较Driver、Worker、Raw Event和Checkpoint | Worker/Raw Event存在更晚Progress→支持观测问题 | 修观测，不隔离Job/Node |
| `HANG-PHASE` | 命名Phase可能正常 | 固定模型/规模/Phase，做同阶段历史对照 | 时长和系统行为匹配且恢复→正常Phase；超预算/首错不同→排除 | 无隔离；仅修告警预算 |
| `HANG-INIT` | Step 0前Init Stall、无Fatal证据 | 同Node/Image/Config/Rank有界Retry，唯一变化为额外观测；不改并行/网络 | 同一Init首错重复→支持稳定Init问题；成功一次→低频原因仍不确定 | 可恢复Job；不能据一次成功清除Node嫌疑 |
| `HANG-COLLECTIVE` | Collective Wait确认但Trigger/First Rank缺失 | 同Communicator/Config/Input重放，开启现有RAS/Flight Recorder/Child Stack；只改观测 | 同Sequence Wait且捕获First Divergent Rank/Group→按新证据转类；只见Busy-wait→Trigger未知 | 未定位前只Job级 |
| `HANG-NETWORK` | Contract一致、无更早Exit/Xid、Network证据先于Hang但物理范围不清 | 固定Op/Message/Load；按原Group→Half→Pair二分；两个Half通过时恢复跨组Path/Peer/Concurrency；每轮只换Node Group/Path/Load之一 | Failure稳定随HCA/Path→物理网络；Idle通过只在Load失败→Congestion；签名变化→不等价 | 隔离最小Node/HCA/Path/Fabric域；无跟随性只Job |
| `HANG-STRAGGLER` | 一Rank通信前Data/Compute慢，逻辑与物理原因不清 | A失败Input/Operator跑健康资源；B Known-good负载跑可疑资源；分别改变Input和Resource | 随Input/Operator→Workload；随Resource→Node/GPU/Host；两边同时失败/通过→不确定 | 隔离Data/Job或Node，不能混换变量 |
| `HANG-NODE` | 缺Rank/Node但Fatal证据不足 | Quarantine后Known-good Compute/Memory/P2P跑可疑Node；失败Operator跑健康GPU | Known-good只在可疑Node失败且Workload在健康资源通过→支持Node | Fatal证据已足够则跳过复现；否则隔离Node并继续部件诊断 |
| `HANG-DATA` | Stack在Loader/File/Queue，Data与Storage/Node未分 | A失败Shard跑健康Node/Path；B健康/合成Data跑可疑Node/Endpoint | 随Shard→Data/Job；随Endpoint→Storage；Local也失败并随Node→Node | 两边不能区分时只恢复Job到独立资源 |
| `HANG-FRAMEWORK` | User Hook与Framework/Version未分，物理证据干净 | 先最小User Path vs Official Path固定Image/Resource；再同Input比较当前/健康Image；一轮只变Path或Version | 只User失败→Commit/Job；Official跨健康Node失败且随Version→Framework/Image；只随Node→Hardware | 阻断对应逻辑对象，不因框架Stack隔离Node |
| `HANG-CHECKPOINT` | Artifact、Endpoint、Writer/Load未分 | 验证Artifact；同Checkpoint跑健康/Local；Known-good同等Checkpoint走可疑Endpoint/Writer；分别改变Artifact/Endpoint/Load | 随Artifact→Checkpoint；随Endpoint→Storage；仅生产Writer Load失败→共享Load | 隔离Checkpoint/Storage/Writer配置；保护上一可信Checkpoint |
| `HANG-PLACEMENT` | Lower layers通过，故障只疑似随Allocation | 相同Workload/Image比较固定健康Placement与Scheduler Placement；必要时Compact/Spread或新旧Policy，一次一项 | 底层健康且只随Placement/Policy→支持；随Physical Node/Path→排除Policy | 回滚/阻断Policy，不Cordon健康Node |
| `HANG-HEARTBEAT` | Heartbeat可能是根因或先前Hang后的Kill机制 | 无需主动注入；比较同Job Heartbeat前Progress/Power/Stack与健康同阶段；必要时仅重放已知Heartbeat条件 | No-progress先发生→Heartbeat为传播；训练健康到Heartbeat→控制面候选 | 修更早Hang或Heartbeat机制；调Timeout不是Hang修复 |

## Minimal reproduction

Call `../minimal-reproduction` only after one row selects the experiment and production MoE Group/Shape/Path/Load cannot fit complete-node resources. It must preserve the row's protected signals.

## Interpretation

`Supported` requires the predicted first anomaly under the changed condition and not the control. Different first anomaly, insufficient exposure, or one clean low-rate run is `Inconclusive`; unavailable safe capability is `Unsupported`.

## Output

Return goal/mode, hypothesis or named gap, experiment/control, fixed/changed conditions, exact resources/settings, expected/actual first anomaly, result, action enabled and proof limit to the reproduction parent for direct handoff.
