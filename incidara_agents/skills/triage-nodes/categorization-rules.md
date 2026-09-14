# Categorization Rules

Single source of truth for **classifying** nodes by alert pattern.

This file contains ONLY the matching rules — given alertname + summary, what is the expected reason.
For **how to investigate** each issue type, see `investigation-methodology.md`.
Investigation may change the target status if evidence contradicts the expected reason.

**For `triaged_hardware` nodes:** Categorization is already done. The existing reason from `get_node_history` tells you what was previously identified. Skip matching and go directly to investigation — verify the issue is still present.

## Matching

For each node, collect `{alertname, summary}` pairs from its alert window. Match against rules top-to-bottom. First match wins.

Every matched rule requires investigation. The `Reason` column is the expected reason if investigation confirms — it is not a guaranteed outcome.

---

## Rules

### A. `NotifyUnvalidatedNodes` alert → Validation Failures

| Rule | Summary pattern | Expected reason | Investigation branch |
|------|----------------|-----------------|---------------------|
| A1 | `gpu-copy-bw:*_by_dma` | `PCIeBandwidthDegradation` | Validation + PCIe/GPU |
| A2 | `gpu-copy-bw:*_by_sm` | `PCIeBandwidthDegradation` | Validation + PCIe/GPU |
| A3 | `nccl-bw:*nvlink-sharp` | `NVLinkFailure` | Validation + NVLink |
| A4 | `ib-loopback` | `IBAbnormal` | Validation + IB |
| A5 | `undefined` or empty | — | Validation + full deep dive |

### B. `PaiServicePodNotReady` or `PaiServicePodNotRunning` alert → Pod Issues

| Rule | Summary pattern | Node type | Expected reason | Investigation branch |
|------|----------------|-----------|-----------------|---------------------|
| B1 | `job-exporter-*` | `*-storage-*` | `JobExporterPortConflict` | Pod + port check |
| B2 | `job-exporter-*` | GPU node | — | Pod + GPU/CDI check |
| B3 | `cilium-*` | any | — | Pod |
| B4 | `coredns-*` | any | — | Pod |
| B5 | other | any | — | Pod |

### C. Other alerts → Hardware / System Faults

| Rule | Alert | Summary pattern | Expected reason | Investigation branch |
|------|-------|----------------|-----------------|---------------------|
| C1 | `NvidiaSmiLatencyTooLarge` | * | `NvidiaSmiLatencyTooLarge` | nvidia-smi check |
| C2 | `Unknown` | `fabric manager` or `NVSwitch` | `FabricManagerMismatch` | fabricmanager + NVSwitch |
| C3 | `NodeCrash` | * | `NodeCrash` | NodeCrash methodology |
| C4 | `GPUUnhealthy` | `ContainerMayFailDueToGpuDeviceEccError` | `GPUUnhealthy` | GPU ECC check |

### D. Recurrence after recovery

| Rule | Alert pattern | Expected reason | Investigation branch |
|------|--------------|-----------------|---------------------|
| D1 | Only `NodeUnschedulable` + `RecoverValidatedNodes` (no failure-specific alert) | — | Full deep dive |

Note: This pattern indicates a node previously triaged and repaired, but cordoned again without a diagnostic alert. The root cause is unknown — requires investigation, not assumption.

### Z. Catch-all

| Rule | Condition | Investigation branch |
|------|-----------|---------------------|
| Z1 | No alerts in window | Full deep dive |
| Z2 | No rule matched | Full deep dive |

---

## Confirmed Patterns

### NVLink Failure via Containerd Shim (confirmed 2026-04-16)

On b300/B300 nodes with no alerts in triage window (Z1), direct available→cordon:
- If job-exporter is in RunContainerError or ContainerCreating with `context canceled` / `context deadline exceeded` from containerd
- AND nvidia-smi hangs OR nvidia-smi shows 0 GPUs
- AND dmesg shows `NVRM: knvlinkUpdatePostRxDetectLinkMask_IMPL: Failed to update Rx Detect Link mask!` / `knvlinkDiscoverPostRxDetLinks_GH100: Getting peer0's postRxDetLinkMask failed!`
- → Expected reason: `NVLinkFailure`

First seen: rack b7r402 (6 x b300 nodes), Apr 5-6 2026.

### Validation Job Stuck WAITING — Device Plugin State Corruption (confirmed 2026-04-27)

When a validation job is found via `get_validation_job` in state `WAITING` with `FailedScheduling` events, do NOT resubmit the validation job — the new one will fail scheduling for the same reason. Investigate the device plugin state on the node first.

`FailedScheduling` reasons that indicate Layer 3 issues (not hardware):
- `Insufficient nvidia.com/gpu` with `nvidia-smi` showing all GPUs present → nvidia-device-plugin lost kubelet registration or stale container holds GPUs
- `Insufficient rdma/hca` with IB ports Active → rdma-shared-dp has orphaned allocation in kubelet checkpoint or plugin de-registered
- `node.kubernetes.io/unreachable` taint → kubelet stopped heartbeating (check kubelet service + PKI certs)

Root causes and classification:
| Root Cause | How to detect | Reason |
|------------|--------------|--------|
| Stale job container running, holds all GPU + RDMA slots | `crictl ps` shows non-system container, `kubectl get pod` returns NotFound | `ZombieJobPod` |
| nvidia-device-plugin abandoned socket (nvidia.com/gpu=0 in k8s, smi=8) | `fuser nvidia-gpu.sock` → no PID; `RegisteredDevices` missing nvidia.com/gpu | `StaleDevicePluginState` |
| Orphaned rdma/hca allocation in kubelet checkpoint | `PodDeviceEntries` has DeviceIDs={"-1": ...} for ghost pod; rdma/hca=0 in Allocatable | `StaleDevicePluginState` |
| Kubelet crash-loop, missing PKI certs | `systemctl status kubelet` → failed; cert files missing | `KubeletCertFailure` |

Pattern: When LTP stops jobs externally without proper pod cleanup, kubelet checkpoint retains `PodDeviceEntry` with `DeviceIDs: {"-1": [...]}` for the deleted pod. This blocks the device plugin from re-allocating those devices to new pods. Fix requires restarting the device plugin pod (or force-killing the stale container for ZombieJobPod).

First seen: 4 nodes in rack b7r202/b7r401/b7r402 (m03u16, p03u16, c02u06, e06u06), Apr 23-27 2026.

---

## Pre-Revalidation Gate

**Before sending any node to `validating`**, confirm all three layers are healthy:

1. **Layer 1 — physical**: `probe_ssh` shows all GPUs, NVLink up, IB Active, no Xid/AER in dmesg
2. **Layer 2 — kubelet**: `systemctl status kubelet` is active/running, no cert failures
3. **Layer 3 — k8s plane**: `kubectl describe node` shows correct Allocatable counts, no stale containers, no orphaned device allocations

Only if all three pass → target `validating`. Any failure → target `triaged_platform` (or `triaged_hardware` if Layer 1 fails).

---

## Updating

When a new pattern is discovered:
1. Query existing reasons via `db-query get_existing_reasons.sql` — reuse if one fits
2. Add a rule row to the matching group (A/B/C) or create a new group
3. Add the investigation branch to `investigation-methodology.md`
4. Future runs will auto-match without re-investigation
