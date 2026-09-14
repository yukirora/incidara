---
name: system-evidence-nccl-timeout
description: "NCCL/RCCL evidence playbook with complete metric/source, temporal/object pattern, decision rule, isolation scope and proof limit per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# NCCL/RCCL timeout system-evidence playbook

## Trigger

Use after NCCL log triage supplies Impact Scope, Failure Stage, first error/Rank, communicator identity, candidates and requested objects.

## Goal

Use passive communicator, stack, GPU/local-link, RDMA/path/load, cross-job and control-plane evidence to support/exclude hypotheses and select the maximum safe isolation scope.

## Query rules

Repeated NCCL RAS/Flight Recorder observations are required; one snapshot mismatch can be normal arrival skew. Use incident-window `rate`/`increase` for counters, preserve Rank→PID→GPU→Node→HCA→Path, and query TSDB identity/coverage first.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `NCCL-PEER-EXIT` | Per-rank logs/process state；Host OOM；Xid/PCIe/Node loss；RAS missing Rank；Step/timeout time | One Rank OOM/Exit/Fatal→RAS/Process missing→Peers later Timeout | 支持：退出/Fatal先于Peer错误并映射同一Rank。排除：Rank仍活且Contract/Path先错。缺首错日志则不确定 | Job/Config or incident-tied Node；Abort whole Communicator and protect Checkpoint |
| `NCCL-DESYNC` | Flight Recorder Group/Sequence/Op/Count/Shape/Dtype/Root/Start/End；RAS operation counts；Rank control state | Different Rank contracts or missing Start persist across aligned snapshots; Network may be clean | 支持：明确契约差异。排除：所有Rank同Contract且更早Exit/Local/Path错误。短暂Count差但持续增长为正常偏斜 | Job/Code/Process Group config；不隔离Network |
| `NCCL-CONFIG` | Image/NCCL/CUDA/Driver versions；`NCCL_*` env；Rank/World/Group；Interface/HCA/GID；Placement | Failure cohort aligns with one mismatched key after Init/Upgrade; physical counters clean | 支持：exact key/value differs and failure follows cohort. 排除：normalized config still follows Physical Node/Path. 只有版本相关为不充分 | Version/Config cohort；阻断/回滚，不RMA Node |
| `NCCL-STRAGGLER` | Missing Collective Start；Child Stack；per-rank Data/Compute time；GPU Power/Clock；Input/Shard | One Rank remains in Data/Compute/Framework before Peers wait; no network/local Fatal | 支持：late Rank has no Start and earlier stage evidence. 排除：all Start same and Path/Local event first. No per-rank history is Gap | Logical Input/Job or Node candidate；交叉验证后才隔离Node |
| `NCCL-LOCAL` | Node-local P2P/NCCL evidence；Xid/ECC/AER/PCIe/NVLink；GPU state/power；Rank mapping | Local error/test failure precedes cross-node timeout on fixed Node | 支持Node：incident-tied physical error or local control failure. 排除：local healthy and failure follows cross-node Path/Config | Node/GPU/local Link；强Fatal直接隔离，证据不足只Job |
| `NCCL-PATH` | Contract/local health；RDMA Retry/ACK/CQ/QP/Port state；Pair/Path mapping；Switch counters | Incident-window RDMA delta on mapped Path before Timeout; fixed Pair/Path recurrence | 支持：local pass+contract一致+Path event first. 排除：counter only after Timeout, Lifetime Total, Desync/Exit first. Missing mapping→Gap | Smallest proven Node/HCA/Port/Link/Path；范围不清交复现二分 |
| `NCCL-CONGESTION` | Idle and loaded windows；concurrent EP Groups；Message bytes/frequency；RDMA/CNP/Pause/queue；cross-job TGS | Idle Pair/Group healthy; latency/retry/timeout appears only with measured concurrent load and recovers when load falls | 支持：load is sole condition with repeated correlation. 排除：fixed Pair fails idle or Job Desync. Rank count alone is not load | Shared traffic/Fabric domain or admission policy；不直接RMA单Node |
| `NCCL-CONTROL` | Independent Job timeline；SM/SA/Routing logs；Heavy Sweep；invalid LID/SetResp/dead-end path；GID/change inventory；physical counters | Multiple independent Jobs fail same seconds; control-plane/change event first; link counters can stay clean | 支持：shared scope+control event+recent change. 排除：one Pair/Node only or control event after failures. Without cross-job clock alignment→Gap | Fabric/SM/SA/Routing/GID/change domain；block change and recover Jobs on safe domain |

## Interpretation

RAS OOB reachability does not prove RDMA data-plane health. All ranks showing the same status may still be stuck together. The last timeout Rank can be a victim. Report several survivors when passive facts cannot distinguish.

## Output

Return verified scope/stage, communicator and per-rank contract, stack/process/GPU/path/load/control-plane timeline, every hypothesis table result, isolation scope, checkpoint, gaps and direct handoff to recovery, reproduction with supplied goal, or closeout.
