# Investigation Methodology

How to investigate triaged nodes. Single source of truth for **tools** and **investigation logic**.
Used by the triage agent (Phase 1 Step 2) and the repair agent when deeper evidence is needed.

For **what each alert pattern classifies as**, see `categorization-rules.md`.

For **Xid error codes** (what they mean, hardware vs. app, immediate action), see `references/xid-reference.md`.

**Investigation determines the final target status.** The categorization rule gives an expected reason, but investigation may change the outcome — e.g., an A4 IB alert might turn out to be a driver issue → `triaged_platform`, or a B2 pod alert might reveal GPU hardware → `triaged_hardware`.

**For `triaged_hardware` nodes:** The existing reason from `get_node_history` tells you which branch to start at. Your goal is to **verify the issue is still present and check for recovery**. The investigation tools and logic are the same — just start at the known branch instead of matching alerts.

---

## Tools

### MCP — primary, auto-save evidence

#### node-ops MCP (role: diagnosis)

| Tool | What it returns | Key parameters & tips |
|------|----------------|----------------------|
| `get_nodes_by_status` | Nodes in a given status | Set `include_alerts=True` to get `validating_timestamp`, `triaged_timestamp`, `alert_count`, `alert_names`, `alert_summaries` per node. **These timestamps define the triage window** — use them as `start_ts`/`end_ts` in subsequent tool calls. |
| `get_node_detail` | Node hardware detail | Default `brief=True` omits ~200KB metainfo — use this. `ip` and `mgmt_ip` are JSON arrays; take first element for SSH IP / BMC IP. |
| `get_node_history` | Status actions with detail | **Use `start_ts`/`end_ts` from the triage window** (validating_timestamp → triaged_timestamp). Set `include_alerts=True` to see alerts alongside actions. `hours` and `days` are fallback defaults — explicit timestamps are always more precise. |
| `get_node_alerts` | Alert records for a node | **Use `start_ts`/`end_ts` from the triage window** — NOT `hours` from NOW(). If a node was triaged 5 days ago, `hours=72` misses the first 2 days of alerts. **Always set `alertname` when you know the type** — reduces 800+ rows to ~5. Key values: `NotifyUnvalidatedNodes`, `CordonValidationFailedNodes`, `NodeNotReady`, `PaiServicePodNotReady`. Default `deduplicate=True` groups identical alerts into one row with first/last seen + count. Set `include_details=True` only when you need the labels/annotations JSONB (3× larger). |
| `get_node_recent_jobs` | Jobs on this node | **Use `end_ts=triaged_timestamp`** to get jobs that ran BEFORE the node was triaged — otherwise you'll get post-triage jobs that are irrelevant. `limit=20` (default) controls how many jobs to return. Finds training jobs via `sourceHost`, NOT validation jobs (those have sourceHost = k8s control plane). |
| `get_validation_job` | Validation (superbench) job for this node | **This is how you find the validation job name.** `hostname` is the node hostname. Returns `job_name`, `user_name`, `job_state`, `completion_time`. Once you have the job name, use `get_job_details` or `ltp.sh logs` to inspect it. |
| `get_job_details` | Job state, exit code, completion phrase | `jobname` supports partial match (LIKE). Check `task_completion_phrase` for known hardware indicators. |
| `get_job_events` | Job scheduling/failure events | `job_name` is the human-readable job name (e.g. `superbench_uaback_auto_Xk95L9kE`) — NOT the internal frameworkName hash. Get the job name from `get_node_recent_jobs` or `get_validation_job` first. |
| `get_existing_reasons` | All reason values in DB | Use before picking a reason label — reuse existing values for consistency. |
| `probe_ssh` | SSH reachability + GPU/IB/NVLink/dmesg (uses sudo) | `ip` is required. Set `hostname` too — triggers auto-save. If unreachable, returns `"SSH unreachable: <ip>"`. |
| `check_fabricmanager` | Fabricmanager service status | Same as probe_ssh: set both `ip` and `hostname` for auto-save. |
| `move_node_status` | Transition node status | Execute phase fallback only (when `execute_triage.py` is unavailable). **Do NOT use for `validating` transitions** — `move_node_status` only writes to DB and does not submit a validation job. Use `execute_triage.py validate` for revalidation instead. |
| `delegate_to_agent` | Delegate node to repair agent | One-call: logs into Chat UI, creates session + task on target agent. Fire-and-forget. Use for `triaged_hardware` nodes confirmed for RMA. |
| `query_rma_cases` | Check existing RMA tickets | |
| `bmc_query` | BMC SEL/chassis/sensor/FRU via remote ipmitool | Does NOT need SSH to node — uses remote ipmitool. Get `bmc_ip` from `get_node_detail` → `mgmt_ip[0]`. Default command `sel_list` returns hardware event log — most useful for RMA. Use `sel_elist` with `since`/`until` for incident windows. Use `sel_info` to check SEL count/last clear time, `sel_time_get` to verify BMC clock, and `sel_get` + `record_id` for one raw record. Other commands: `chassis_status`, `chassis_poh`, `mc_watchdog_get`, `sensor_list`, `fru`. |
| `bmc_screenshot` | BMC KVM console screenshot | For crashed/unresponsive nodes. Shows kernel panic, BIOS errors, POST failures. Same `bmc_ip` source as bmc_query. |
| `bmc_health_log` | BMC Health Event Log via Redfish API | Structured hardware events: GPU not present, NVSwitch errors, NIC temp critical, PCIe errors, power events, LAN link down. Use `category="h200"` for Supermicro, `category="b300"` for AMI MegaRAC. Filter by `severity="Critical"` to find real hardware faults. Same `bmc_ip` source. Check BOTH this and `bmc_query sel_list` — they cover different event types (Redfish has GPU/NVSwitch/NIC; IPMI SEL has memory ECC, PCIe AER, thermal thresholds). |

#### agent-evidence MCP

| Tool | What it does | Key parameters & tips |
|------|-------------|----------------------|
| `save_evidence_tool` | Save ad-hoc evidence | For SSH commands only — MCP tool output auto-saves. `source` must be one of: `probe_ssh`, `nvidia_smi`, `dmesg`, `ib_stat`, `job_log`, `alert`, `nvlink`, `fabricmanager`, `job_list`, `job_detail`, `job_events`, `other`. Set `category` to the fault subsystem (`gpu`, `ib`, `nvlink`, `pcie`, `platform`, `unknown`). Add a `summary` line like "GPU 4: 42 uncorrectable ECC, Xid 79". `collected_by` is locked server-side — don't pass it. |
| `get_node_evidence_tool` | Retrieve saved evidence | Filter by `source` or `category` to check specific evidence. Use `collected_by="triage"` or `"repair"` to see what another agent already found. |
| `delete_node_evidence_tool` | Delete evidence for a node | Set `before` (ISO timestamp) to only delete older evidence. Without it, deletes ALL evidence for the node. |

### Scripts — fallback when MCP unavailable

| Script | Replaces | Notes |
|--------|----------|-------|
| `db_query.sh <sql_file>` | `get_nodes_by_status`, `get_node_alerts`, etc. | SQL files in `sub-skills/db-query/sql/`. Set `SSH_AUTH_SOCK` first. |
| `kubectl_check.sh` | No MCP equivalent | Requires SSH jump host. Use for pod/node k8s status. |
| `node_check.sh` | `probe_ssh` (partial) | |
| `ltp.sh logs <user>~<job>` | No MCP equivalent | LTP REST API; requires `LTP_HOST` + `LTP_TOKEN` env vars. |

---

## Core Principle

Every issue type requires investigation. The alert tells you the category; investigation must:
1. Collect **specific evidence values** — benchmark numbers (baseline, actual, variance), GPU indices, PCI addresses, IB device/port names, Xid codes, ECC counts
2. Collect **error log excerpts** — `sudo dmesg` lines (dmesg requires root), job stderr/stdout errors, UCX/NCCL error messages
3. **Verify the alert is current** (not stale)
4. **Check for co-occurring issues** that may change classification
5. **Confirm reachability** — SSH failure changes everything
6. **Auto-save via MCP tools.** For ad-hoc SSH: curate useful excerpts and save via `save_evidence_tool`.

**"OS-level hardware healthy" is not sufficient for revalidation.** A node must pass all three layers before being sent to `validating`:
- **Layer 1 — physical hardware**: `nvidia-smi` sees all GPUs, NVLink links up, IB ports Active, no Xid/AER in `sudo dmesg`
- **Layer 2 — OS/kubelet**: kubelet service running and heartbeating to k8s API, no cert/PKI failures
- **Layer 3 — k8s resource plane**: `kubectl describe node` shows correct `Allocatable` counts, no stale containers holding device slots, device plugins registered in kubelet checkpoint

Finding a problem at any layer drives classification independently of the other layers. A node with perfect `nvidia-smi` output but broken device plugin state is a **platform issue**, not hardware-healthy.

---

## Decision Tree

```
Step 1 (ALL nodes): Alert evidence
  → get_node_alerts — use start_ts/end_ts from the triage window,
    set alertname to the known type, use deduplicate=True
  → For validation failures: alertname="NotifyUnvalidatedNodes"
  → Extract: benchmark name, baseline/actual/variance, GPU index, timestamps

Step 2 (ALL nodes): Reachability + Layer 1 (physical hardware)
  → probe_ssh — set both ip and hostname for auto-save
  → Unreachable → triaged_hardware / NodeCrash (skip remaining steps)
  → Reachable: record GPU inventory, NVLink states, IB ports, kernel messages

Step 2b (ALL reachable nodes): Layer 2 + Layer 3 verification — MANDATORY
  This is not optional. "Looks healthy from probe_ssh" is NOT sufficient for revalidation.
  Run ALL checks below on every reachable node before branching.
  See "Step 2b: k8s-Plane Verification" section below for full details.

  ┌─ Layer 2: kubelet health [SSH node]
  │   systemctl status kubelet + cert file existence
  │   Degraded/crash-looping → triaged_platform / KubeletCertFailure or KubeletCrash
  │   Missing PKI certs → triaged_platform / KubeletCertFailure
  │
  └─ Layer 3: k8s resource plane [LTP host + SSH node]
      kubectl describe node <hostname>  [LTP host]
       → Check: Taints (unreachable:NoExecute?), Conditions (Ready?),
                Allocatable.nvidia.com/gpu, Allocatable.rdma/hca
      Compare nvidia-smi GPU count [SSH] vs Allocatable.nvidia.com/gpu [LTP host]:
       → Match → no device plugin issue at GPU level
       → Mismatch (smi=8, k8s=0) → nvidia-device-plugin broken → triaged_platform
      kubectl get pods --all-namespaces --field-selector spec.nodeName=<hostname>  [LTP host]
       → Non-system pod still Running → triaged_platform / ZombieJobPod
       → Check device plugin pod logs for gRPC/registration errors
      Kubelet checkpoint + socket files  [SSH node]
       → PodDeviceEntries with DeviceIDs {"-1": ...} for non-existent pod → orphaned alloc
       → RegisteredDevices missing nvidia.com/gpu or rdma/hca → plugin de-registered
       → Either → triaged_platform / StaleDevicePluginState

Step 3: Branch by issue type
  (Only reached after Steps 2 + 2b confirm no Layer 2/3 issues)

  ┌─ Validation failure (A1–A5) ── NotifyUnvalidatedNodes present
  │   3a: Validation job investigation (common trunk)
  │   3b: Targeted checks by fault subsystem
  │
  ├─ Pod issues (B1–B5) ── PaiServicePodNotReady / NotRunning
  │   3a: Pod investigation (common trunk)
  │   3b: Targeted checks by pod type
  │
  ├─ Hardware alerts (C1–C4) ── other hardware alertname
  │   Targeted checks per fault type
  │
  └─ Unknown (A5-unclear, D1, Z1, Z2) ── no clear direction
      Full deep dive
```

---

## Step 1: Alert Evidence (ALL nodes)

**Get the triage window first.** Phase 1 (`get_nodes_by_status` with `include_alerts=True`) returns `validating_timestamp` and `triaged_timestamp` for each node. These define the exact alert window — use them as `start_ts` and `end_ts`:

```
start_ts = node["validating_timestamp"]   # when the node entered validation
end_ts   = node["triaged_timestamp"]      # when it was cordoned/triaged
```

If `validating_timestamp` is null (node went directly to triaged_unknown), fall back to `triaged_timestamp - 7 days`.

Then call `get_node_alerts` with:
- `start_ts` / `end_ts` — the triage window boundaries
- `alertname` — set to the known alert type for focused results. For validation failures: `"NotifyUnvalidatedNodes"`. For pod issues: `"PaiServicePodNotReady"`. Leave empty only in the deep-dive branch where you don't know the type yet.
- `deduplicate=True` (default) — groups identical alerts into one row with first/last seen + count

**Why not `hours=72`?** Hours is relative to NOW(). If a node has been in `triaged_unknown` for 5 days, `hours=72` would miss the first 2 days of alerts in the triage window. Explicit timestamps are always more precise.

For `NotifyUnvalidatedNodes` alerts: extract the validation job name AND the benchmark details embedded in the summary:
- Benchmark name (e.g., `gpu-copy-bw:perf/cpu_to_gpu4_by_dma_under_numa0_bw`)
- Numeric values: baseline, actual, variance %
- GPU index or PCI address if present

If you need the full labels/annotations JSONB (e.g., to extract structured benchmark fields), set `include_details=True` — but expect 3× larger output.

---

## Step 2: Reachability + Layer 1 Physical Hardware (ALL nodes)

| Source | What to check |
|--------|---------------|
| `probe_ssh` | Pass both `ip` and `hostname` — `ip` is required for connection, `hostname` triggers auto-save. If reachable: returns GPU inventory (count, PCI addresses), NVLink link states, IB port states, recent kernel messages. |

**If unreachable** → node is degraded despite k8s Ready → `triaged_hardware / NodeCrash`. Check both `bmc_health_log` (Redfish structured events: GPU/NVSwitch/NIC) and `bmc_query` (command=`sel_list`) (IPMI SEL: memory ECC, PCIe AER, thermal) — they cover different event types. Also `bmc_screenshot` to see console state. Skip remaining steps.

**If reachable** — record from probe_ssh output:
- GPU count and PCI addresses (for ticket: "GPU 4 (PCI 0000:ab:00.0)")
- NVLink link states per GPU (which links up/down)
- IB port states per device (Active/Down/Initializing)
- Recent kernel messages (Xid, AER, NVLink errors)

**Do not stop here.** OS-level hardware looking healthy does NOT mean the node is ready for revalidation. Proceed to Step 2b.

---

## Step 2b: k8s-Plane Verification (ALL reachable nodes) — MANDATORY

Run all five checks on every reachable node. Any failure here drives classification directly — independent of what Layer 1 found.

Each check is labeled with where it runs:
- **LTP host** — `kubectl` commands run from the LTP jump host (or via `kubectl_check.sh`)
- **SSH node** — commands run on the target node via SSH

### Check 1: Kubelet Health (Layer 2) — SSH node

```bash
systemctl status kubelet
journalctl -u kubelet --no-pager -n 50
```

| Finding | Classification |
|---------|----------------|
| `active (running)`, no error loops | Pass — proceed to Check 2 |
| `activating` / `failed` / crash-loop | `triaged_platform / KubeletCrash` |
| `x509: certificate signed by unknown authority` or missing cert file | `triaged_platform / KubeletCertFailure` |
| `/etc/kubernetes/bootstrap-kubelet.conf: no such file` | `triaged_platform / KubeletCertFailure` |

Key cert files to verify exist (SSH node):
- `/etc/kubernetes/bootstrap-kubelet.conf`
- `/var/lib/kubelet/pki/kubelet-client-current.pem`

If kubelet is broken: **stop here** — k8s-plane checks below will be unreliable. Classify as `triaged_platform`.

### Check 2: Node Conditions and Allocatable Resources (Layer 3) — LTP host

```bash
kubectl describe node <hostname>
```

Extract and record:
- **Taints**: `node.kubernetes.io/unreachable:NoExecute` → kubelet stopped heartbeating (separate from SSH reachability — kubelet can be dead even if SSH works)
- **Conditions**: `Ready: True/False/Unknown`, `MemoryPressure`, `DiskPressure`
- **Allocatable.nvidia.com/gpu**: number the k8s scheduler sees (0 = device plugin broken)
- **Allocatable.rdma/hca**: number of RDMA devices visible to scheduler (0 = rdma-shared-dp broken)

### Check 3: OS vs k8s GPU Count Comparison (Layer 1 vs Layer 3)

Compare `nvidia-smi -L` count (from probe_ssh, SSH node) against `Allocatable.nvidia.com/gpu` (from kubectl describe, LTP host):

| nvidia-smi count | kubectl Allocatable.nvidia.com/gpu | Interpretation |
|-----------------|-----------------------------------|----------------|
| 8 | 8 | Layer 3 consistent — GPU device plugin OK |
| 8 | 0 | **Platform**: nvidia-device-plugin lost kubelet registration → `triaged_platform / StaleDevicePluginState` |
| < 8 | matches smi | **Hardware**: GPU physically missing/failed → `triaged_hardware / GPUUnhealthy` |
| 0 | 0 | Ambiguous — check dmesg Xid/NVLink errors and fabricmanager status |

Same comparison for `rdma/hca`: IB devices visible via `ibstat` (SSH node) vs `Allocatable.rdma/hca` (LTP host).

### Check 4: Pods on the Node — Stale Job Pods and Device Plugin Logs (Layer 3) — LTP host

```bash
# List all pods on the node
kubectl get pods --all-namespaces --field-selector spec.nodeName=<hostname>
```

Look for:
- **Non-system pods** not in `Terminating` or `Completed` state — these are stale job pods holding device slots
- **Device plugin pods** (`nvidia-device-plugin-*`, `rdma-shared-dp-*`) — check their status and recent logs

```bash
# Logs for the device plugin pods on this node
kubectl logs -n kube-system <nvidia-device-plugin-pod> --tail=50
kubectl logs -n kube-system <rdma-shared-dp-pod> --tail=50
```

| Finding | Classification |
|---------|----------------|
| No non-system pods (or all Completed/Terminating) | Pass |
| Job pod in `Running` state, not in k8s proper | **Platform**: stale job pod holding device slots → `triaged_platform / ZombieJobPod`. Note the pod name, namespace, age. |
| Device plugin log shows `Failed to re-register` or gRPC errors | Confirms `StaleDevicePluginState` |
| Device plugin pod in `CrashLoopBackOff` | `triaged_platform / StaleDevicePluginState` |

### Check 5: Device Plugin Checkpoint (Layer 3) — SSH node

```bash
sudo cat /var/lib/kubelet/device-plugins/kubelet_internal_checkpoint | python3 -c "
import json,sys
d=json.load(sys.stdin)
data=d.get('Data',d)
print('=== RegisteredDevices ===')
print(json.dumps(data.get('RegisteredDevices',{}), indent=2))
print('=== PodDeviceEntries (non-empty) ===')
for e in data.get('PodDeviceEntries',[]):
    if e.get('DeviceIDs'):
        print(json.dumps(e, indent=2))
"
```

Key fields:
- **`RegisteredDevices`**: lists what device plugins have successfully registered with kubelet. If `nvidia.com/gpu` or `rdma/hca` is absent here, that resource shows as 0 in `Allocatable`.
- **`PodDeviceEntries`** with `DeviceIDs: {"-1": [...]}`: orphaned allocation from a deleted pod. The `-1` key means the container→device mapping is broken. This blocks the device plugin from re-allocating those devices.

| Finding | Classification |
|---------|----------------|
| Both `nvidia.com/gpu` and `rdma/hca` in RegisteredDevices, no `-1` entries | Pass |
| `nvidia.com/gpu` absent from RegisteredDevices | Plugin de-registered → `triaged_platform / StaleDevicePluginState` |
| `rdma/hca` absent from RegisteredDevices | rdma-shared-dp de-registered → `triaged_platform / StaleDevicePluginState` |
| PodDeviceEntry with `DeviceIDs: {"-1": [...]}` for non-existent pod | Orphaned alloc → `triaged_platform / StaleDevicePluginState`. Fix: restart the responsible device plugin pod. |

**Confirming the device plugin socket is live** (SSH node, if plugin running but resource shows 0):
```bash
ls -la /var/lib/kubelet/device-plugins/nvidia-gpu.sock   # nvidia-device-plugin
ls -la /var/lib/kubelet/plugins_registry/hca.sock         # rdma-shared-dp
sudo fuser /var/lib/kubelet/device-plugins/nvidia-gpu.sock  # any PID listening?
```
If socket exists but `fuser` shows no PID → plugin abandoned the socket → gRPC broken → `triaged_platform / StaleDevicePluginState`.

---

### Step 2b Summary — Classification Shortcuts

After running all five checks, any of these conditions immediately drives the classification:

| Condition | Action |
|-----------|--------|
| Kubelet crash-loop / missing certs | `triaged_platform / KubeletCertFailure` |
| Taint `unreachable:NoExecute` + kubelet stopped | `triaged_platform / KubeletCrash` |
| nvidia-smi=8, k8s Allocatable.nvidia.com/gpu=0 | `triaged_platform / StaleDevicePluginState` |
| rdma/hca missing from Allocatable but IB ports Active | `triaged_platform / StaleDevicePluginState` |
| Stale job pod still Running on node | `triaged_platform / ZombieJobPod` |
| PodDeviceEntry DeviceIDs={"-1": ...} for ghost pod | `triaged_platform / StaleDevicePluginState` |

**Only if all five checks pass** → the node is genuinely platform-healthy at the k8s layer. Continue to Step 3 (branch by alert type) and consider revalidation as a candidate action.

---

## A-Branch: Validation Failures (A1–A5)

### A-Trunk: Validation Job Investigation (common for ALL A-rules)

The `NotifyUnvalidatedNodes` alert contains the validation job name.

| Step | Source | What to collect |
|------|--------|-----------------|
| 1 | `get_job_details` | Use the job name from the alert. Check `task_completion_phrase` for known hardware indicators: `ContainerMayFailDueToGpuDeviceEccError` → GPU ECC error; `ContainerUnrecognizedFailed` with empty `task_node` → node unreachable during job. |
| 2 | `get_job_events` | Use the `frameworkname` from `get_job_details`. Look for scheduling failures, resource errors. |
| 3 | `ltp.sh logs <user>~<job>` | Container log from the failed validation run. Extract error lines (Xid, CUDA error, UCX error, NCCL timeout, ECC error). Requires `LTP_HOST` + `LTP_TOKEN`; skip if unavailable. |

### A1/A2 — PCIe/GPU Bandwidth (gpu-copy-bw)

| What to collect | Why |
|-----------------|-----|
| GPU count and PCI addresses from probe_ssh | Identify which GPU has degraded bandwidth |
| nvidia-smi output | GPU health, ECC counts |
| PCIe-related kernel errors in dmesg (AER, DPC, link down) | Hardware-level PCIe fault vs transient benchmark noise. Collect specific excerpts. |
| Exact benchmark values from get_node_alerts | "PCIe DMA CPU→GPU4: actual 13.54 GB/s, baseline 55.25 GB/s, variance -75%" |

### A3 — NVLink (nccl-bw)

| What to collect | Why |
|-----------------|-----|
| GPU count and PCI addresses from probe_ssh | 0 GPUs = severe NVLink/driver failure |
| NVLink link state per GPU from probe_ssh | Which GPU links are down? (e.g., "GPU 2: link 0–3 down, link 4–5 active") |
| NVLink-related kernel errors from probe_ssh or dmesg | Known b300 pattern: `PostRxDetectLinkMask` failures. Collect specific excerpts. |
| NCCL benchmark values from get_node_alerts | Actual vs expected bandwidth, which GPU pairs affected |
| NCCL/UCX error log excerpts from job logs | Concrete error messages: e.g., `ibv_create_ah failed: Connection timed out on mlx5_3` |

### A4 — IB (ib-loopback)

| What to collect | Why |
|-----------------|-----|
| IB port states from probe_ssh | Which ports are Down? (e.g., "mlx5_3 port 1: Down") |
| IB error counters | Physical error counts confirm hardware fault. Record specific values. |
| IB/driver-related kernel errors | Hardware fault vs driver/config issue. Collect specific dmesg excerpts. |
| IB benchmark values from get_node_alerts | Actual vs expected bandwidth |
| UCX error log excerpts from job logs | e.g., `ibv_create_ah failed: Connection timed out on mlx5_3` |

**Decision refinement:** IB ports Down → `triaged_hardware`. All Active but driver errors → consider `triaged_platform`.

### A5 — Validation Reason Undefined

The alert says "undefined" — this means the validation job's result could not be determined. This is NOT a single issue. There are **three distinct root causes**, each requiring different investigation:

#### Sub-cause 1: Validation job failed to run at all

The superbench job never started on the node.

| What to check | How | What it means |
|---------------|-----|---------------|
| Job exists and state | `get_validation_job(hostname)` | If no job found or state is `WAITING` → job was never scheduled. Check: is node cordoned? Is node reachable? GPU count in `Allocatable`? |
| Job scheduling events | `get_job_events(job_name)` | `FailedScheduling` → what constraint failed? Usually GPU missing, SKU mismatch, or node unreachable |
| Node readiness | `run_kubectl(command="get node <hostname>")` | `NotReady` / `SchedulingDisabled` → node was cordoned or kubelet not heartbeating |

Common root causes: node unreachable at validation time, GPU not visible to k8s (device plugin not registered), kubelet cert expired, disk full preventing job pull.

#### Sub-cause 2: Validation job ran but failed to pass

The superbench job executed but one or more benchmarks failed. "Undefined" means the Alert Manager couldn't parse a specific reason code from the result. **You must investigate the actual job output to find what failed.**

**What to investigate:**

- **Job identity and state** — `get_validation_job(hostname)` → `job_name`, `job_state`; `get_job_details(jobname=job_name)` → `completion_state`, `exit_code`, `task_completion_phrase`; `get_job_events(job_name)` → scheduling events, framework errors
- **Job logs** — `ltp.sh logs <user>~<job_name>` (handles auth + log retrieval), or `run_ssh_command` to curl the log manager API directly. Look for: which benchmark failed, which GPU, specific error messages (NCCL timeout, GPU memory error, IB bandwidth below threshold, disk I/O error)
- **Alert annotations** — `get_node_alerts(hostname, alertname="NotifyUnvalidatedNodes", include_details=True)`. The `annotations` field often contains the real failure reason: benchmark name (e.g., `nccl-test`, `gpu-burn`), baseline vs actual values, variance percentage, GPU index. **This IS the real failure reason** even when the status summary says "undefined"
- **Hardware correlation** — trace the specific benchmark failure to a hardware subsystem: GPU-related → `probe_ssh` for ECC/Xid; IB-related → IB port states + error counters; disk-related → `df -h`, dmesg

Do NOT shortcut to "validation undefined = hardware unknown." Find the specific benchmark that failed and trace it to a hardware subsystem.

#### Sub-cause 3: Validation job completed but results are inaccessible

The job may have passed or failed, but the platform cannot retrieve its results. This is a **platform infrastructure investigation** — the log serving path is broken somewhere.

**What to investigate:**

This is a deep investigation into why the log serving infrastructure is not working. Possible failure points include:

- **Log manager pod health** — is it running? CrashLoopBackOff? OOMKilled? Config/cert mount missing? Check pod status, logs, restart count
- **Service reachability** — can the node reach the log manager endpoint? Can other nodes? Is it reachable from some but not others? This differentiates network issues from service issues
- **DNS resolution** — does the node's DNS resolve the log manager service? Is `/etc/resolv.conf` pointing to cluster DNS?
- **Network path** — can packets reach the log manager network? Is CNI healthy on the node? Are network policies blocking egress?
- **Log storage** — is the log disk full? Are log directories being created? Is the object storage backend (OSS/NFS) mounted and writable?
- **Port/config** — does the service targetPort match the container port? Any host port conflicts?

**This is not a quick fix.** Investigate methodically, document what you find, and escalate to the platform team if the root cause is beyond the repair agent's scope (e.g., CNI misconfiguration, network policy issues, object storage outage). Do NOT just restart the pod — find why it's broken.

#### Investigation order

1. First check **Sub-cause 1** (job scheduling) — this is the fastest check via `get_validation_job` + `get_job_events`
2. If job ran (state is not WAITING), check **Sub-cause 2** (job failure) — get job details, job logs, alert annotations, trace to specific hardware subsystem
3. If job shows completed/succeeded but result is still "undefined", check **Sub-cause 3** (log infrastructure) — pod health, service reachability, DNS, network path, storage, port config

---

## B-Branch: Pod Issues (B1–B5)

### B-Trunk: Pod Investigation (common for ALL B-rules)

| Step | Source | What to collect |
|------|--------|-----------------|
| 1 | kubectl: pod status on node | Is it still failing? (alert may be stale) |
| 2 | kubectl: describe pod | Last state, error, exit code, restart count |
| 3 | kubectl: logs | Container error output |

### B1 — JobExporterPortConflict

| What to check | Why |
|---------------|-----|
| What process is using the conflicting port? | Confirms port conflict |

**Decision:** Pod now Running → self-resolved → revalidation. Still failing → `triaged_platform`.

### B2–B5 — Other Pods

| What to check | Why |
|---------------|-----|
| CDI spec presence, GPU driver status (if GPU/CDI-related) | CDI missing → `triaged_platform`, GPU driver hanging → `triaged_hardware` |

**Decision:** Self-resolved → revalidation. CDI/config → `triaged_platform`. GPU hardware → `triaged_hardware`.

---

## C-Branch: Hardware Alerts (C1–C4)

### C1 — NvidiaSmiLatencyTooLarge

| What to collect | Why |
|-----------------|-----|
| nvidia-smi response, GPU count | Confirms alert is current |
| nvidia-smi full output | ECC counts, temperatures, power |
| GPU errors in kernel log (Xid, ECC, NVLink) from probe_ssh or dmesg | Underlying cause. Collect specific excerpts. |

**Decision:** nvidia-smi hangs or 0 GPUs → `triaged_hardware`. Works fine now → transient, check for other issues.

### C2 — FabricManagerMismatch

| What to collect | Why |
|-----------------|-----|
| nvidia-fabricmanager status from check_fabricmanager | Stopped/crashed confirms hardware mismatch |
| GPU count from probe_ssh | Fabric manager issues can cause GPU disappearance |
| Fabric manager / NVSwitch kernel errors from probe_ssh or dmesg | Specific error messages. Collect excerpts. |

**Decision:** Fabricmanager stopped + NVSwitch errors → `triaged_hardware`. Running now → still `triaged_hardware` but note may be transient.

### C3 — NodeCrash

NodeCrash can mask underlying hardware faults. Probe all business IPs, not just one.

| What to collect | Why |
|-----------------|-----|
| All business IPs reachable? | Any unreachable = concrete hardware/network evidence. Note which IPs. |
| If SSH works: GPU health, ECC counts, nvidia-smi | Look for masked GPU ECC. Note GPU index + PCI address + ECC count. |
| If SSH works: IB state | IB port states per device, error counters |
| If SSH works: NVLink state | NVLink link states per GPU |
| If SSH works: `sudo dmesg` excerpts | Specific Xid codes, AER errors, NVLink errors |
| If SSH works: recent failed jobs (`get_node_recent_jobs` with `end_ts=triaged_timestamp`) | Co-occurring failures near crash time |
| `bmc_health_log` + `bmc_query` (command=`sel_list`) | Check BOTH — they cover different events. `bmc_health_log`: structured Redfish events (GPU not present, NVSwitch, NIC temp, power). `bmc_query sel_list`: raw IPMI SEL (memory ECC, PCIe AER, thermal thresholds). Get `bmc_ip` from `get_node_detail` → `mgmt_ip[0]`. Use `category="h200"` for Supermicro, `category="b300"` for AMI. |
| `bmc_screenshot` | Console state — kernel panic, BIOS errors, POST failures |

**Decision rules:**
- Any business IP unreachable → `triaged_hardware / NodeCrash`
- Concrete hardware evidence found → `triaged_hardware` / matched reason
- Reachable + no hardware evidence → don't force `triaged_hardware`. Transient/platform → `triaged_platform`. Unclear → `triaged_unknown`
- Never assume hardware without evidence

### C4 — GPUUnhealthy

| What to collect | Why |
|-----------------|-----|
| GPU count from probe_ssh | Missing GPU = severe fault. Note which GPU index. |
| ECC error counts per GPU from nvidia-smi | Which GPU has uncorrectable ECC? (e.g., "GPU 3: 42 uncorrectable ECC") |
| GPU errors in kernel log (Xid, ECC, NVLink) from probe_ssh or dmesg | Xid type reveals fault type — see `references/xid-reference.md` for full catalog. Key codes: Xid 48=DBE, Xid 64=remapping failure, Xid 79=fallen off bus, Xid 95=uncontained error, Xid 94=contained (secondary). |
| GPU PCI addresses | For ticket: "GPU 3 (PCI 0000:07:00.0)" |

**Decision:** Confirmed ECC on specific GPU → `triaged_hardware / GPUUnhealthy` with GPU index + ECC count. No ECC found now → possibly transient, investigate further.

---

## Deep Dive — Unknown / No Match (A5-unclear, D1, Z1, Z2)

For nodes where the alert doesn't provide a clear direction, or no alert at all:

| Step | Source | What to collect |
|------|--------|-----------------|
| 1 | `get_node_recent_jobs` (with `end_ts=triaged_timestamp`) | Discover what jobs ran/failed before triage. Finds training jobs but NOT validation jobs. |
| 2 | `get_job_details` + `get_job_events` + `ltp.sh logs` | For each failed/relevant job. Collect error log excerpts. |
| 3 | `get_node_alerts` (with `start_ts`/`end_ts` from triage window, no `alertname` filter) | All alerts in the window. Extract all numeric values. |
| 4 | `get_node_history` (with `start_ts`/`end_ts`, `include_alerts=True`) | Full status timeline with alert context. |
| 5 | kubectl | Node conditions (Ready? SchedulingDisabled?), events, taints, pod status |
| 6 | SSH | Broad hardware checks: GPU health, IB state, NVLink state, dmesg, fabricmanager. Collect specific error excerpts. |
| 7 | `bmc_health_log` + `bmc_query` | Check BOTH — Redfish health log (GPU/NVSwitch/NIC events) and IPMI SEL (memory ECC, PCIe AER, thermal). Get `bmc_ip` from `get_node_detail` → `mgmt_ip[0]`. |

If still unexplained: report "no root cause found" — do NOT dig into source code.

---

## Rules

- **Never skip an investigation because a tool/token is missing.** Attempt it, report what couldn't be done, flag as incomplete.
- **Alert = category, not conclusion.** Investigation may change the target status.
- **Use explicit `start_ts`/`end_ts` from the triage window** for `get_node_alerts` and `get_node_recent_jobs`. The triage window (validating_timestamp → triaged_timestamp) is the precise alert/job range; `hours`/`days` from NOW() are rough fallbacks.
- **Do not rely on argument shapes written here.** Use MCP tool schemas at runtime for exact parameter names and types. The tips above are guidance — the schema is authoritative.
