---
name: job-log-triage-throughput-jitter
description: "Throughput Jitter log playbook with cause, symptom, log pattern and decision output per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Throughput Jitter log-triage playbook

## Trigger

Use when Step P99/CV or slow-step frequency increases while median may remain healthy.

## Goal

Build exact slow-step windows/period and map repeated log patterns to checkpoint, network, leak, hardware, profiler, input, routing, placement or expected-phase candidates.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `JITTER-CHECKPOINT` | Periodic save/async write/writer contention | Slow Steps align every Checkpoint period and return | `checkpoint/save/commit` at same N-Step→Step spike | Output period/writers/duration; alignment supports candidate not fault severity |
| `JITTER-NETWORK` | Intermittent Path/RDMA/congestion | Phased slow windows, communication warnings/recovery | `NCCL/RDMA/retry/ACK/CQ/link` before each slow window | Output affected Ranks/Nodes/windows/Message; log cannot define Path alone |
| `JITTER-LEAK` | Thread/communicator/memory/I/O accumulation | Step time gradually widens with runtime/Restore cycles | `thread/communicator/memory/GC/blocked` count warnings grow→slower Steps | Output Fresh/Restore, exposure and resource candidate |
| `JITTER-HARDWARE` | One-time/recurrent thermal/RAS/PCIe/GPU event | Isolated spike or repeated fixed Node association | `Xid/AER/NVLink/throttle/clock/temp`→slow Step | Physical candidate only when event precedes and maps Node |
| `JITTER-PROFILER` | Profiler capture/writeback or recompilation | Spikes at configured Profile Steps or Compile events | `profiler/trace/writeback/compile/recompile`→spike | Named event/time match supports expected tool cost |
| `JITTER-INPUT` | Fixed Batch/Shard/Shape causes variable work | Same input identity repeats slow Step across nodes | `batch/shard/sample/shape/length`→slow Step | Output input IDs/Seed; no data identity→Gap |
| `JITTER-ROUTING` | MoE Tokens/Expert/Overflow variable | Slow Steps coincide with routing skew/Expert warnings | `router/tokens expert/overflow/drop/load balance`→A2A/Step spike | Output Layer/Expert/Input/Rank and metric need |
| `JITTER-PLACEMENT` | Scheduler/Noisy Neighbor/concurrent load | Jitter appears on certain Allocation or overlapping Job | `placement/nodelist/policy/concurrent job` changes→spikes | Logs yield candidate; output current/control Placement/load |
| `JITTER-EXPECTED` | Evaluation/maintenance/known phase | Period/magnitude matches planned event | `evaluation/maintenance/checkpoint` and healthy baseline | Support expected only with same-phase history |

## Interpretation

Extract all per-step TGS/time, P50/P95/P99/Std/CV and Slow Steps. Exclude warmup and named phases before classifying. Persistent lower plateau is Slowdown.

## Output

Return slow-step list, period/magnitude, phase exclusions, per-hypothesis log result, minimum exposure/cycles and requested passive evidence.
