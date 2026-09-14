---
name: job-log-triage-job-hang
description: "Executable Job Hang log playbook. Each hypothesis row defines cause, typical symptom, log keywords/order, decision gates, evidence output, and safe scope."
allowed-tools: Bash Read Grep Glob
---

# Job Hang log-triage playbook

## Trigger

Use when scheduler/processes remain alive but completed steps stop, a job is cancelled/timed out after no progress, or the user asks why training hung.

## Goal

Confirm real no-progress, find the last healthy step and earliest log event, locate the wait stage, and map logs to actionable hypotheses without declaring a physical root cause from a peer timeout.

## Scope and progress

```text
IMPACT_SCOPE: single-rank | single-job | same-node-multi-job | rack/rail | multi-independent-job | unknown
FAILURE_STAGE: initialization | first-collective | steady-state | data | checkpoint/restore | teardown | unknown
expected_step = last_completed_step + elapsed_since_last_step / healthy_step_time
```

Worker/rank logs are authoritative when driver logs are buffered. Log mtime is not progress.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `HANG-OBS` | Driver日志截断、转发/采集器卡住、TensorBoard桥缺口 | 主日志停止但Worker Step、Token或Checkpoint继续；Scheduler/Kernel仍有训练活动 | `completed step`在Worker继续；主日志无后续；Exporter/forwarding warning。顺序：主日志停止→权威进度继续 | 支持即排除训练Hang，只修观测。输出冲突的进度源、最后一致Step和缺失范围，不隔离Job/Node |
| `HANG-PHASE` | 合法Compile、初始化、Checkpoint、Evaluation、Profiler写回 | 无Step但日志明确处于命名阶段；耗时可能仍在同阶段健康预算 | `compile/compilation`、`checkpoint/save/restore`、`evaluation`、`profiler`、`initializing`。顺序：进入阶段→无错误→尚未超预算 | 支持需阶段名和开始时间；超过预算则不能停在此类，输出阶段耗时和同阶段对照请求 |
| `HANG-INIT` | Communicator/Rendezvous初始化竞态、配置/连接问题、首个Barrier等待 | Step 0前所有或部分Rank到Barrier/Init后安静，无Fatal日志 | `rendezvous/store/barrier/init communicator/NCCL init`；对比各Rank最后一行。顺序：部分/全部到Init→无Step | 支持只到初始化阶段；有配置/RDMA错误转对应假设。输出未到达Rank、最后状态、配置/通信证据需求 |
| `HANG-COLLECTIVE` | Rank在同一Collective等待，触发原因仍可能是Desync、慢Rank、GPU或网络 | 所有Rank停在同一Step；Stack/日志指向ProcessGroup/NCCL/RCCL；无明显Exit | `allreduce/allgather/reduce-scatter/alltoall/ProcessGroupNCCL/watchdog`。顺序：最后健康Step→Collective Start/Wait→无End→Timeout | 支持“等待位置”而非根因。输出Process Group、Sequence/Op/Shape/Start/End和缺失Rank需求 |
| `HANG-NETWORK` | HCA/GID/QP/CQ/Link/Path、共享Fabric或拥塞先触发通信停顿 | Verbs/RDMA错误早于Step停止；可能多Job同窗；Rank契约看起来一致 | `IBV_WC_RETRY_EXC_ERR`、`retry exceeded`、`ACK timeout`、`CQ/QP`、`GID/LID`、`link down`。顺序：网络错误→Collective停→Peer Timeout | 支持网络候选但日志不能定物理范围。输出错误Node/HCA/Peer/时间、跨Job范围和系统证据请求 |
| `HANG-STRAGGLER` | Data/Compute/Framework慢Rank未进入Collective | 一个Rank没有Collective Start，仍输出Data/Kernel/User路径；其他Rank等待 | `DataLoader/queue/read`、长Kernel/Compile、用户Hook；对比各RankCollective Start。顺序：慢Rank停在通信前→Peers进入并等待 | 支持慢Rank候选；最后Timeout Rank不是首错。输出未进入Rank、其最后阶段/Input/Node |
| `HANG-NODE` | Rank进程退出、Host OOM、GPU/Xid/PCIe、Node失联 | 某Rank日志截断/退出或Node活动消失，Peers随后Hang/Timeout | `Killed/OOM/exit/signal/node lost/Xid/AER/PCIe/GPU lost`。顺序：本Rank Fatal→进程/Node消失→Peer wait | 显式Fatal可支持进程/Node候选；无物理证据只到Job。输出Rank/PID/GPU/Node、首错和Kernel/BMC需求 |
| `HANG-DATA` | 坏Batch/Shard、DataLoader死锁、Queue为空、Storage读等待 | Child Stack/日志停在Loader/Queue/File Read；多Rank可能同等等待 | `DataLoader.__next__`、`queue.get`、`read/open`、`dataset/shard/sample`、`D-state`。顺序：Data请求→无返回→Step停止 | 支持Data/Storage层，日志不能区分Input与Endpoint。输出Batch/Shard、等待调用、Storage Path和Host列表 |
| `HANG-FRAMEWORK` | 用户Hook/Lock、Framework Rendezvous、Runtime死锁或版本回归 | Stack在Futex/Lock/Hook/Store；系统Fatal证据缺失；可能随版本出现 | `futex/lock/wait/hook/rendezvous/store/runtime`、Traceback前后版本。顺序：进入公共/用户路径→线程不再推进 | 支持Job/版本候选；输出调用栈、User/Framework边界、Image/Commit和健康版本需求 |
| `HANG-CHECKPOINT` | Artifact/Writer/Storage/Restore等待 | 最后活动是Write/Fsync/Rename/Commit/Restore；Checkpoint无完成标记 | `checkpoint`、`write/fsync/rename/manifest/commit/restore`、文件系统错误。顺序：Checkpoint开始→部分Worker完成/等待→Step停止 | 支持Checkpoint层；输出所有Worker结果、Artifact/Writer/Endpoint和最后可信Checkpoint |
| `HANG-PLACEMENT` | Rank/Node/HCA拓扑或Scheduler Policy造成只在某Allocation复发 | 新Allocation后Hang；无明确低层Fatal；历史上随Placement变化 | `nodelist/rank mapping/topology/placement/scheduler/policy`和最近Allocation。日志通常只有相关性 | 日志不能单独支持Policy根因。输出当前/健康Placement、低层已排除项和系统证据/对照需求 |
| `HANG-HEARTBEAT` | Heartbeat本身误杀，或长时间Hang后Heartbeat通道再失效 | Heartbeat错误醒目，但Step可能早已停止；也可能训练一直正常到被杀 | `heartbeat timeout/unhealthy tasks/tasks crashed/watchdog`。顺序：若no-progress早于Heartbeat则它是终止机制；若进度/功耗正常到Heartbeat则保留控制面候选 | 输出Heartbeat时间、最后进度时间和之前30–60分钟证据需求；不能只加Timeout结束RCA |

## Interpretation

First event that explains later messages wins candidate priority. Same last Step across ranks proves a common boundary, not an RCCL root cause. Cancellation, watchdog and peer timeout after no-progress are propagation/termination.

## Output

Return scope/stage, progress projection, authoritative sources, last healthy step, wait point, first log event and propagation, every hypothesis decision from the table, affected Rank/PID/GPU/Node, last trusted checkpoint, exact passive metrics/artifacts already named, gaps and handoff to `/system-evidence-diagnosis` or job-only recovery.
