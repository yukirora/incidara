# Signal-constrained Memory Planning Runbook

## Enforce the Signal Contract while solving memory

Memory is not an independent optimizer. Every proposed memory adjustment must first pass:

```text
Does this setting change any Signal Contract field?
├─ yes → setting is locked; reject the adjustment
└─ no  → setting is allowed; evaluate its memory effect
```

## 2.1 Choose the memory mode from the parent spec

### `fit-with-headroom`

Use when the target failure is not a memory failure. The reproduction must fit without changing group, shape, message, path, schedule, load, first anomaly, or other locked targets.

Examples: EP All-to-All timeout, expert kernel error, network path issue, framework deadlock.

### `reproduce-memory-pressure`

Use when the target is OOM, fragmentation, allocation-order, memory-induced stall, or memory-related jitter. Memory behavior is part of the Signal Contract and must not be optimized away.

The parent must supply as available:

```text
rank/stage where pressure begins
allocated/reserved/peak/usable VRAM
failing allocation request or OOM point
allocation order and lifetime
activation/workspace/communication peaks
fragmentation or headroom ratio
first memory-related anomaly
```

The plan must match the requested pressure/failure signature, for example:

```text
peak_to_usable_ratio target
same failing allocation size/order
same max-memory rank/stage
same OOM/fragmentation first anomaly
```

A plan that merely makes the workload fit does not reproduce a memory failure.

## 2.2 Decompose memory by ownership and behavior

State components:

```text
routed expert weights/gradients/optimizer/master
shared expert state
attention/dense/router/embedding/output state
```

Runtime components:

```text
activations/recompute live set
router dispatch/combine buffers
expert GEMM workspace
collective buffers
framework/compiler temporary buffers
fragmentation and reserve
```

For every rank/stage `r`:

```text
M_state[r]
= Σ_c(
    P_weight[c,r]    × bytes_weight[c]
  + P_grad[c,r]      × bytes_grad[c]
  + P_optimizer[c,r] × bytes_optimizer[c]
  + P_master[c,r]    × bytes_master[c]
)

M_peak[r]
= M_state[r]
+ M_activation[r]
+ M_dispatch[r]
+ M_expert_workspace[r]
+ M_collective[r]
+ M_temp[r]
+ M_fragmentation[r]
```

Use `max_r(M_peak[r])`, not aggregate state/GPU count.

For each GPU SKU:

```text
M_usable
= physical VRAM
- runtime reserve
- communication reserve
- operational headroom
```

## 2.3 Record how each setting would change both memory and targets

Before applying a setting, produce an impact check.

### EP

Memory effect: changes local routed-expert ownership.

Also changes: experts/rank, EP peer count/group, routing placement, All-to-All behavior. Therefore locked for EP peer/message/router targets.

### ETP/TP

Memory effect: shards expert/shared tensors and may reduce some state/workspace.

Also changes: expert shard, GEMM M/N/K, TP collective, EP subgroup/message. Therefore locked for expert kernel or EP+TP targets.

### PP/layer layout

Memory effect: distributes resident layers/state and activations by stage.

Also changes: stage ownership, pipeline neighbors, microbatch schedule/timing, checkpoint ownership. Therefore locked for EP+PP/full-pipeline targets.

### CP

Memory effect: reduces context activation per rank.

Also changes: context partition, attention shape, CP collective and EP+CP order. Therefore locked for long-context targets.

### DP/EDP and FSDP/ZeRO/distributed optimizer

Memory effect: pure replication reduction does not necessarily reduce per-rank memory; state-sharding strategies reduce selected weights/gradients/optimizer states.

Also changes: replica count, gradient/parameter communication, concurrent EP groups and checkpoint writers. Therefore locked when those are targets.

### Microbatch/sequence/packing/input

Memory effect: changes activation, dispatch buffer and expert workspace.

Also changes: tokens/EP group, tokens/expert skew, message bytes/fragmentation and GEMM M dimension. Therefore locked for router/message/GEMM/context targets unless another setting reproduces all derived values and requested fidelity permits it.

### Remat

Memory effect: lowers activation live set.

Also changes: backward compute/schedule and sometimes overlap. Reject for timing/kernel/jitter targets when that change matters.

### Precision/quantization/offload/kernel/backend

Memory effect: can substantially reduce state/workspace.

Also changes kernels, message bytes, transfer timing, numerical behavior and allocation order. Use only for a Signal Contract explicitly orthogonal to those properties and downgrade fidelity as required.

Store every rejected option in `REJECTED_MEMORY_ADJUSTMENTS` with the Signal Contract field it would violate.

## 2.4 Select the setting by limiting component, not a global priority

### Expert state exceeds capacity

Consider only allowed settings that reduce expert state:

```text
EP
ETP/expert TP
expert-state FSDP/ZeRO/EDP sharding
PP only if it moves equivalent MoE layers
```

### Shared/dense state exceeds capacity

```text
PP layer partition
shared-state FSDP/ZeRO
TP
```

### Optimizer state exceeds capacity

```text
distributed optimizer
ZeRO/FSDP over an allowed DP/EDP group
```

### Activation exceeds capacity

```text
microbatch
remat
CP
PP
TP
```

### Expert workspace/dispatch exceeds capacity

```text
microbatch/tokens per expert
capacity/padding
ETP/TP
expert kernel/backend
```

Filter each list through `allowed_changes` and the target-impact check before using it.

## 2.5 Calculate only the current component's required degree

For component `c` with shardable bytes `M_c` and capacity available to it after other fixed components `C_c`:

```text
S_c_required = ceil(M_c / C_c)
```

Round upward to the nearest framework-supported degree on an allowed setting, rebuild groups, and recalculate every rank/stage. This is constraint rounding, not a global setting sweep.

If no allowed setting reduces the limiting component without violating the Signal Contract:

```text
increase GPU count/SKU while keeping locked settings
or
return partial/unsupported at the requested fidelity
```

## 2.6 Accept memory according to mode

For `fit-with-headroom`:

```text
max_r(M_peak[r]) <= M_usable
and all Signal Contract fields remain matched
```

For `reproduce-memory-pressure`:

```text
memory target/error/pressure signature is matched
and non-memory Signal Contract fields remain matched
```

Derive `G_memory` from the smallest framework-valid group/stage setting satisfying the selected mode. Report the limiting component, applied setting, rejected settings, and fidelity effect.
