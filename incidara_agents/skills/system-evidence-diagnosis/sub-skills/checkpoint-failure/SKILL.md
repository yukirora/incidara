---
name: system-evidence-checkpoint-failure
description: "Checkpoint evidence playbook with complete artifact/storage/host pattern, decision, scope and proof rules per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Checkpoint failure system-evidence playbook

## Trigger

Use with checkpoint ID/Step, Worker outcomes, first file operation/error, writer placement and candidates.

## Goal

Determine what failure follows and identify the newest safe recovery point without confusing expected checkpoint load with infrastructure fault.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `CKPT-ARTIFACT` | All Shards/size；Manifest/Checksum/Commit/temp dirs；reader validation；Worker outcomes | Same artifact invalid on healthy/local storage; Known-good artifact works | Support if integrity fails independent of endpoint. Exclude if same artifact passes local/healthy and endpoint fails good artifact | Quarantine artifact/Job; fall back previous checkpoint |
| `CKPT-ENDPOINT` | Storage logs；latency/throughput/queue/errors；fsync/rename/metadata；same artifact local/remote | Endpoint metric/error precedes failure; multiple operations/jobs may share | Support with endpoint-following cross evidence. Exclude if artifact fails everywhere. Time overlap only→Gap | Storage Endpoint/domain; protect jobs/checkpoints |
| `CKPT-WRITER` | Writer/Shard/Replica assignment；per-worker success；same payload/endpoint | Failure follows Writer config/Rank placement while other writers succeed | Support writer follow; exclude if every writer/endpoint same | Writer/Config or mapped Node candidate |
| `CKPT-LOAD` | Payload bytes；writer concurrency；other jobs；I/O/blocked/dirty/writeback；successful windows | Endpoint healthy low load; latency/error appears only with calibrated production concurrency | Support load co-movement repeated. Fixed single writer fail→endpoint/writer | Shared storage load/admission; no RMA single Node |
| `CKPT-HEADNODE` | Head/service role；extra processes/I/O/Memory；writer placement；non-head control | Only head writer shows extra pressure and failure | Support requires placement control or repeated head correlation. Endpoint-wide failure excludes | Head placement/service contention candidate |
| `CKPT-RESTORE` | Fresh/Restore TGS；threads/communicators/memory；state integrity；equal exposure | Persistent resource/TGS delta begins at Restore only | Support Fresh-vs-Restore; exclude if Fresh same or follows Node/version independent | Restore implementation/version; not checkpoint content alone |

## Interpretation

Checkpoint TCP retransmit/I/O pressure can be expected under writes; compare previous successful same-size/concurrency checkpoints. Empty metrics do not prove healthy storage.

## Output

Return artifact integrity, endpoint/writer/load/head/restore pattern results, newest safe checkpoint, data-loss risk, scope, gaps and handoff to recovery or reproduction.
