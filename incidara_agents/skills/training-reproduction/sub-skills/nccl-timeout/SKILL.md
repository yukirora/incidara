---
name: training-reproduction-nccl-timeout
description: "Controlled NCCL/RCCL timeout replay with full entry, experiment, decision, action and proof rules per hypothesis."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# NCCL/RCCL timeout reproduction playbook

## Trigger

Use only with upstream goal, precise timeout/first-anomaly signature, communicator evidence, surviving hypotheses, unresolved isolation/RCA/validation decision and safety/resource budget.

## Goal

Capture a named missing communicator fact or use one controlled comparison to distinguish Job/Config/Node/HCA/Path/Load/Control-plane actions.

## Common fixed context

Preserve Process Group/Sequence/Op/Count/Shape/Dtype/Root, Message, Rank Mapping, Image/Version, HCA binding, Path, concurrency/load and observation window whenever they carry the failure.

## Experiments and hypothesis decisions

| Hypothesis | 进入条件与候选原因 | 固定条件、唯一变化项与实验 | 判定标准 | 动作与证明边界 |
|---|---|---|---|---|
| No hypothesis | Timeout精确但缺First Rank/Contract/Path；已有RAS/Flight Recorder/Stack能补字段 | Settings不变，只增加已有捕获，一个有界重放 | 复现且获得命名字段→回Diagnosis；只复现Timeout无新证据→Inconclusive | 不产生Root Cause/Node隔离 |
| `NCCL-PEER-EXIT` | 早期Exit/OOM可能随Workload或Physical Resource | A失败Input/Operator跑健康资源；B Known-good负载跑可疑资源；分开改变Input/Resource | 随Workload→Job/Data/Config；随Resource且Known-good失败→Node | Fatal证据已足够则跳过复现 |
| `NCCL-DESYNC` | Contract差异已发现，需要确认代码/配置 | 固定Resource/Input/Group Size；只切Control Flow/Group/Shape配置到Known-good | Corrected设置下所有Rank Contract一致且Progress恢复，原设置重复Mismatch→支持 | 隔离Commit/Config；不测/隔离Network |
| `NCCL-CONFIG` | 某Image/Runtime/Rank/HCA/GID设置不一致 | 固定Workload/Resource；每轮只规范一个Key或比较当前/健康版本 | Timeout随具体Key/版本出现且Normalized Control通过→支持 | Block Config/Image cohort；仍随Node则转Physical |
| `NCCL-STRAGGLER` | Rank在通信前晚到，逻辑与物理未分 | 失败Shard/Operator在健康Resource；Known-good负载在可疑Resource；同Phase/Seed | 随Input/Operator→Workload；随Resource→Node；两边不区分→Inconclusive | 对应Job/Data或Node动作 |
| `NCCL-LOCAL` | Node-local GPU/PCIe/NVLink候选且Fatal不足 | Quarantine后相同Known-good Compute/Memory/P2P/NVLink/local Collective跑可疑/健康Node；唯一变量Resource | 控制测试只在可疑Node失败且失败Workload在健康GPU通过→支持 | 隔离Node/local Link；不扩到Fabric |
| `NCCL-PATH` | Contract一致、local pass、Path证据先出但范围未定 | 固定Message/Op/Load；Node-local→CUDA-buffer RDMA Pair→same-message two-node Collective→Group/Half/Pair；每轮只换Group/Path | Failure稳定随Pair/HCA/Path，Healthy Path通过→支持；签名变化/仅总量异常→不确定 | 隔离最小Node/HCA/Port/Path |
| `NCCL-CONGESTION` | Idle测试通过，只在并发负载下失败 | 固定Workload/Group/Path/Message；只开关/分级Background或Concurrent EP Load，测实际Offered Load | Retry/Latency/Timeout和Load重复同现同消→支持；Idle Pair也失败→转Path | Traffic/Fabric domain/Admission；不RMA单Node |
| `NCCL-CONTROL` | 多Job同窗且SM/SA/Routing/Change候选 | 先Historical/Change对照；仅在隔离Fabric重放一个GID/Routing/Control变更，禁止生产注入 | Shared failures跟Control Change出现/消失→支持；固定Pair独立失败→Path | Block/Rollback Change/Fabric domain；证明不自动定位某个Cable |

## Minimal reproduction

Use `../minimal-reproduction` only after a row selects an experiment and required communicator graph/message/topology/load exceeds available complete nodes.

## Interpretation

`Supported` requires the original first anomaly and control behavior. One successful Retry does not clear intermittent hardware/network. A partial Group cannot refute a larger EP/TP/PP interaction.

## Output

Return goal/mode, hypothesis/gap, fixed/changed context, exact command/resource, expected/actual first anomaly, status, action and proof limit to the reproduction parent.
