---
name: job-log-triage-startup-scheduling
description: "Startup/scheduling log-event playbook with cause, symptom, keyword/order and decision output per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Startup and scheduling log-triage playbook

## Trigger

Use when a Job remains waiting, cannot be admitted/placed, or fails during Image pull, Mount, Init or distributed Barrier before training.

## Goal

Locate the exact startup stage, split specific/actionable and broad/normal signals, and determine user/config/resource/node/platform scope. These cases normally do not need training reproduction.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Event/Log关键字与先后Pattern | 判定与动作 |
|---|---|---|---|---|
| `START-QUOTA` | Queue/Quota/Admission/resource shortage or waiting Job holds quota | No Pod placement; broad insufficient-resource counts; requested resource exceeds allocatable/guaranteed | `FailedScheduling/Insufficient cpu/memory/GPU/quota/admission/waiting queue`；submit→no admission/placement | Compare request, VC quota, running/waiting consumers and unhealthy capacity. Normal pressure→explain/queue；policy inconsistency→admin |
| `START-PLACEMENT` | Selector/Taint/Affinity/Gang/Topology/fragmentation prevents feasible group | Total GPUs appear enough but no complete feasible placement | `didn't match selector/untolerated taint/affinity/PodGroup/gang/not feasible/topology` | Split each scheduler reason; prove exact constraint. Policy/fragmentation scope, not Node failure unless specific Node evidence |
| `START-IMAGE` | Wrong Image/tag, registry/auth/network, pull timeout, local disk pressure | Pod placed but Container remains Pulling/ImagePullBackOff | `ErrImagePull/ImagePullBackOff/manifest unknown/unauthorized/pull timeout/no space` | Missing image/tag→user/config；registry/shared→platform；single Node disk→Node platform action |
| `START-MOUNT` | Volume/Secret/Config/NFS/FUSE/CSI mount failure | Pod placed, Init blocked before user process | `FailedMount/MountVolume/CSI/NFS/FUSE/stale/permission denied` | Map volume and endpoint; shared failures→Storage；single stale Node mount→Node platform |
| `START-INIT` | Init Container/runtime/pre-command/package/config failure | Container/Init starts and exits before training | `Init:Error/CrashLoopBackOff/pre-command/apt/import/config/permission` plus exit/traceback | First Init error determines user/Image/platform scope; do not diagnose GPU training |
| `START-BARRIER` | Missing role/Pod, partial placement, rendezvous/barrier mismatch | Some tasks start, others absent; no training Step | `barrier/rendezvous/waiting peers/missing rank/taskrole` after partial scheduling | Determine which role never started and why; route to quota/placement/image/mount or config |
| `START-NODE` | Node NotReady, Disk/Memory/PID pressure, Kubelet/Runtime failure | Specific relevant Node excluded/evicts/fails start | `NotReady/DiskPressure/MemoryPressure/PIDPressure/kubelet/runtime/evicted` | Persisted event must overlap Job and requested SKU. Current live Node state cannot prove old failure |

## Interpretation

A never-running Job is blocked by the earliest startup failure. Broad scheduler messages require capacity context; specific Node/container signals require mapped investigation. One message can contain several comma-separated causes.

## Output

Return stage, first event, request versus capacity, specific/broad signals, every hypothesis decision, user/system boundary, required passive evidence and direct reply/recovery/system-evidence handoff.
