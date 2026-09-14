# Fidelity and Resource Selection Runbook

## Calculate minima by fidelity and apply resource constraints

## 4.1 Calculate separate minima

```text
G_raw(fidelity)
= max(
    G_group(fidelity),
    G_memory(fidelity),
    G_topology(fidelity),
    G_load(fidelity)
  )

G_required(fidelity)
= U × ceil(G_raw(fidelity) / U)
```

Report separately when meaningful:

```text
G_required_exact
G_required_mechanism_equivalent
```

A local-layer minimum must never replace the exact full-interaction minimum without a fidelity label.

## 4.2 Apply the caller's resource contract

Supported forms:

```text
fixed allocation: G_fixed
maximum budget: G <= G_cap
minimum floor: G >= G_floor
available set: G in {G1,G2,...}
```

Selection:

```text
fixed:
  if G_fixed >= G_required → sufficient; report slack
  else → lower fidelity or unsupported

maximum:
  choose smallest available G >= G_required and <= G_cap

minimum floor:
  choose smallest available G >= max(G_required, round_up(G_floor,U))

available set:
  choose minimum available G >= G_required whose SKU/topology passes
```

Report:

```text
G_required
G_selected
G_slack = G_selected - G_required
```

If supplied resources are already at/near `G_required`, preserve the highest-fidelity settings and avoid unnecessary model surgery. If all allocated GPUs must join `WORLD_SIZE`, use slack only through an Allowed Setting verified not to change target concurrency/load/collectives; otherwise keep slack outside the Signal-Contract-required communicator or use it for sequential controls.

If resources are below the required minimum, use the component-specific memory adjustment above and the following zero/low-fidelity-impact reductions where allowed:

```text
unrelated DP/EDP replicas
→ duration/data volume
→ unrelated repeated layers for local EP mechanisms
→ limiting-component-specific sharding/activation setting
→ last-resort lower-fidelity offload/freeze/quantization/model-shape change
```

Stop when the requested fidelity fits or when only locked settings/targets remain.
