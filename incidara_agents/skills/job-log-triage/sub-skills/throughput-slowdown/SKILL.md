---
name: job-log-triage-throughput-slowdown
description: "Sustained Slowdown log playbook with cause, symptom, log pattern and decision output per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Sustained Slowdown log-triage playbook

## Trigger

Use when equivalent training enters a persistently lower TGS/mean/P50 plateau.

## Goal

Prove comparability, exclude expected phases, locate start Step/time and map the first log-level change to a candidate.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `SLOW-NETWORK` | Persistent RDMA/Collective degradation | Every steady Step/Collective becomes uniformly slower; communication warnings begin near plateau | `NCCL/RDMA/retry/ACK/CQ`、Collective duration。顺序：Network warning/duration↑→TGS lower plateau | Output Group/Message/Rank/Node/time; logs cannot prove Path |
| `SLOW-RESTORE` | Restore leaves extra Communicator/Thread/Resource | Fresh baseline normal; after Checkpoint Restore TGS becomes permanently lower | `restoring`、communicator init waves、thread/resource warnings→first resumed Steps slow | Output Restore Step/Checkpoint, Fresh control and resource evidence request |
| `SLOW-HOST` | CPU/Memory/Data/I/O contention or leak | Data wait/blocked time becomes persistent; GPU may idle | `DataLoader/read/write/blocked/GC/thread/OOM/pressure` before plateau | Output Host/Rank/wait stage; distinguish Data/Storage/Runtime later |
| `SLOW-HARDWARE` | Thermal/Clock/GPU/PCIe/Node degradation | One Rank repeatedly slow; Xid/clock/throttle/local warnings may precede | `Xid/AER/NVLink/throttle/clock/temperature` or fixed slow Rank→global lower TGS | Incident-tied physical log supports Node candidate; otherwise request evidence |
| `SLOW-EXPECTED` | Checkpoint/Eval/Compile/Profiler or workload change | Lower window aligns with named phase/config and later recovers or is non-comparable | `checkpoint/eval/compile/profiler` or Batch/Sequence/parallel change | If phase/config changed, do not call steady regression; output excluded Steps/settings |
| `SLOW-VERSION` | Framework/Image/Kernel/Config regression | Regression begins after version change across healthy nodes; system logs clean | Version/Commit/Image/Kernel change→first lower steady Steps | Output exact version delta; logs give cohort correlation, not mechanism |
| `SLOW-ROUTING` | MoE Expert imbalance/overflow/workspace | Load-balance/Tokens warning changes before TGS; some Expert/Rank slow | `router/load balance/tokens per expert/overflow/drop/expert`→Collective/Step slower | Output Layer/Expert/Rank/Input and routing metric need |
| `SLOW-PLACEMENT` | Rank topology/Policy causes exposed communication | Same config slower only on new Allocation/Placement | `nodelist/topology/rank mapping/HCA/placement/policy` change→plateau | Output current/healthy placement; no Node isolation from logs |

## Interpretation

Compare identical model/data/version, Sequence, Global/Microbatch, precision, parallelism, Image, nodes and Fresh/Restore state. Exclude warmup/checkpoint/eval/profile. Repeated recovery is Jitter, not Slowdown.

## Output

Return comparability, baseline/current TGS/P50, start Step/time, excluded windows, first log event, each hypothesis result, requested passive evidence and handoff.
