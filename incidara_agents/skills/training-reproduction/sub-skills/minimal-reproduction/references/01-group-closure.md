# Group Closure and Settings Runbook

## Settings versus targets

## Actual settings the planner may solve

```text
WORLD_SIZE / NNODES / NPROC_PER_NODE
EP / ETP / TP / PP / CP / DP / EDP
FSDP / ZeRO / distributed-optimizer sharding
expert count/placement and layer layout
micro/global batch, accumulation, sequence, packing, data
precision, optimizer, remat, offload
MoE dispatcher, expert kernel, NCCL/RCCL runtime knobs
nodelist, rank placement, GPU assignment, HCA/NIC binding
checkpoint writer/replica and concurrent-job/load settings
```

## Derived target properties to verify

```text
actual group/rank relationship and experts per rank
expert/tensor shard and GEMM shape
tokens per EP group/expert and skew
collective operation/order/frequency and bytes per peer
per-rank/stage peak memory
actual NVLink/HCA/fabric path
aggregate offered load and observation exposure
first anomaly and propagation
```

The planner changes settings, asks the framework/runtime to generate these properties, and compares them with targets. It never treats a target group, message, or path as a knob.

## Calculate the minimum Signal-Contract-required group-closed submesh

## 1.1 Build group relations

From production group dumps/config, represent each protected group type as a relation over logical ranks:

```text
EP relation
ETP/TP relation
PP stage/neighbor relation
CP relation
DP/EDP relation when required by the Signal Contract
checkpoint-writer relation when required by the Signal Contract
concurrent-group relation when required by the Signal Contract
```

Seed with the rank(s), communicator, stage, or operator involved in the first anomaly.

## 1.2 Compute closure, not a one-time union

```text
R = seed ranks
repeat:
  for every rank in R:
    add every peer connected through each Signal-Contract-required group relation
until R no longer grows

R_closed = R
G_group_raw = |R_closed|
```

This closure captures interactions across axes. A one-time union around one rank can underestimate an EP×TP×PP grid.

Construct an isomorphic target submesh; production rank IDs need not be reused, but group sizes, intersections, neighbor relations, and target placement must match.

## 1.3 Four dependency scopes

Assume production EP, TP, and PP are all enabled.

### EP-only: TP and PP may change without changing the Signal Contract

Lock settings that produce the target EP membership, experts/rank, router/token distribution, dispatcher/message, and first anomaly. TP/PP may be reduced only if pilot verification shows the target expert shard, GEMM, group and message remain equivalent.

### EP+TP: TP-generated properties are required; PP is not

Lock original/target EP, TP/ETP, expert dimensions, dtype/kernel, routing/batch settings, and rank order that produce the target expert shard/GEMM and EP/TP communication. PP may be reduced to a valid MoE-stage skeleton.

### EP+PP: PP-generated properties are required; TP is not

Lock EP, required PP stages/layer layout, pipeline microbatch/interleaving, and model/batch settings producing target stage tensors and EP-vs-PP ordering. TP may change only if target expert/message properties remain equivalent.

### EP+TP+PP: both TP- and PP-generated properties are required

Lock EP, TP/ETP, required PP chain/layout, expert/model shape, dtype/kernel, pipeline microbatch, rank placement, and runtime backends. Only unrelated DP/EDP, CP, repeated layers, data, and duration may change.

The case is supplied by the parent. If it is unknown, return `inconclusive`; do not infer it by silently changing TP/PP.

## 1.4 Convert target groups to settings

Set global rank-space knobs:

```text
WORLD_SIZE
NNODES = ceil(WORLD_SIZE / NPROC_PER_NODE)
NPROC_PER_NODE = 8 under the default allocation contract
rank order and rendezvous settings
```

Set group-producing knobs:

```text
expert-model-parallel-size / EP
expert-tensor-parallel-size / ETP
tensor-model-parallel-size / TP
pipeline-model-parallel-size / PP
pipeline layer layout and microbatch schedule
context-parallel-size / CP
state-sharding and DP/EDP-related settings
```

Set model/workload knobs that produce EP properties:

```text
num experts, top-k, capacity/drop/padding
expert placement and dimensions
microbatch, sequence/packing, input distribution
MoE dispatcher/backend and relevant collective settings
```

Set placement knobs that produce the path:

```text
nodelist/node selector
rank-to-node/GPU assignment
GPU-HCA/NIC binding
topology-aware placement constraints
```

Set checkpoint/concurrency knobs when required by the Signal Contract:

```text
checkpoint mode/sharding/writer replicas/interval
number of concurrent jobs/EP groups and background load
```

Concrete flag names are framework-specific. For example, Megatron commonly exposes `--expert-model-parallel-size`, `--expert-tensor-parallel-size`, `--tensor-model-parallel-size`, `--pipeline-model-parallel-size`, `--context-parallel-size`, `--num-experts`, `--moe-router-topk`, `--moe-token-dispatcher-type`, and batch/sequence flags. Resolve names from the installed framework; do not invent them.

Ask the actual framework group builder to generate EP/ETP/TP/PP/CP/DP/EDP/writer groups. Reject settings whose generated group graph is not isomorphic to the target required submesh.

Set:

```text
G_group = allocation_unit × ceil(G_group_raw / allocation_unit)
```
