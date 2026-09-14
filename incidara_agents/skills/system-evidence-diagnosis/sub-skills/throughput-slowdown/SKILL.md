---
name: system-evidence-throughput-slowdown
description: "Sustained Slowdown evidence playbook with complete metric pattern, rule, isolation scope and proof limit per hypothesis."
allowed-tools: Bash Read Grep Glob
---

# Sustained Slowdown system-evidence playbook

## Trigger

Use with equal healthy/regression steady-Step windows and fixed comparison settings from log triage.

## Goal

Find the first persistent cost before the lower TGS plateau and determine its logical/physical/version/placement scope.

## Queries and hypothesis decision table

| Hypothesis | 需要的Metric/证据 | 典型时空Pattern / Rule | 支持、排除与证据不足判定 | 最大隔离范围与立即动作 |
|---|---|---|---|---|
| `SLOW-NETWORK` | Per-step Collective time；RDMA Retry/ACK/CQ/Port Delta；Group/Message/Path；TGS | Network/Collective metric rises before and stays with lower plateau; follows Host/Path | 支持需window Delta+temporal lead+mapping。排除：Network clean or changes after TGS。Lifetime Total不足 | Path/Node candidate；需二分才具体隔离 |
| `SLOW-RESTORE` | Fresh/Restore TGS；thread/communicator/process count；Checkpoint time；equal exposure | Resource count and TGS diverge only after Restore and remain | 支持需Fresh control。排除：Fresh同样慢或随Node/Version independent of Restore | Restore/Runtime/Version；avoid restore path |
| `SLOW-HOST` | CPU Runnable/Blocked、PSI、Memory、Data wait、Storage latency/queue/dirty/writeback | Host/Data/I/O metric changes first and remains through plateau | 支持temporal chain and affected Rank。排除：Host clean, Compute/Network first | Host workload/Data/Storage scope；按对象隔离 |
| `SLOW-HARDWARE` | GPU Power/Clock/Temp/RAS/PCIe；per-rank Step/Kernel；Node history | Clock/Power/thermal/RAS or fixed Rank slows before global plateau | 支持Physical需同Node mapping/controls。排除：follows version/input | Node/GPU only with physical evidence |
| `SLOW-EXPECTED` | Phase markers；same-phase healthy duration/TGS；workload settings | Window matches expected phase or non-comparable setting and returns | 支持same-phase control。超预算/earlier anomaly排除 | No isolation；fix accounting/alert |
| `SLOW-VERSION` | Version/config change；system clean evidence；Compute/Communication/Idle profile | Regression follows version across healthy nodes; profile shows predicted stage cost | Passive evidence supports cohort；mechanism needs A-B/profile。随Node则排除 | Version/Image；block/rollback candidate |
| `SLOW-ROUTING` | Tokens/Expert、max/mean/CV、Overflow/Drop、Dispatch bytes、Expert Kernel、TGS | Routing metric changes before expert/Collective cost and plateau | 支持causal order+same Input condition。排除：routing stable, network/system first | Job/Router/Input/Config |
| `SLOW-PLACEMENT` | Current/healthy topology；Rank→GPU/HCA；Collective exposure；Policy change；standalone health | Standalone nodes healthy; regression follows Placement/Policy and exposed path | Passive correlation usually needs A-B。Failure following node/path excludes Policy | Placement/Policy candidate；不Cordon healthy nodes |

## Interpretation

Use identical queries/windows and find earliest persistent change. Ghost/stale metrics or missing hosts are gaps. Only after system evidence is clean should profiling attribute a Kernel.

## Output

Return baseline/regression windows, exact query, earliest metric, full chain, per-hypothesis result, scope, expected GPU-hour impact, gaps and handoff.
