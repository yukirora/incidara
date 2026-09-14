# NVIDIA Xid Error Reference

**Source**: [NVIDIA Xid Errors — Analyzing Xid Errors with the Xid Catalog](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)  
**Catalog download**: [Xid-Catalog.xlsx](https://docs.nvidia.com/deploy/xid-errors/_downloads/4586dadb59119a55d1e93a181caa4272/Xid-Catalog.xlsx)  
**Applies to**: Ampere and newer (A100, H100, B100/B200/GB200). For Volta and older see the [archive](https://docs.nvidia.com/deploy/xid-errors/archive/index.html).

---

## How to read an Xid in dmesg

```
NVRM: Xid (PCI:0000:BB:DF): <XID_NUMBER> ...
```

- `BB:DF` = PCIe bus:device.function → maps to a specific GPU via `nvidia-smi -q | grep -A5 "GPU 00000000:BB:DF"`
- The numbers after the Xid code are engine/context state — useful only for Xid 74/144-150 (NVLink errors)

---

## Immediate interpretation: hardware vs. app

| Category | Xid codes | Meaning |
|---|---|---|
| **Definitely hardware** | 48, 63, 64, 74, 79, 92, 95, 109, 110, 119, 120, 136, 140, 143, 144–150, 158 | GPU/memory/NVLink failure — requires RESET_GPU or RESTART_BM |
| **Likely hardware, run diags** | 13 (repeat, same TPC/GPC), 31 (repeat, same GPU), 46, 62 | Could be hardware; follow workflow to rule out app |
| **Likely app/user code** | 13 (solo, no burst), 31 (different GPUs), 32, 37, 69, 80 | App passed bad data or illegal instructions |
| **Sympathetic / secondary** | 45, 94, 137, 141 | Caused by another error — trace the primary Xid |
| **Informational / ignore** | 14, 43, 63, 106, 107, 154 | No action unless seen repeatedly |

---

## Key Xids for H100/H200 and B300 fleet

### Xid 13 — Graphics Engine Exception
- **H100**: YES | **B300**: YES
- Immediate: `RESTART_APP`
- Investigatory: `WORKFLOW_XID_13`
- Meaning: General user app fault (out-of-bounds, illegal instruction). In rare cases hardware or driver bug.
- **Triage rule**: Repeat on same TPC+GPC → possible HW, run DCGM/FieldDiag. Solo, no burst → app issue.

### Xid 31 — GPU Memory Page Fault (MMU)
- **H100**: YES | **B300**: YES
- Immediate: `RESTART_APP`
- Investigatory: `WORKFLOW_XID_31`
- Meaning: Illegal address access by an application unit. Usually app bug, can be driver/HW.
- **Triage rule**: Repeat faults to same GPU → possible HW. Repeat to different GPUs → app issue.

### Xid 45 — Preemptive Removal
- **H100**: YES | **B300**: YES
- Immediate: `WORKFLOW_XID_45` → Solo: RESTART_FM; Not solo: IGNORE
- Meaning: GPU app cleanup after a previous error (almost always Xid 48/DBE). **Secondary error** — always look for the primary Xid that caused it.

### Xid 48 — Double Bit ECC Error (DBE)
- **H100**: YES | **B300**: YES
- Immediate/Investigatory: `WORKFLOW_XID_48`
- Meaning: Uncorrectable DRAM ECC error. **Definitive hardware fault.**
- Solo: drain and run FieldDiag. With Xid 63/64: follow those. Check SRAM threshold flag.
- In dmesg often accompanied by: Xid 45, Xid 63/64, or Xid 95.

### Xid 63 — GPU Memory Remapping Event
- **H100**: YES | **B300**: YES
- Immediate: `IGNORE` | Investigatory: `IGNORE`
- Meaning: Row remapping occurred. GPU retired a bad DRAM row and remapped it. Normal wear — only act if remapping failures (Xid 64) follow.

### Xid 64 — GPU Memory Remapping Failure
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU`
- Meaning: Row remapping table exhausted — no more spare rows. **Hardware fault**, GPU needs replacement.

### Xid 74 — NVLink Error (H100 only)
- **H100**: YES | **B300**: NO
- Immediate: `WORKFLOW_NVLINK_ERR`
- Meaning: NVLink hardware error. Extract hex registers from the Xid message and evaluate bits per NVIDIA workflow. Repeat on same link (>2x) → report hardware bug.

### Xid 79 — GPU Has Fallen Off the Bus
- **H100**: YES | **B300**: YES
- Immediate: `RESTART_BM`
- Meaning: GPU is no longer accessible over PCIe. **Severe hardware failure** — node must be rebooted. If repeated, GPU or PCIe slot is bad.

### Xid 92 — Excessive Single-Bit ECC Errors
- **H100**: YES | **B300**: YES
- Immediate: `IGNORE` | Investigatory: `CONTACT_SUPPORT`
- Meaning: High SBE rate — correctable errors happening too fast. Monitor: if escalates to Xid 48 (DBE) → hardware fault.

### Xid 94 — Contained Memory Error
- **H100**: YES | **B300**: YES
- Immediate: `RESTART_APP`
- Investigatory: `IGNORE (sympathetic)`
- Meaning: Contained uncorrectable error — app context affected but GPU survives. Often appears with Xid 48. **Secondary — find primary Xid.**

### Xid 95 — Uncontained Memory Error
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU`
- Investigatory: `IGNORE (sympathetic)`
- Meaning: Uncontained uncorrectable error — GPU state may be corrupt, reset required. Often appears with Xid 48. **Secondary — find primary Xid.**

### Xid 109 — Context Switch Timeout
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU`
- Meaning: GPU hung during context switch. **Hardware-level GPU hang** — reset required.

### Xid 119 — GSP RPC Timeout
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU` | Investigatory: `INVESTIGATE_SW`
- Meaning: GPU System Processor (firmware) RPC timed out. Can be driver bug or GPU firmware issue. Reset GPU; if recurring, report to NVIDIA.

### Xid 120 — GSP Error
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU` | Investigatory: `INVESTIGATE_SW`
- Meaning: GPU System Processor error. Similar to Xid 119 — driver/firmware issue.

### Xid 136 — NVLink ALI Training Failed (H100 only)
- **H100**: YES | **B300**: NO
- Immediate: `RESET_GPU` | Investigatory: `INVESTIGATE_LINK_SI`
- Meaning: NVLink Active Link Initialization (ALI) training failed. Check link mechanical connections and SI telemetry.

### Xid 140 — ECC Unrecovered Error
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU`
- Meaning: Unrecoverable ECC error. **Definitive hardware fault.** GPU must be reset; if recurring, replace.

### Xid 143 — GPU Initialization Error
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU`
- Meaning: GPU failed to initialize. Try reset; if it persists across reboots → GPU hardware failure.

### Xid 144–150 — NVLink5 Errors (B300/GB200 only)
- **H100**: NO | **B300**: YES
- Immediate/Investigatory: `WORKFLOW_NVLINK5_ERR`
- Meaning: NVLink5 subsystem errors (SAW, RLW, TLW, TREX, NVLPW_CTRL, NETIR, MSE). **Requires decoding** — extract `intrInfo` and `errorStatus` from the Xid message and evaluate using the "XID 144-150 Decode" sheet in the catalog.
- These are B300-specific; H100 uses Xid 74 for NVLink.

### Xid 158 — GPU Fatal Timeout
- **H100**: YES | **B300**: YES
- Immediate: `RESET_GPU`
- Meaning: GPU fatal timeout — GPU is unresponsive. **Hard hardware failure**, requires reset. If it recurs, GPU needs replacement.

---

## Resolution bucket definitions

| Bucket | Action |
|---|---|
| `RESTART_APP` | Kill and restart the user's application. No GPU/node restart needed. |
| `RESET_GPU` | Reset the GPU (MIG reset or drain from k8s + `nvidia-smi -r`). See [GPU Debug Guidelines](https://docs.nvidia.com/deploy/gpu-debug-guidelines/index.html). |
| `RESTART_BM` | Reboot the bare metal node. |
| `RESTART_FM` | Restart the Fabric Manager service (`systemctl restart nvidia-fabricmanager`). |
| `IGNORE` | No action required. |
| `IGNORE (sympathetic)` | Secondary error from a primary fault — address the primary Xid first. |
| `CONTACT_SUPPORT` | Escalate to NVIDIA support. |
| `CHECK_APP/CUDA` | Likely user application bug — run `cuda-gdb` or `compute-sanitizer`. |
| `INVESTIGATE_SW` | Driver or firmware bug — update drivers, report to NVIDIA if persists. |
| `CHECK_MECHANICALS` | Check physical connections: PCIe slot, NVLink cables, power connectors. |
| `WORKFLOW_XID_13` | Triage pattern: repeat same TPC/GPC → hardware diag; solo no burst → app. |
| `WORKFLOW_XID_31` | Triage pattern: repeat same GPU → hardware diag; different GPUs → app. |
| `WORKFLOW_XID_45` | Solo → RESTART_FM; with other Xids → ignore (secondary to primary). |
| `WORKFLOW_XID_48` | DBE: solo → DRAIN_AND_RESET + FieldDiag; with Xid 63/64 → follow those. |
| `WORKFLOW_NVLINK_ERR` | H100 NVLink: decode hex registers from Xid 74 message, evaluate bits. |
| `WORKFLOW_NVLINK5_ERR` | B300 NVLink5: decode `intrInfo`/`errorStatus` from Xid 144–150 message. |

---

## Cluster-specific patterns we've seen

| Pattern | Observed Xid(s) | Conclusion |
|---|---|---|
| GPU falls off bus mid-job | 79 (+ often 45) | GPU hardware failure → RMA |
| DRAM row remapping exhausted | 64 (+ 63 history) | GPU DRAM failure → RMA |
| High ECC → job crash | 48 → 45 → job killed | DBE hardware fault → RMA |
| NVSwitch disrupts many nodes simultaneously | 74 (multiple nodes same time) | NVSwitch/FM fabric issue — not individual GPU |
| Containerd unable to pull image / sandbox create fails | 95 (on one node) | Uncontained error → containerd state corrupt → node restart |
| B300 NVLink training failure at boot | 144–150 | NVLink5 link SI issue → check mechanical + report |
