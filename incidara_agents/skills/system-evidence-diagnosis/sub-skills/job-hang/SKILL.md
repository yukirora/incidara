---
name: system-evidence-job-hang
description: "Executable Job Hang evidence playbook. Each hypothesis row defines required metrics/sources, time/object pattern, support/refute rules, isolation scope, and proof limit."
allowed-tools: Bash Read Grep Glob
---

# Job Hang system-evidence playbook

## Trigger

Use after log triage confirms no completed-step/token/checkpoint progress beyond the healthy phase budget and supplies scope, stage, wait point and candidates.

## Goal

Map passive time-series, child stacks, communicator state, kernel, storage, topology and change facts to Hang hypotheses and the maximum safe isolation scope. No active replay here.

## Query rules

Use the TSDB sub-skill to verify Job/Attempt/hosts, map Step to time, and compare same-phase healthy/incident windows. Discover names/labels before queries:

```text
list_prometheus_metrics("gpu")
list_prometheus_metrics("dcgm")
list_prometheus_metrics("ib_")
list_prometheus_metrics("rdma")
list_prometheus_labels(<selected metric>)
```

Known LTP metrics when present include `gpu_utilization` or `dcgm_gpu_utilization`, `dcgm_power_usage`, `node_xid_error`, `ib_port_physical_state`, `increase(ib_port_rcv_errors[window])` and `increase(ib_port_xmit_discards[window])`. When discovered, query `rate(hw_rdma_tx_retx_pkts_total[5m])` and `rate(hw_rdma_tx_ack_timeout_total[5m])`; otherwise use available `ib_port_*` and persisted network evidence rather than inventing aliases. Empty data is a gap. Power uses the same-SKU idle/wait baseline and active-training baseline, never copied watt thresholds.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `HANG-OBS` | Driver/Worker/Raw TensorBoard Step；Checkpoint Commit；Exporter/Scrape freshness | Driver停但Worker/Raw Event/Checkpoint在同一时间后继续 | 支持：权威Progress继续。排除：所有权威源均停且覆盖完整。证据不足：Worker/Raw Event缺失 | 仅观测链路；不停止健康训练，不隔离Node |
| `HANG-PHASE` | Phase start/end；同模型/规模健康Phase时长；GPU/Host/Storage同阶段曲线 | 当前Compile/Checkpoint/Eval等耗时和行为处于健康分布，随后恢复 | 支持：命名Phase+健康对照匹配。排除：超预算且出现更早资源异常。缺同阶段基线则不确定 | 不隔离；修正Phase-aware告警或转资源Hypothesis |
| `HANG-INIT` | Rank到达Barrier/Init；Rendezvous/Store；Communicator状态；GPU/Network Fatal | Step 0前部分Rank未到或Communicator未完成；无更早Fatal时仅证明Init Stall | 支持阶段，不自动证明竞态。配置/Network错误转对应类。缺Rank到达证据则不确定 | Job级；需要短现象捕获时交复现，不随意隔离Node |
| `HANG-COLLECTIVE` | Child Stack；NCCL RAS Operation Count；Flight Recorder Sequence/Op/Count/Shape/Start/End；GPU Util+Power | Progress停+Collective Stack；高/部分Util但功耗接近同SKU等待基线兼容Polling | 支持等待机制；若无首个Rank/Path仍不能给触发根因。Progress继续则排除。只看Util不足 | Job/Communicator级；按缺失Rank、GPU、Network新证据转类 |
| `HANG-NETWORK` | Contract一致性；Rank→GPU→HCA→Path；RDMA Retry/ACK/CQ/Port Delta；跨Job/SM/SA事件 | 网络Delta在Progress停止前出现并映射到受影响Path；多Job同窗扩大Scope | 支持：契约一致+无更早Exit/Xid+Network先变。排除：Network变化在Hang后或Desync/Exit更早。Lifetime Total无效 | 映射到的Node/HCA/Path/Fabric域；范围不清时只Job并交复现二分 |
| `HANG-STRAGGLER` | Per-rank Collective Start；Child Stack；Data/Compute时间；Input；GPU Power/Clock | 一Rank在Data/Compute/Framework，Peers已进入Collective；慢Rank的活动先于Peer Wait | 支持：缺Collective Start+前置慢阶段。排除：它已同序进入且Network/GPU先错。无Per-rank证据则不确定 | Logical Input/Job或Physical Node候选；必须交叉后才隔离Node |
| `HANG-NODE` | Missing Process/Rank；Host OOM；`node_xid_error`；dmesg PCIe/NVLink；Node State；Power/Metric disappearance | Process/Node/Xid/OOM在Peer Wait前发生并映射同一对象 | 支持Node需Incident-tied Fatal。排除：Node仍健康且错误随Input/Version。Metric消失但Scrape也断仅是Gap | 有强物理证据时Node；否则Job级恢复到独立资源 |
| `HANG-DATA` | Child Stack；Batch/Shard；Storage latency/queue/throughput；Blocked/D-state；跨Job Endpoint | Rank先进入Loader/File Wait，Storage或Input异常早于Progress停止 | 支持Data层；同Shard跨健康资源失败→Data，同Endpoint多Job→Storage。无Input/Endpoint对照则不确定 | Data/Job或Storage Endpoint；不直接隔离GPU |
| `HANG-FRAMEWORK` | User/Framework Stack；Thread/Lock；Image/Commit；System evidence；健康版本 | 多Rank/固定路径停在Hook/Lock/Rendezvous且物理证据干净，随版本/控制流 | 支持Job/Version候选；被动相关通常不足Root Cause。更早GPU/Network/Data证据则排除 | Job或版本；需要最小路径/版本A-B时交复现 |
| `HANG-CHECKPOINT` | Every Worker outcome；Artifact/Manifest/Checksum/Commit；Writer placement；Storage/Host I/O；Phase baseline | Checkpoint开始后部分Writer/Endpoint异常，Progress停；或合法长写在健康预算 | 支持Artifact/Endpoint/Writer之一需对象跟随证据。只有时间重合则不确定 | Checkpoint/Job、Writer Node或Storage域；先保护最后可信Checkpoint |
| `HANG-PLACEMENT` | Current/healthy Rank Placement；GPU-HCA/Leaf/Rail；Scheduler Policy/Change；lower-layer controls | Compute/P2P/Pair/Storage均健康，故障仅随Placement/Policy | 被动证据通常只支持候选；若故障仍随Physical Node则排除Policy | 最多Policy/Placement候选；交固定与Scheduler Placement对照，不Cordon健康Node |
| `HANG-HEARTBEAT` | Last Progress；Heartbeat loss；GPU Util/Power；Process liveness；earlier Stack/Network | No-progress早于Heartbeat→Heartbeat是终止传播；正常训练持续到Heartbeat→控制面候选 | 按时间顺序判定。只看到最终Heartbeat日志为证据不足 | 按更早根因Scope；纯Heartbeat候选只到Job/Control Plane，不能靠调大Timeout修Hang |

## Interpretation

Locate the earliest relevant anomaly and propagation. Partial high-utilization GPUs show which devices entered a wait, not the origin. All metrics flat/disappearing with scrape failure is observability failure. If native evidence cannot identify first Rank, report that limit.

## Output

Return verified sources/windows, exact queries, per-Rank/GPU/HCA results, child/communicator state, normalized timeline, every hypothesis rule result, maximum isolation scope, gaps and direct handoff to recovery, reproduction with supplied goal, or closeout.
