---
name: minimal-reproduction
description: "Calculate the smallest feasible complete-node GPU allocation and settings for an already-selected MoE reproduction experiment. Runs five ordered Markdown calculation runbooks for group closure, memory, topology/load/exposure, resource selection, and pilot validation."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# minimal-reproduction

Calculate a minimum MoE reproduction plan only after the parent runbook has selected the hypothesis and experiment.

## Scope

```text
original model = MoE
original EP > 1, TP > 1, PP > 1
allocation unit = complete node, 8 GPUs by default
output = required GPUs, selected GPUs, concrete settings, fidelity, proof limit
```

Do not redefine the hypothesis, first anomaly, dependency scope, required fidelity, or reproduction target. Return `unsupported` when original group membership, state ownership, resources, topology, or required observation is unavailable.

## Required input

The parent must provide in natural language:

```text
first anomaly and propagation
required fidelity
required EP/TP/PP/CP/DP/EDP and full-state relations
properties that must remain equivalent
settings that cannot change and changes that are allowed
original model, parallelism, process groups, rank placement, and state ownership
memory mode and available memory evidence
resource limit and topology inventory
available launcher, benchmark, and observation method
safety, retry, and exposure limits
```

Do not proceed when a missing field could change the minimum.

## Ordered calculation runbooks

Read and execute these five files in order. Each file consumes the previous result; none chooses another hypothesis or experiment.

### 1. Communication-group closure and concrete settings

Read `references/01-group-closure.md`.

Start from the Rank, communication group, stage, or operator of the first anomaly. Repeatedly add every peer connected through all required relations until the Rank set no longer grows. Ask the installed framework to generate the resulting groups and reject non-isomorphic configurations.

Return the closed submesh, required communication-group GPU count, and concrete EP/TP/PP and placement settings.

### 2. Signal-constrained memory calculation

Read `references/02-memory-planning.md`.

For a non-memory fault, make the experiment fit with headroom without changing the protected group, shape, message, path, schedule, load, or first anomaly. For OOM, fragmentation, or memory stalls, reproduce the pressure/allocation signature rather than optimizing it away.

Return the limiting memory component, accepted adjustment, rejected adjustments, per-Rank/stage peak, and memory-required GPU count.

### 3. Topology, load, and exposure

Read `references/03-topology-load-exposure.md`.

Require both enough GPUs and the correct NVLink/HCA/Leaf/Rail/Rack relationship. Use measured or calibrated offered load, not Rank count as a proxy. Preserve enough cycles or trials for jitter and low-rate failures.

Return topology/load GPU lower bounds, the required placement predicate, and observation exposure.

### 4. Fidelity and resource selection

Read `references/04-resource-selection.md`.

Calculate exact and mechanism-equivalent minima separately, round to complete nodes, and apply the fixed allocation, maximum budget, minimum floor, or available set supplied by the parent.

Return required GPUs, selected GPUs, slack, fidelity, and concrete settings.

### 5. Pilot validation

Read `references/05-pilot-validation.md`.

Build framework groups, initialize model/state, measure the maximum-memory Rank/stage, run warmup, and verify group, shape, tokens, message, path, memory, load, and exposure before entering the trigger window.

If a derived property differs, return to the one calculation stage responsible for it. Do not launch a broad setting sweep.

## Stop conditions

Return `partial`, `inconclusive`, or `unsupported` rather than silently changing:

```text
the first anomaly
the required communication-group relationship
expert/tensor/GEMM shape
tokens per expert or collective message
the target physical path or offered load
the memory-pressure signature
the required observation exposure
```

## Output

Answer in natural language with these headings:

```text
Status and requested fidelity
Reproduction properties that must remain equivalent
Settings that stayed fixed and changes that were allowed

Communication-group closure
Group-required GPU count and generated parallel settings

Memory mode and limiting component
Accepted and rejected memory adjustments
Maximum per-Rank/stage memory and memory-required GPU count

Required topology, path, load, and exposure

Exact minimum
Mechanism-equivalent minimum
Selected GPU count and unused slack
Selected GPU type, nodes, Rank placement, and all concrete settings

Pilot measurements
Verified derived properties
Remaining proof limit
```

Return the plan to `training-reproduction`; the parent runs the approved experiment and interprets its result.
