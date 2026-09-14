# Topology, Load, and Exposure Runbook

## Apply topology, load, and exposure constraints

## 3.1 Topology

Calculate a count lower bound:

```text
G_topology = U × minimum nodes required by the target domain relationship
```

Also require a predicate:

```text
topology_matches(
  selected nodes,
  rank placement,
  GPU-HCA binding,
  required NVLink/leaf/rail/rack/pod relationship
) == true
```

A sufficient count with the wrong topology is invalid.

## 3.2 Load

For aggregate EP traffic:

```text
Offered Load
≈ concurrent EP groups
 × bytes per group per step
 × dispatch frequency
```

`G_load` is the minimum allocation known to produce the target measured load under available topology/background-load capability. If no existing mechanism can produce it, return `partial/unsupported`; do not infer it from rank count alone.

## 3.3 Exposure

For jitter/low-rate failures, preserve equal trial count, cycles, or GPU-hour exposure. One clean run is inconclusive.
