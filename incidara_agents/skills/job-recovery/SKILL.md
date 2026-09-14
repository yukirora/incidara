---
name: job-recovery
description: "Evidence-backed production isolation and job recovery. The only training-incident stage allowed to mutate job/node/resource state; captures bounded evidence, applies the smallest justified isolation, restores from a validated checkpoint, and verifies forward progress."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# job-recovery

Run only when the completed upstream major skill supplies an isolation/recovery goal, evidence, confidence/proof limit, safety boundary, and checkpoint candidate.

## Before mutation

Capture only evidence at risk of loss:

```text
authoritative worker/rank logs and first anomaly
current process/child stacks when safe
NCCL RAS/flight-recorder dump when available
incident TSDB window
kernel/Xid/PCIe and requested network/storage evidence
job/image/config/allocation/topology/change identity
```

Do not delay containment for unavailable evidence.

## Isolation scope

```text
job only
→ evidence proves only this attempt/workload is bad

version/config/data/checkpoint
→ failure follows the logical object across healthy resources

node/GPU/local link
→ incident-tied physical evidence precedes the job failure

HCA/path/fabric domain
→ mapped pair/path/cross-job evidence supports that domain

storage endpoint
→ multiple operations/jobs or cross-check follows the endpoint

scheduler policy/placement
→ resources pass and failure follows policy/placement
```

Never quarantine a node because it was the last timeout reporter. If evidence cannot identify a smaller object, isolate only the job and recover on disjoint known-good resources.

Use the platform's authoritative lifecycle action path and approval policy. Record action ID, object, reason, rollback, and post-condition.

## Abort and checkpoint

Abort the complete communicator/job when one-rank continuation is unsafe. Select the latest checkpoint with committed marker, complete shards, valid checksum/manifest, and compatible model/optimizer/parallel mapping. Fall back to the previous checkpoint when integrity is uncertain.

## Restore

Exclude only supported objects. Use a disjoint known-good allocation when the fault domain is unknown. Preserve version/config unless it is the supported suspect; do not change node, image, and workload simultaneously.

## Verify recovery

Require:

```text
all ranks complete rendezvous
forward/backward/collective progress
loss, learning rate, optimizer and step continuity
step time/TGS returns to healthy cohort when applicable
new checkpoint commits successfully
no recurrence during the required observation window
```

## Output Format

```text
## Job Recovery Result

Approved isolation scope and supporting evidence
Evidence captured before mutation
Actions and action IDs
Checkpoint selected and integrity result
Excluded objects and new allocation
Forward-progress/checkpoint verification
Side effects and rollback status
Recovery result and remaining evidence gap
NEXT_SKILL and HANDOFF_GOAL
```

## Handoff

Recovery success controls impact but does not prove root cause. Hand the accumulated incident context directly:

```text
recovered, root cause still open, passive evidence needs widening
→ NEXT_SKILL: /system-evidence-diagnosis
→ HANDOFF_GOAL: deep-diagnosis with completed isolation/recovery context and wider controls

recovered, passive evidence already defines the active RCA experiment
→ NEXT_SKILL: /training-reproduction
→ REPRODUCTION_GOAL: deep-diagnosis

new or changed recovery method needs regression
→ NEXT_SKILL: /training-reproduction
→ REPRODUCTION_GOAL: validation
→ VALIDATION_TARGET: recovery
→ pass original failure, recovery post-conditions, and healthy guards

recovered and RCA/fix proof is complete
→ NEXT_SKILL: /rca-closeout
→ HANDOFF_GOAL: persist outcome and knowledge

recovery failed or impact expands
→ NEXT_SKILL: human escalation / approved fallback
→ pass failed action, rollback, current impact, and remaining safe options
```

Append actions and recovery outcome to the incident context.
