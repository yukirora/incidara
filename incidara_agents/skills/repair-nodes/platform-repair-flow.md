# Platform Repair Flow

The iterative execution pipeline for `triaged_platform` nodes: **diagnose → fix → verify → loop**.
After the fix is verified, send the node to revalidation.

**All node categories** — process all node types (h200, b300, cpu, storage, ctrl). GPU-specific fix steps (nvidia-container-runtime, FM restart, device plugin) only apply to h200/b300; non-GPU nodes skip those steps.

Unlike hardware repair (linear: evidence → ticket → RMA), platform repair is iterative — you may need multiple fix attempts, and the node must pass all three layers before revalidation.

For **what evidence to collect per fault type**, see `investigation-methodology.md`.
For **workflow orchestration** (scope, discover, report), see `SKILL.md`.

---

## Pipeline Overview

```
triaged_platform
  │
  ├─ Step 1: Read triage diagnosis (reason + existing evidence)
  │
  ├─ Step 2: Re-verify — is the issue still present?
  │   ├─ All layers healthy → self-recovered → validating
  │   ├─ Same issue → continue
  │   └─ Different issue → re-triage
  │
  ├─ Step 3: Attempt fix (by reason)
  │   ├─ StaleDevicePluginState → restart device plugin
  │   ├─ ZombieJobPod → kill stale container + restart device plugin
  │   ├─ KubeletCertFailure → renew certs
  │   ├─ KubeletCrash → restart kubelet (+ cert check)
  │   ├─ JobExporterPortConflict → restart job-exporter
  │   └─ Unknown → escalate
  │
  ├─ Step 4: Verify fix
  │   ├─ All 3 layers healthy → Step 5 (revalidation)
  │   ├─ Fix didn't help → try next remediation → loop back to Step 3
  │   └─ New issue found → re-triage
  │
  └─ Step 5: Send to revalidation (move to validating)
```

---

## Step 1: Read Triage Diagnosis

Call `get_node_evidence_tool(hostname)` and `get_node_history(hostname)` to understand:
- **Reason** — tells you which fix to attempt (e.g., `StaleDevicePluginState`)
- **Triage evidence** — what Layer 2/3 checks triage already ran and what they found
- **Timestamps** — `validating_timestamp` / `triaged_timestamp` for alert window

If no triage evidence exists (auto-cordon path), run the full Layer 2/3 verification from Step 2.

---

## Step 2: Re-Verify Issue Is Still Present

The node may have self-recovered since triage. Re-run the relevant Layer 2/3 checks before attempting any fix.

### Layer 1 — Physical Hardware (SSH)

Call `probe_ssh` — confirm all GPUs visible, NVLink up, IB Active, no Xid/AER in dmesg (probe_ssh uses sudo internally).
If Layer 1 has issues → this is NOT a platform-only problem. Re-triage as `triaged_hardware`.

### Layer 2 — Kubelet (SSH)

Use `run_ssh_command` to check kubelet:
```
run_ssh_command(hostname="<hostname>", ip="<ip>", command="systemctl status kubelet")
```

| Finding | Next step |
|---------|-----------|
| `active (running)`, no error loops | Pass — proceed to Layer 3 |
| `failed` / crash-loop | KubeletCrash path (Step 3) |
| `x509: certificate signed by unknown authority` or missing cert file | KubeletCertFailure path (Step 3) |

Key cert files to verify exist:
- `/etc/kubernetes/bootstrap-kubelet.conf`
- `/var/lib/kubelet/pki/kubelet-client-current.pem`

### Layer 3 — k8s Resource Plane (kubectl + SSH)

**Check 1: Node conditions and allocatable** — use `run_kubectl`:
```
run_kubectl(command="describe node <hostname>", hostname="<hostname>")
```
- Taints: `node.kubernetes.io/unreachable:NoExecute` → kubelet not heartbeating
- `Allocatable.nvidia.com/gpu` vs nvidia-smi count → mismatch = device plugin issue
- `Allocatable.rdma/hca` vs ibstat count → mismatch = rdma plugin issue

**Check 2: Pods on the node** — use `run_kubectl`:
```
run_kubectl(command="get pods --all-namespaces --field-selector spec.nodeName=<hostname>", hostname="<hostname>")
```
- Non-system pod still Running → ZombieJobPod

**Check 3: Device plugin checkpoint** — use `run_ssh_command`:
```
run_ssh_command(hostname="<hostname>", ip="<ip>", command="cat /var/lib/kubelet/device-plugins/kubelet_internal_checkpoint")
```
- `RegisteredDevices` missing nvidia.com/gpu or rdma/hca → plugin de-registered
- `PodDeviceEntries` with `DeviceIDs: {"-1": [...]}` for ghost pod → orphaned allocation

### Outcome of Re-Verification

| Result | Action |
|--------|--------|
| All 3 layers healthy | Self-recovered → go directly to Step 5 (revalidation) |
| Same issue as triage found | Continue to Step 3 (attempt fix) |
| Different issue found | Re-triage with new evidence → loop back to Step 1 |

---

## Step 3: Attempt Fix

Based on the confirmed issue, apply the appropriate fix.

### StaleDevicePluginState

**Root cause:** nvidia-device-plugin or rdma-shared-dp lost kubelet registration or has orphaned allocation in checkpoint.

**Fix 1 — restart the affected device plugin pod** using `run_kubectl`:
```
# Identify the pod on this node
run_kubectl(command="get pods -n kube-system --field-selector spec.nodeName=<hostname>", hostname="<hostname>")
# Look for nvidia-device-plugin or rdma-shared-dp pods

# Delete the pod — daemonset will recreate it
run_kubectl(command="delete pod -n kube-system <device-plugin-pod>", hostname="<hostname>")
```

Wait ~30s for the new pod to start and re-register. Then re-check:
```
run_kubectl(command="describe node <hostname>", hostname="<hostname>")
```
Check `Allocatable` section for GPU/RDMA counts.

**Fix 2 — if pod restart didn't help, restart kubelet** (resets checkpoint):
Orphaned `PodDeviceEntries` with `DeviceIDs: {"-1": [...]}` can only be cleared by restarting kubelet:
```
run_ssh_command(hostname="<hostname>", ip="<ip>", command="systemctl restart kubelet", sudo=True)
```
After kubelet restart, device plugins re-register within ~60s.

### ZombieJobPod

**Root cause:** A job container is still running on the node but k8s doesn't know about it. It holds GPU/RDMA device slots.

**Fix — kill the stale container, then restart device plugins** using `run_ssh_command` + `run_kubectl`:
```
# Find non-system containers
run_ssh_command(hostname="<hostname>", ip="<ip>", command="crictl ps | grep -v -E 'kube-proxy|nvidia-device-plugin|rdma-shared|cilium|coredns|pause|node-exporter|job-exporter|kube-rbac-proxy|mounter'")

# Cross-reference: kubectl get pod should return NotFound for the stale one
run_kubectl(command="get pod <pod-name> -n <namespace>", hostname="<hostname>")

# Kill the stale container
run_ssh_command(hostname="<hostname>", ip="<ip>", command="crictl stop <container-id>")
```

After killing the stale container, restart the device plugin pods (same as StaleDevicePluginState) to reclaim the freed device slots.

### KubeletCertFailure

**Root cause:** Kubelet's client/server certs expired or are missing.

**Fix — renew certs** (preferred via config stage):
```
run_config_stage(hostname="<hostname>", stage="setup_kubelet_certs")
```

If `run_config_stage` doesn't cover cert renewal, manual fix using `run_ssh_command`:
```
# Check cert files
run_ssh_command(hostname="<hostname>", ip="<ip>", command="ls -la /etc/kubernetes/bootstrap-kubelet.conf /var/lib/kubelet/pki/kubelet-client-current.pem")

# If bootstrap conf exists, kubelet can self-renew:
run_ssh_command(hostname="<hostname>", ip="<ip>", command="systemctl restart kubelet", sudo=True)

# If bootstrap conf is also gone → need to re-copy from master (escalate if needed)
```

After cert renewal, verify:
```
run_ssh_command(hostname="<hostname>", ip="<ip>", command="systemctl status kubelet")
run_kubectl(command="get nodes <hostname>", hostname="<hostname>")
```

### KubeletCrash

**Root cause:** Kubelet service crashed — could be OOM, config error, or cert failure.

**Fix — restart kubelet and diagnose crash cause** using `run_ssh_command`:
```
run_ssh_command(hostname="<hostname>", ip="<ip>", command="systemctl restart kubelet", sudo=True)
# Wait ~10s, then check:
run_ssh_command(hostname="<hostname>", ip="<ip>", command="systemctl status kubelet")
run_ssh_command(hostname="<hostname>", ip="<ip>", command="journalctl -u kubelet --no-pager -n 50")
```

If kubelet keeps crashing, check in order:
1. **Certs** — follow KubeletCertFailure path
2. **Disk pressure** — `df -h` on root and /var/lib/kubelet
3. **Config** — `/var/lib/kubelet/config.yaml` exists and is valid

If kubelet recovers, wait ~60s for node to re-register with API server and device plugins to reconnect.

### JobExporterPortConflict

**Root cause:** job-exporter pod can't start because its port is already in use.

**Fix — find and kill the conflicting process** using `run_ssh_command` + `run_kubectl`:
```
# Check what's using the port (typically 9102 or 9104)
run_ssh_command(hostname="<hostname>", ip="<ip>", command="ss -tlnp | grep 9102", sudo=True)

# If it's a stale container:
run_ssh_command(hostname="<hostname>", ip="<ip>", command="crictl ps | grep job-exporter")
run_ssh_command(hostname="<hostname>", ip="<ip>", command="crictl stop <old-container-id>")

# Then restart the pod (daemonset recreates it):
run_kubectl(command="delete pod -n kube-system <job-exporter-pod>", hostname="<hostname>")
```

### Unknown / Other

If the reason doesn't match any known platform fix:
1. Re-read triage evidence carefully
2. If you can identify the fix with confidence — attempt it directly
3. If not — **escalate to human**. Do NOT guess.

---

## Step 4: Verify Fix

After applying the fix, re-run all Layer 2/3 checks from Step 2.

**Verification checklist — all must pass before revalidation:**
- [ ] Kubelet `active (running)`
- [ ] No `unreachable:NoExecute` taint on node
- [ ] `Allocatable.nvidia.com/gpu` matches nvidia-smi count
- [ ] `Allocatable.rdma/hca` matches ibstat count
- [ ] No stale non-system pods
- [ ] No orphaned `PodDeviceEntries` with `-1` DeviceIDs
- [ ] Device plugin pods running and registered

**Outcomes:**

| Result | Action |
|--------|--------|
| All checks pass | → Step 5 (send to revalidation) |
| Same issue persists | Try next remediation → loop back to Step 3 |
| All remediations exhausted | Escalate to human — leave node in `triaged_platform` |
| New issue found | Re-triage: if Layer 1 failed → `triaged_hardware`; if different platform issue → re-diagnose and loop back to Step 3 |

---

## Step 5: Send to Revalidation

After all three layers are verified healthy, submit a revalidation request:

```
submit_validation(
  hostname="<hostname>",
  summary="Platform fix: <what was done>. All 3 layers verified healthy."
)
```

This sends an `admin-validate-node` alert to the Alert Manager, which triggers the platform to schedule a superbench validation job AND transition the node to `validating`.

**⚠️ Do NOT `kubectl uncordon` the node.** Revalidation runs while the node is still cordoned. If validation passes, the platform automatically uncordons the node. Uncordoning manually before validation passes would allow workloads to schedule on an unverified node.

Do NOT use `move_node_status` for revalidation — it only updates the DB status without actually creating a validation job.

The repair agent does NOT wait for validation to complete — that is tracked by the platform.

---

## Execution Model

Platform repair executes directly without user approval — diagnose, fix, verify, and send to revalidation.

| Fix | Behavior |
|-----|----------|
| Delete device plugin pod (daemonset recreates) | Execute directly |
| Kill stale container via `crictl stop` | Execute directly |
| Restart kubelet | Execute directly |
| `run_config_stage` for cert renewal | Execute directly |
| Escalate to human | N/A — just report |

If a fix fails or the issue is beyond the agent's capability, report findings and move on.
No `--execute` flag needed — platform repair is always in execute mode.
