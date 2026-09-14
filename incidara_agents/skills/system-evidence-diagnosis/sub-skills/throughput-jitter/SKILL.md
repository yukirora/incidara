---
name: system-evidence-throughput-jitter
description: "Throughput Jitter evidence playbook with complete metric pattern, repeatability rule, scope and proof limit per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Throughput Jitter system-evidence playbook

## Trigger

Use with slow-step list, period/magnitude and equal same-phase healthy windows.

## Goal

Match repeated slow-step windows to the earliest checkpoint/network/resource/hardware/profile/input/routing/placement event with sufficient exposure.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `JITTER-CHECKPOINT` | Checkpoint Step/Writer；I/O latency/queue/dirty/writeback；blocked/host memory；TGS | Multiple slow windows align with save/async tail and writer nodes | Repeated alignment+healthy checkpoint control supports。Spikes outside checkpoint weaken | Expected within budget or Checkpoint/Storage config scope |
| `JITTER-NETWORK` | Per-window RDMA Retry/ACK/CQ/Port；Collective time；Path mapping；TGS | Network Delta precedes each slow Step; phased recovery follows Path/Node | Repeated temporal/object match supports。Only after spike or TCP-only weakens | Node/HCA/Path candidate；needs active Path comparison |
| `JITTER-LEAK` | Thread/communicator/process；Memory/PSI；I/O；Fresh/Restore；equal runtime exposure | Resource monotonically accumulates before widening P99/CV | Equal-exposure growth only under target lifecycle supports | Runtime/Restore/Feature version |
| `JITTER-HARDWARE` | Xid/RAS/PCIe/Clock/Power/Temp；Node mapping；event time | Physical event precedes slow window and repeats/follows fixed object | Incident-tied event supports；one post-spike counter or missing scrape insufficient | Node/GPU candidate only with physical evidence |
| `JITTER-PROFILER` | Profile/Compile events；trace write time；same-step healthy control | Spikes exactly follow capture/writeback/recompile and vanish when absent | Event alignment repeated supports expected overhead | Profiler/Compile config, no infrastructure isolation |
| `JITTER-INPUT` | Batch/Shard/Shape/Length；per-stage time；Resource mapping | Same input triggers slow Step across healthy resources | Follows input→support；follows resource with good input→exclude | Data/Job or Physical candidate after cross-check |
| `JITTER-ROUTING` | Tokens/Expert max/mean/CV；Overflow/Drop；Dispatch bytes；Expert time；TGS | Routing metric changes first on each slow Step | Repeated route→Expert/A2A→Step chain supports | Router/Input/Config |
| `JITTER-PLACEMENT` | Placement/Topology；concurrent jobs/load；standalone health | Jitter follows Allocation or overlap while config fixed | Placement vs load must be distinguished；only correlation is insufficient | Policy/Placement or noisy-neighbor load |
| `JITTER-EXPECTED` | Phase calendar；healthy same-phase P99/CV and sample count | Pattern within historical magnitude/period | Match supports expected；excess or new root metric refutes | No isolation; adjust accounting/alert |

## Interpretation

One spike or clean run is inconclusive. Counters use per-window delta; keep per-host dimensions. Exporter gaps/ghost data invalidate correlation.

## Output

Return window coverage, repeated matches/non-matches, sample/exposure sufficiency, earliest event per Slow Step, each hypothesis result, scope/gaps and handoff.
