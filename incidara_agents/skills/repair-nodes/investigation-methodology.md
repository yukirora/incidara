# Repair Nodes — Investigation Methodology

How to collect evidence for repair actions. Single source of truth for **evidence collection logic per fault type**.
Covers both hardware faults (→ RMA ticket) and platform faults (→ fix + revalidation).

Used by the repair agent when filling evidence gaps or re-collecting diagnostics.

For **hardware execution pipeline**, see `hardware-repair-flow.md`.
For **platform execution pipeline**, see `platform-repair-flow.md`.
For **ticket wording and quality gate**, see `repair-ticket-summary-methodology` skill.
For **workflow orchestration** (scope, discover, report), see `SKILL.md`.

---

## Two Paths to `triaged_hardware`

- **Path 1** (via triage agent): `triaged_unknown` → triage investigates → `triaged_hardware`
  Evidence may already exist in the evidence DB, but may be shallow (just enough to classify, not enough for a ticket).

- **Path 2** (direct): Auto-cordon (e.g., `CordonValidationFailedNodes`) with no investigation.
  Only the cordon alert exists — no investigation was ever done.

For either path, always verify and fill gaps. Never assume existing evidence is sufficient.

---

## Evidence Collection Procedure

Follow these steps in order for each `triaged_hardware` node:

### 1. Check existing saved evidence

Call `get_node_evidence_tool(hostname)` — see what triage or a previous repair run already collected.
Filter by `collected_by="triage"` to see what the triage agent found.
If evidence already exists and is recent, skip re-collection for those sources.

### 2. Check platform data (auto-saved to evidence DB)

| Tool | What you get | Tips |
|------|-------------|------|
| `get_node_alerts` | Benchmark details, numeric values from `NotifyUnvalidatedNodes` | Use `start_ts`/`end_ts` from the triage window — NOT `hours` from NOW(). |
| `get_node_history` | Triage context + existing evidence | Use `start_ts`/`end_ts` from the triage window. `include_alerts=True` to see alerts alongside actions. |
| `get_node_detail` | FaultCode, category, SN, IPs | Default `brief=True` — usually sufficient. Use `brief=False` only if you need metainfo. |

### 3. Always re-run live diagnostics (auto-saved to evidence DB)

| Tool | What you get | Why always |
|------|-------------|-----------|
| `probe_ssh` | GPU inventory, NVLink states, IB ports, kernel messages (uses sudo for dmesg) | Node state may have changed since triage. Pass both `ip` and `hostname` for auto-save. |

If `probe_ssh` shows the node is unreachable:
- Use `bmc_health_log` for structured Redfish health events — GPU not present, NVSwitch errors, NIC temp critical, PCIe errors, power events
- Use `bmc_query(command="sel_list")` for raw IPMI SEL events — memory ECC, PCIe AER, thermal thresholds. For crash windows, prefer `bmc_query(command="sel_elist", since="<UTC>", until="<UTC>")`; also collect `sel_info`, `sel_time_get`, and `sel_get` for suspicious record IDs.
- Check BOTH — they cover different event types and complement each other
- Use `bmc_screenshot` for console state (kernel panic, BIOS errors, POST failures)
- Get `bmc_ip` from `get_node_detail` → `mgmt_ip[0]`

If you run additional SSH commands (nvidia-smi, ibstat, etc.):
- Identify useful excerpts (Xid codes, AER errors, ECC counts, failed port states)
- Save via `save_evidence_tool` with a meaningful `summary`
- **Note: `dmesg` requires `sudo`** — without sudo it returns "Operation not permitted". The `probe_ssh` MCP tool already uses sudo internally.

### 4. Fill evidence gaps by fault type

Based on the triage reason / FaultCode, follow the relevant branch below.
If existing evidence is already sufficient for that branch, skip re-collection.

### 5. Pass the evidence completeness gate

Call `get_node_evidence_tool(hostname)` to list all collected evidence.
Apply the quality gate from `repair-ticket-summary-methodology` — verify all
mandatory evidence items are present for the fault type.
If blocked: go back and collect missing evidence. Do NOT submit incomplete tickets.

---

## Per-Fault-Type Evidence Guide

### PCIe/GPU Bandwidth (FaultCode ~ AMDGPUDriverHang, SuperBenchModelPerformanceDegradation; reason ~ gpu-copy-bw, gemm-flops)

| What to collect | Source | Why |
|-----------------|--------|-----|
| Benchmark numeric values (actual, baseline, variance %) | `get_node_alerts` (alertname=`NotifyUnvalidatedNodes`) | Core evidence for ticket — concrete numbers |
| Which GPU index / PCI address | `probe_ssh` GPU inventory + alert details | Identify the specific GPU |
| GPU health, ECC counts | `probe_ssh` + SSH: `nvidia-smi` | Rule out co-occurring GPU fault |
| PCIe errors in dmesg (AER, DPC, link down) | `probe_ssh` kernel messages + SSH: `sudo dmesg` | Hardware-level PCIe fault vs transient noise |
| Fabricmanager status | `check_fabricmanager` | FM crash can cause GPU bandwidth degradation |

### NVLink (reason ~ nccl-bw, NVLink)

| What to collect | Source | Why |
|-----------------|--------|-----|
| Which GPU links affected | `probe_ssh` NVLink states | Specific link state per GPU |
| NVLink benchmark values or NCCL error details | `get_node_alerts` + job logs | Concrete numbers for ticket |
| GPU count and PCI addresses | `probe_ssh` | 0 GPUs = severe NVLink/driver failure |
| NVLink-related kernel errors (PostRxDetectLinkMask, etc.) | `probe_ssh` kernel messages + SSH: `sudo dmesg` | Known b300 pattern |
| Fabricmanager status | `check_fabricmanager` | FM crash can cause NVLink state collapse |
| NCCL/UCX error log excerpts from job logs | `ltp.sh logs` or `get_job_events` | Concrete error messages |

### IB (reason ~ ib-loopback, IBPortDown, IBAbnormal)

| What to collect | Source | Why |
|-----------------|--------|-----|
| Which IB device/port affected | `probe_ssh` IB port states + alert details | Specific port identification |
| IB benchmark values or UCX/NCCL error excerpts | `get_node_alerts` + job logs | Concrete numbers for ticket |
| IB port states and error counters | `probe_ssh` + SSH: `ibstat`, `ibqueryerrors` | Physical error counts confirm hardware fault |
| IB/driver-related kernel errors | `probe_ssh` kernel messages + SSH: `sudo dmesg` | Hardware fault vs driver/config issue |
| Fabricmanager status | `check_fabricmanager` | FM issues can affect IB connectivity |

### GPU ECC / GPUUnhealthy (FaultCode ~ GPUUnhealthy, ECC)

| What to collect | Source | Why |
|-----------------|--------|-----|
| Which GPU (index + PCI address) | `probe_ssh` + `get_node_alerts` | Specific GPU identification |
| ECC count (uncorrectable) | `probe_ssh` + SSH: `nvidia-smi` | Core evidence — concrete ECC count |
| Xid code from dmesg | `probe_ssh` kernel messages + SSH: `sudo dmesg` | Xid type reveals fault: 94=NVLink, 79=fallen off bus, 63=ECC |
| GPU health and count | `probe_ssh` | Missing GPU = severe fault |

### NodeCrash (FaultCode ~ NodeCrash)

| What to collect | Source | Why |
|-----------------|--------|-----|
| Which IPs reachable / unreachable | `probe_ssh` (all business IPs) | Any unreachable = concrete hardware evidence |
| If SSH works: GPU health, ECC counts | `probe_ssh` + SSH: `nvidia-smi` | Look for masked GPU ECC |
| If SSH works: IB, NVLink, dmesg | `probe_ssh` + SSH: `ibstat`, `sudo dmesg` | Check all subsystems for root cause |
| BMC health log | `bmc_health_log` + `bmc_query(command="sel_list")` | Check BOTH — Redfish health log (GPU/NVSwitch/NIC) and IPMI SEL (memory ECC, PCIe AER, thermal). Use `category="h200"` for Supermicro, `category="b300"` for AMI. For power/crash cases, also collect `sel_info`, `sel_time_get`, and a filtered `sel_elist` with `since`/`until`. |
| BMC screenshot | `bmc_screenshot` | Console state — kernel panic, BIOS errors |
| Recent failed jobs before crash | `get_node_recent_jobs(end_ts=triaged_timestamp)` | Co-occurring failures near crash time |

### FabricManager (reason ~ FabricManagerMismatch)

| What to collect | Source | Why |
|-----------------|--------|-----|
| Fabricmanager service status | `check_fabricmanager` | Stopped/crashed confirms mismatch |
| GPU count | `probe_ssh` | FM issues can cause GPU disappearance |
| NVSwitch / NVLink kernel errors | `probe_ssh` kernel messages + SSH: `sudo dmesg` | FM crash is often symptom of NVSwitch fault |
| FM version vs driver version | `check_fabricmanager` + SSH: `nvidia-smi` | Version mismatch → platform issue. Hardware errors → `triaged_hardware` |

### CUDADriverFailure / GPUDriverHanging / MMUFault

| What to collect | Source | Why |
|-----------------|--------|-----|
| GPU count and nvidia-smi output | `probe_ssh` + SSH: `nvidia-smi` | Driver may have crashed — 0 GPUs visible |
| Xid codes in dmesg | `probe_ssh` kernel messages + SSH: `sudo dmesg` | Xid 43=GPU stopped, Xid 31=chained, Xid 44=MMU fault |
| NVLink and NVSwitch state | `probe_ssh` | Underlying hardware fault may cause driver failures |
| Fabricmanager status | `check_fabricmanager` | FM crash often accompanies driver failures |
| Recent kernel errors | `probe_ssh` kernel messages | AER errors, link down, power events |

### DiskError

| What to collect | Source | Why |
|-----------------|--------|-----|
| Disk device and error type | `get_node_alerts` + `get_node_detail` | Which disk, what error |
| Smartctl / nvme smart-log output | SSH: `smartctl -a /dev/...` or `nvme smart-log /dev/...` | Concrete SMART failure indicators |
| Filesystem errors in dmesg | `probe_ssh` kernel messages + SSH: `sudo dmesg` | I/O errors, read-only remounts |

### CPUMemoryLost / SKUMismatch

| What to collect | Source | Why |
|-----------------|--------|-----|
| Expected vs actual hardware | `get_node_detail` (brief=False if needed) | SKU mismatch details |
| Memory inventory | SSH: `dmidecode -t memory`, `lsmem` | Which DIMMs are missing/failed |
| CPU topology | SSH: `lscpu` | Verify CPU count and model |

---

## Platform Fault Types

For platform issues, evidence collection focuses on **which layer is broken and what specifically is wrong** — this directly determines the fix.

### StaleDevicePluginState

| What to collect | Source | Why |
|-----------------|--------|-----|
| nvidia-smi GPU count vs Allocatable.nvidia.com/gpu | `probe_ssh` + kubectl `describe node` | Mismatch = device plugin broken |
| ibstat count vs Allocatable.rdma/hca | `probe_ssh` + kubectl `describe node` | Mismatch = rdma plugin broken |
| RegisteredDevices in kubelet checkpoint | SSH: `cat /var/lib/kubelet/device-plugins/kubelet_internal_checkpoint` | Missing resource = plugin de-registered |
| PodDeviceEntries with `-1` DeviceIDs | SSH: checkpoint inspection | Orphaned allocation blocks re-registration |
| Device plugin pod logs | kubectl: `kubectl logs -n kube-system <pod>` | gRPC errors, registration failures |
| Device plugin socket status | SSH: `fuser /var/lib/kubelet/device-plugins/nvidia-gpu.sock` | Abandoned socket = gRPC broken |

### ZombieJobPod

| What to collect | Source | Why |
|-----------------|--------|-----|
| Running containers on node | SSH: `crictl ps` | Non-system container = stale job pod |
| Cross-reference with k8s pods | kubectl: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<hostname>` | Pod NotFound but container running = zombie |
| Which device slots are held | SSH: checkpoint PodDeviceEntries | The zombie is holding GPU/RDMA slots |
| Container age and image | SSH: `crictl inspect <id>` | Confirms it's a stale job, not a system pod |

### KubeletCertFailure

| What to collect | Source | Why |
|-----------------|--------|-----|
| Kubelet service status | SSH: `systemctl status kubelet` | Crash-loop or cert error message |
| Cert file existence | SSH: `ls -la /etc/kubernetes/bootstrap-kubelet.conf /var/lib/kubelet/pki/kubelet-client-current.pem` | Missing files = cert failure |
| Kubelet journal | SSH: `journalctl -u kubelet --no-pager -n 50` | Specific x509/cert error messages |

### KubeletCrash

| What to collect | Source | Why |
|-----------------|--------|-----|
| Kubelet service status | SSH: `systemctl status kubelet` | `failed` / `activating` / crash-loop |
| Crash reason from journal | SSH: `journalctl -u kubelet --no-pager -n 50` | OOM, config parse error, cert failure |
| Disk space | SSH: `df -h` | Disk pressure can cause kubelet crash |
| Node taint status | kubectl: `kubectl describe node <hostname>` | `unreachable:NoExecute` = kubelet not heartbeating |

### JobExporterPortConflict

| What to collect | Source | Why |
|-----------------|--------|-----|
| Port in use | SSH: `ss -tlnp \| grep 9102` (or 9104) | What process holds the port |
| Job-exporter pod status | kubectl: `kubectl get pods -n kube-system --field-selector spec.nodeName=<hostname>` | RunContainerError or CrashLoopBackOff |
| Job-exporter events | kubectl: `kubectl describe pod -n kube-system <pod>` | Port bind failure message |

---

## Cross-Skill Methodology for New Failure Types

When the case doesn't match known fault types:

1. **Read normalized triage context first:**
   - `fault_source`, `symptom`, `impact`, `repro_hint`, `confidence`, `unknown_pattern`, `evidence[]`
2. **Decide evidence depth by confidence:**
   - Confidence high and evidence consistent → draft ticket directly
   - Confidence low or conflicting → run targeted deep-dive before wording
3. **Keep ownership boundary strict:**
   - Triage owns classification and compact evidence payload
   - Repair owns vendor-facing Chinese `summary/reproducer`
4. **If still unclear after deep-dive:**
   - Do not invent certainty in ticket wording
   - Use conservative symptom-focused text and put uncertainty in internal notes

---

## Rules

- **Always re-run `probe_ssh`.** Node state may have changed since triage. Live diagnostics are mandatory, not optional.
- **Auto-save via MCP tools.** `probe_ssh`, `get_node_alerts`, `check_fabricmanager`, `get_node_detail`, `get_node_history`, `get_node_recent_jobs` all auto-persist to evidence DB. No manual save needed.
- **Manual save for ad-hoc SSH.** Curate useful excerpts (not raw dumps). Add meaningful `summary`. `source` must be one of: `probe_ssh`, `nvidia_smi`, `dmesg`, `ib_stat`, `job_log`, `alert`, `nvlink`, `fabricmanager`, `job_list`, `job_detail`, `job_events`, `other`.
- **FM crash is often hardware.** FM crash WITH any GPU/NVLink anomaly = `triaged_hardware`, not platform.
- **IB Active + errors = hardware.** Active port with error counters or FW errors → `triaged_hardware`.
- **Validation failure is NEVER a platform issue.** Validation timeout/undefined/FailedScheduling is always caused by a real problem. "Undefined" has 3 sub-causes — see the "Validation Reason Undefined" section below.
- **Use explicit `start_ts`/`end_ts` from the triage window.** NOT `hours` from NOW().
- **Do not rely on argument shapes written here.** Use MCP tool schemas at runtime for exact parameters.

---

## Validation Reason Undefined

The alert says "undefined" — the validation job's result could not be determined. This is NOT a single issue. Three distinct root causes, each requiring different investigation:

### Sub-cause 1: Validation job failed to run at all

The superbench job never started on the node.

- `get_validation_job(hostname)` — no job found or state is `WAITING` → job was never scheduled
- `get_job_events(job_name)` — `FailedScheduling` → what constraint failed? GPU missing, SKU mismatch, node unreachable
- `run_kubectl(command="get node <hostname>")` — `NotReady` / `SchedulingDisabled`

Common root causes: node unreachable, GPU not visible to k8s (device plugin), kubelet cert expired, disk full.

### Sub-cause 2: Validation job ran but failed to pass

The superbench job executed but one or more benchmarks failed. "Undefined" means the Alert Manager couldn't parse a specific reason code from the result. **You must investigate the actual job output to find what failed.**

**What to investigate:**

- **Job identity and state** — `get_validation_job(hostname)` → `job_name`, `job_state`; `get_job_details(jobname=job_name)` → `completion_state`, `exit_code`, `task_completion_phrase`; `get_job_events(job_name)` → scheduling events, framework errors
- **Job logs** — `ltp.sh logs <user>~<job_name>` (handles auth + log retrieval), or `run_ssh_command` to curl the log manager API directly. Look for: which benchmark failed, which GPU, specific error messages (NCCL timeout, GPU memory error, IB bandwidth below threshold, disk I/O error)
- **Alert annotations** — `get_node_alerts(hostname, alertname="NotifyUnvalidatedNodes", include_details=True)`. The `annotations` field often contains the real failure reason: benchmark name (e.g., `nccl-test`, `gpu-burn`), baseline vs actual values, variance percentage, GPU index. **This IS the real failure reason** even when the status summary says "undefined"
- **Hardware correlation** — trace the specific benchmark failure to a hardware subsystem: GPU-related → `probe_ssh` for ECC/Xid; IB-related → IB port states + error counters; disk-related → `df -h`, dmesg

Do NOT shortcut to "validation undefined = hardware unknown." Find the specific benchmark that failed and trace it to a hardware subsystem.

### Sub-cause 3: Validation job completed but results are inaccessible

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

### Investigation order

1. **Sub-cause 1** (job scheduling) — fastest: `get_validation_job` + `get_job_events`
2. **Sub-cause 2** (job failure) — get job details, job logs, alert annotations, trace to specific hardware subsystem
3. **Sub-cause 3** (log infrastructure) — pod health, service reachability, DNS, network path, storage, port config
