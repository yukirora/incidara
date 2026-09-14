---
name: system-evidence-process-crash
description: "Process crash evidence playbook with complete source pattern, decision, isolation scope and proof rule per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Process crash system-evidence playbook

## Trigger

Use with first exiting Rank/PID/Node, signal/code, crash phase, core/scheduler references and candidates.

## Goal

Determine whether the first death follows user input/code, race/lifecycle, host OOM, GPU/Node or scheduler; otherwise preserve Unknown.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `CRASH-USER` | Full traceback；Input/Operator/Commit；healthy-resource repeats；system clean evidence | Same exception/file/line/input across healthy resources, no earlier physical event | Support deterministic replay/log. Exclude if fixed Physical Node fails Known-good control | Job/Input/Commit；reply/fix, no Node action |
| `CRASH-RACE` | Core/native stack；thread/lifecycle/shutdown order；version；equal exposure；sanitizer when available | Low-rate crash follows concurrency/lifecycle and native frames; no deterministic input/physical mapping | Support needs repeated/equal-budget or core mechanism. One segfault is insufficient | Job/Version candidate；do not RMA without physical evidence |
| `CRASH-HOST-OOM` | Kernel OOM log/killed PID；cgroup limit；Host memory/PSI；process mapping | Host pressure/OOM-kill identifies target PID before exit137 and peer failures | Support exact PID/time. Exclude if GPU allocator OOM or scheduler kill | Job/Host resource config；Node only for platform memory leak/condition |
| `CRASH-GPU` | Xid/ECC/AER/PCIe/NVLink/device loss；GPU/Node mapping；other jobs/controls | Physical event precedes crash on mapped GPU/Node and repeats/follows resource | Support incident-tied error/control. Exclude if same input/version across healthy nodes | Node/GPU/local Link with strong evidence |
| `CRASH-SCHEDULER` | Persisted scheduler/accounting/pod event；walltime/priority/eviction；last progress | Explicit preemption/walltime/eviction before process termination, no prior app failure | Support accounting event. Prior Hang/OOM makes scheduler termination secondary | Expected/no action or scheduler/policy scope |
| `CRASH-OBS` | Log/core/kernel/scheduler retention coverage；last timestamps；scrape/exporter | Sources end before cause and no retained evidence | Support only Unknown classification. Any recovered first event moves to other class | Job-level containment; record Evidence Gap, no Node isolation |

## Interpretation

Use historical persisted sources for old Jobs. Current SSH/dmesg cannot prove past state after reboot/other workloads.

## Output

Return identity/coverage, first crash/system event, core/scheduler/kernel facts, per-hypothesis result, maximum isolation scope, checkpoint, gaps and handoff.
