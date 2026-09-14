---
name: job-log-triage-checkpoint-failure
description: "Checkpoint failure log playbook with cause, symptom, keyword/order and decision output per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Checkpoint failure log-triage playbook

## Trigger

Use when save/finalize/read/restore errors, times out or stops training.

## Goal

Determine first failing Worker/phase, whether data is intact, progress at risk, and artifact/endpoint/writer/load/head-node/restore candidates.

## Log patterns and hypothesis decisions

| Hypothesis | 候选原因 | 典型现象 | Log关键字与先后Pattern | 日志层判定与完整输出 |
|---|---|---|---|---|
| `CKPT-ARTIFACT` | Missing/corrupt Shard/Manifest/Checksum/Commit, partial checkpoint | Same artifact fails independent of endpoint; directory incomplete/temp | `shard/manifest/checksum/commit/corrupt/missing key/ENOENT`；all Worker outcomes | Output artifact ID/path, complete/missing shards and last trusted checkpoint; one Worker error not proof all corrupt |
| `CKPT-ENDPOINT` | Storage read/write/fsync/rename/metadata/NFS/FUSE endpoint fault | Errors follow path/endpoint; multiple jobs/writers may fail | `I/O error/timeout/fsync/rename/metadata/NFS/FUSE/stale/lock`→checkpoint fail | Output endpoint, operation, first error/time/node; distinguish artifact later |
| `CKPT-WRITER` | Writer/Replica/Sharding assignment or implementation | Only specific Writer/Rank fails while others save | `writer/rank/shard/replica` plus per-worker `saved/failed` | Output successful/failed writers, placement and config; do not mark whole artifact corrupt before commit check |
| `CKPT-LOAD` | Writer concurrency/shared I/O load overwhelms storage | Duration grows or errors only at production concurrency | `blocked/timeout/retry/queue` with simultaneous writers/checkpoints | Logs provide load candidate; output writer count/payload/time and successful lower-load history |
| `CKPT-HEADNODE` | Head/service node has extra GCS/Prometheus/control I/O/Memory load | Only writer on head/service node fails repeatedly | Head hostname/role, extra services and checkpoint error align | Output writer role/Node and Host evidence need; correlation alone insufficient |
| `CKPT-RESTORE` | Restore creates leaked communicator/thread/memory or incompatible state | Restore succeeds but subsequent TGS/memory/thread behavior persistently differs from Fresh | `restore`→extra init/communicator/thread warnings→later Slowdown/OOM/Hang | Output Restore Step, Fresh comparison, version and persistent symptom; route to corresponding failure mode too |

## Interpretation

Order write→finalize→commit across all Workers. A finalization error may occur after data wrote successfully. Determine whether training data is lost or only report/finalization failed.

## Output

Return checkpoint Step/path, first error/Worker/Node, every Worker outcome, artifact completeness, duration/progress at risk, each hypothesis result, requested artifact/storage/host evidence and safest checkpoint.
