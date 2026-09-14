---
name: job-log-triage-process-crash
description: "Process crash/death log playbook with cause, symptom, keyword/order and decision output per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Process crash log-triage playbook

## Trigger

Use when Rank exits with Traceback/Signal/Segfault/SIGKILL, Node exits, or Job dies without summary.

## Goal

Identify first exiting process and preceding event; separate user/race/host OOM/GPU/scheduler/observability causes from peer cascades.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `CRASH-USER` | Deterministic exception/assert/illegal input/operator | Same input/path raises traceback across healthy resources | `Traceback/AssertionError/ValueError/illegal address/index` with file/line→exit | Output first traceback/Input/Commit/Rank; clear user exception supports Job scope |
| `CRASH-RACE` | Use-after-free, concurrency/shutdown/destructor/resource race | Low-rate SIGSEGV/SIGABRT around lifecycle; inconsistent stack | `SIGSEGV 139/SIGABRT 134/core dumped/double free/use after free`；often teardown/concurrency | Output signal/PID/core/phase/exposure; logs alone rarely prove race |
| `CRASH-HOST-OOM` | Linux OOM killer or cgroup memory limit | Bare `Killed`/exit137; process disappears; peers later timeout | `oom-kill/Out of memory/Killed process/exit 137/SIGKILL`→PID death→peer error | Host OOM candidate only with kernel/cgroup evidence request; GPU OOM is different |
| `CRASH-GPU` | Xid/PCIe/NVLink/Driver/device loss | GPU error precedes process exit; fixed Node/Rank | `Xid/AER/PCIe/NVLink/GPU lost/NVRM/CUDA device`→signal/exit | Incident-tied GPU log supports physical candidate; output mapping and Node evidence |
| `CRASH-SCHEDULER` | Preemption/walltime/eviction/external kill | Scheduler event/exit 143; no prior app Fatal; may be expected | `PREEMPTED/TIMEOUT/Evicted/PodExternalDeleted/scancel/SIGTERM 143` | If prior Hang/OOM exists, scheduler is kill mechanism. Output persisted event/reason/time |
| `CRASH-OBS` | Abrupt death with missing/truncated logs and no retained kernel/scheduler/core | No summary; last line cuts off; live state unavailable | `unknown death` is absence pattern, not keyword | Output all missing sources and last timestamp; remain Unknown, no physical blame |

## Interpretation

First exiting Rank and preceding cause take priority. Broken Pipe, NCCL Timeout, Heartbeat and exit cascades after it are propagation. Live current Node state cannot prove an old crash.

## Output

Return scope/stage, first PID/Rank/Node exit, signal/code, first preceding log, core reference, scheduler event, peer propagation, each hypothesis result, trusted checkpoint and passive evidence request.
