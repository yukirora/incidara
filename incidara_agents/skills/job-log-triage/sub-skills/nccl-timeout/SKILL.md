---
name: job-log-triage-nccl-timeout
description: "NCCL/RCCL timeout log playbook with complete cause, symptom, keyword/order, scope/stage and decision rules per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# NCCL/RCCL timeout log playbook

## Trigger

Use when NCCL/RCCL warning/timeout appears, a job is cancelled after collective no-progress, or communication fails during initialization/training.

## Goal

Find the first rank/process event before peer timeouts and distinguish peer exit, desync, config, straggler, local GPU/link, path, congestion and shared control-plane candidates.

## Impact scope

| Observed impact | Priority start |
|---|---|
| Single Rank/Job | Control flow, OOM/Exit, DataLoader, single GPU/NIC, job config |
| Multiple Jobs same Node | GPU/NVLink/PCIe/HCA/Driver/Node environment |
| Same Rack/Rail multiple Jobs | Leaf/Switch/Link, congestion, shared config/topology |
| Independent Jobs same seconds | Fabric/SM/SA, Routing/GID, shared control plane/change |

One job log cannot prove shared scope; request cross-job evidence when needed.

## Failure stage

| Stage | Priority candidates |
|---|---|
| Communicator Init/Connection | Host/IP/Interface, port/firewall, version, HCA/GID/QP, Rank/World/Group config |
| First Collective | Call order, Count/Dtype/Root/Shape mismatch, missing Rank, Topology/Transport |
| Stable then sudden timeout | OOM/Exit, Kernel/Xid, Link flap, congestion, Switch/SM/SA, Data/Checkpoint straggler |
| Message-size/scale specific | Ring/Tree/Protocol, Channel/QP, cross-tier topology, Buffer/Load/Overlap |
| Upgrade/Expansion correlated | New Node/HCA/GID/Routing/Placement or Image/NCCL/CUDA/Driver/Firmware/Config |
| Teardown only | Cancellation, Exit cascade, Finalize/Destroy ordering; confirm training completed |

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `NCCL-PEER-EXIT` | Rank OOM/Signal/Traceback/Node loss/GPU Fatal before communication | One Rank fails first; Peers later Broken Pipe/NCCL Timeout/Heartbeat | `OOM/Killed/exit/signal/Traceback/Xid/node lost`→process ends→peer `watchdog/timeout` | Earlier Exit is primary candidate. Output first Rank/PID/GPU/Node/error/time; later Timeout is propagation |
| `NCCL-DESYNC` | Ranks call different Sequence/Op/Count/Shape/Dtype/Root or branch | Same Process Group but contract differs; one Rank has no matching Start | `sequence/op/count/shape/dtype/root` mismatch; Flight Recorder warning; conditional branch. Order: divergent call→wait/timeout | Support contract candidate; output Group and per-Rank contract. Network logs cannot explain mismatch |
| `NCCL-CONFIG` | Image/NCCL/CUDA/Driver/Group/Rank/HCA/GID/Dispatcher inconsistency | Init/first collective fails by node/cohort after upgrade/expansion | Env/config dumps, `NCCL_*`, Rank/World mismatch, interface/HCA/GID, version. Order: config load/init error→timeout | Output exact differing key/value/ranks/version. Logs support config cohort, not physical Node root |
| `NCCL-STRAGGLER` | Rank delayed by Data/Compute/Framework before collective | Peers start same Sequence; one Rank lacks Start and remains elsewhere | `DataLoader/queue/read/compile/kernel/hook` on missing Rank; Peers `collective start/wait`→timeout | Output late Rank's last stage/Input/Node. No Start means investigate before NCCL, not network first |
| `NCCL-LOCAL` | GPU/Xid/PCIe/NVLink/P2P/local collective fault | Local errors precede cross-node timeout; often fixed Node | `Xid/AER/PCIe/NVLink/P2P/CUDA error/GPU lost`→Rank stalls/exits→peer timeout | Incident-tied local Fatal supports Node candidate; output physical mapping and node-local test need |
| `NCCL-PATH` | HCA/GID/LID/QP/CQ/Port/Link/Path failure | Communicator contract consistent; Verbs/RDMA error precedes timeout | `IBV_WC_RETRY_EXC_ERR/retry exceeded/ACK timeout/CQ/QP/GID/LID/port down`→collective fail | Output Rank→GPU→HCA→Peer/Path and time. Log alone cannot distinguish HCA/Cable/Switch |
| `NCCL-CONGESTION` | Aggregate concurrent traffic overload without a single broken pair | Idle runs succeed; timeout/slow collective aligns with many groups/jobs | Load/concurrency/job overlap plus Retry/CNP/Pause if logged; no stable Fatal pair | Logs alone provide candidate only. Output Group/Message/Concurrency/time and request measured load/path evidence |
| `NCCL-CONTROL` | Shared Fabric SM/SA/Routing/GID or broad change destabilizes paths | Independent Jobs fail same seconds; physical counters may be clean | `heavy sweep/invalid LID/SetResp/dead end path/osm_sa/osm_vendor` or routing/change events before multi-job failures | Support shared control-plane candidate only with multi-job scope and temporal evidence; output affected Jobs/domain/change |

## Interpretation

Order across ranks and jobs is mandatory. Earlier OOM/Exit/Xid outranks peer timeout; contract mismatch outranks network tuning; cancellation/watchdog is termination. Broader supported scope raises shared dependencies earlier.

## Output

Return Impact Scope/evidence limit, Failure Stage/recent change, last healthy Step, first error Rank/Node/time, collective identity/contract, propagation, every table hypothesis result, requested passive evidence, checkpoint and direct handoff.
