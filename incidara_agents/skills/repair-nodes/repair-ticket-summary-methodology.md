# Repair Ticket Summary Methodology

Use this document for two purposes:
1. **Quality gate** — verify evidence is sufficient before allowing ticket submission
2. **Wording generation** — translate evidence into vendor-facing Chinese summary/reproducer

## Output Contract

Return:
1. `summary` (Chinese, vendor-facing)
2. `reproducer` (Chinese, vendor-facing)
3. `internal_notes` (optional; not submitted unless user asks)
4. `gate_status`: `pass` or `blocked` with missing items list

Do not include markdown tables in the final ticket body.

---

## Evidence Completeness Gate

Before writing any ticket text, verify evidence is sufficient.
If mandatory items are missing, do NOT write the ticket — flag what's missing
and go back to collect it (follow investigation-methodology.md for the relevant branch).

### How to check

Call `get_node_evidence_tool` to see all collected evidence.
Verify the required sources are present for the fault type.
If a required source is missing, the gate is blocked.

### Mandatory (ALL tickets)

- [ ] FaultCode identified
- [ ] SSH reachability confirmed (or explicitly unreachable with details)
- [ ] Node category known (b300, h200, cpu, etc.)
- [ ] Triage reason known (tells which fault branch)

### Mandatory by fault type

PCIe/GPU bandwidth (FaultCode ~ AMDGPUDriverHang, SuperBenchModelPerformanceDegradation; reason ~ gpu-copy-bw, gemm-flops):
- [ ] Benchmark numeric values (actual, baseline or threshold, variance %)
- [ ] Which GPU index / PCI address
- [ ] Live diagnostics: GPU count, nvidia-smi, PCIe errors in dmesg

NVLink (reason ~ nccl-bw, NVLink):
- [ ] Which GPU links affected
- [ ] NVLink benchmark values or NCCL error details
- [ ] Live diagnostics: NVLink link states per GPU, dmesg NVLink errors

IB (reason ~ ib-loopback, IBPortDown, IBAbnormal):
- [ ] Which IB device/port is affected
- [ ] IB benchmark values or UCX/NCCL error excerpts
- [ ] Live diagnostics: IB port states, error counters, dmesg

GPU ECC / GPUUnhealthy (FaultCode ~ GPUUnhealthy, ECC):
- [ ] Which GPU (index + PCI address)
- [ ] ECC count (uncorrectable)
- [ ] Xid code from dmesg if available

NodeCrash (FaultCode ~ NodeCrash):
- [ ] Which IPs reachable / unreachable
- [ ] Any hardware errors found in dmesg or nvidia-smi

FabricManager (reason ~ FabricManagerMismatch):
- [ ] Fabricmanager service status
- [ ] NVSwitch errors in dmesg if available

### If mandatory items are missing

- Do NOT write the ticket. Set `gate_status: blocked` and list missing items.
- If the missing evidence requires SSH that is unreachable:
  - Document the unreachability as the finding
  - Write a conservative ticket with what you have + note the gap in `internal_notes`
  - Set `gate_status: pass` with caveat
- If SSH works but you didn't collect enough:
  - Go back and collect. Follow investigation-methodology.md for the relevant branch.
  - Do not submit incomplete tickets.

---

## Vendor-Safe Translation Rules

### Principle

Give the vendor concrete, reproducible hardware evidence. Include real error messages,
log excerpts, numeric values — the vendor needs these to diagnose the fault.
Strip only the workload identity: job names, model names, user names — anything
that reveals WHAT we were running. Everything else (error messages, MPI prefixes,
peer IPs, NCCL output, benchmark numeric values) is fine.

### What to KEEP (vendor needs concrete evidence)

- Exact numeric values: baseline, actual, variance %, threshold
  - e.g. "PCIe DMA 带宽实测 13.54 GB/s，预期 ≥55 GB/s，偏差 -75%"
- Hardware identifiers: GPU index, PCI address, IB device/port name
  - e.g. "GPU 4 (PCI 0000:ab:00.0)", "mlx5_3 port 1"
- Error messages and log excerpts — from ANY source:
  - dmesg: `NVRM Xid 79 GPU has fallen off the bus`, `PCIe AER uncorrectable error`
  - nvidia-smi: ECC counts, GPU missing, temperature/power anomalies
  - ibstat/ibqueryerrors: port states, error counters
  - fabricmanager logs: NVSwitch errors
  - UCX errors: `ibv_create_ah(...) failed: Connection timed out on mlx5_3`
  - NCCL errors: timeout, transport failure
  - Job stderr/stdout error lines: `CUDA error`, `illegal memory access`, etc.
- Framework output prefixes in logs: `[1,5]<stdout>:`, `[rank0]:`, MPI rank markers — these are fine, they don't reveal the workload
- Peer node IPs in error messages: `dgid=::ffff:192.0.2.10` — fine, doesn't reveal what's running
- NCCL / UCX / MPI diagnostic output — fine, these are communication layer details
- nvidia-smi full output
- IB port states, link widths, counter values
- NVLink link states per GPU
- PCIe AER/DPC errors from dmesg
- Hardware test descriptions (translated from benchmark names, see below)

### What to STRIP (workload identity only)

Strip ONLY things that identify WHAT we were running or reveal our internal cluster stack:

| Strip | Example | Why |
|-------|---------|-----|
| Job/framework names | `superbench_uaback_auto_1C4vZ4v7` | Reveals workload identity |
| User names | `user_name` from job details | Reveals who/what team |
| Model/dataset names | Any reference to specific model or training task | Reveals business workload |
| Platform names | SuperBench, PAI, OpenPAI, LTP | Reveals our platform stack |
| Orchestration layer terms | `Kubernetes`, `kubectl`, `kubelet`, K8s node conditions (`disk_pressure=unknown`, `memory_pressure=unknown`, `ready=unknown`), Kubernetes alert names (`NodeNotReady`, `NodeUnschedulable`) | Reveals internal cluster management stack; hardware vendor has no visibility into our orchestration layer and does not need it |
| Internal CLI commands | `kubectl get node <hostname>`, `kubectl describe node` | Internal tooling the vendor cannot run |

**Translating orchestration signals to vendor-safe language:**
- `NodeNotReady` / K8s node conditions all `unknown` → `节点对外不可达` (node became externally unreachable)
- `disk_pressure=unknown, memory_pressure=unknown, ready=unknown` → `所有服务健康状态均变为 unknown` (all health checks returned unknown)
- Do NOT include `kubectl` commands in the `reproducer` section — use SSH-accessible commands only (`nvidia-smi`, `journalctl`, `dmesg`, `ibstat`, etc.)

Everything else stays. Error messages, infrastructure prefixes, peer IPs,
communication framework output — all fine. The vendor cannot reverse-engineer
our workload from `ibv_create_ah failed: Connection timed out` or `[1,5]<stdout>:`
even with MPI rank markers present.

### Terminology Translation

Benchmark names and internal alert names should be translated to hardware test
descriptions in Chinese. The numeric VALUES from benchmarks are kept.

| Internal term | Vendor-safe Chinese |
|--------------|-------------------|
| `gpu-copy-bw:*_by_dma` | PCIe DMA 带宽测试 |
| `gpu-copy-bw:*_by_sm` | PCIe SM 带宽测试 |
| `nccl-bw:*nvlink-sharp` | NVLink 带宽测试 |
| `ib-loopback` | IB 回环带宽测试 |
| `gemm-flops` | GEMM TFLOPS 测试 |
| `cpu-memory-bw-latency` | CPU 内存带宽和延迟测试 |
| benchmark full path `gpu-copy-bw:perf/cpu_to_gpu4_by_dma_under_numa0_bw` | "PCIe DMA 带宽测试 CPU→GPU4" |
| `NotifyUnvalidatedNodes`, `CordonValidationFailedNodes` | Omit alert name. Describe test result directly |
| `PaiServicePodNotReady/NotRunning` | Omit — not relevant to hardware vendor |
| `task_completion_phrase: ContainerMayFailDueToGpuDeviceEccError` | "测试因 GPU ECC 错误终止" |
| `task_completion_phrase: ContainerUnrecognizedFailed` (empty task_node) | "测试运行时节点不可达" |

### Translation Examples

**Alert summary (internal) → Vendor ticket text:**
```
Internal: NotifyUnvalidatedNodes: gpu-copy-bw:perf/cpu_to_gpu4_by_dma_under_numa0_bw baseline 55.25 actual 13.54 variance -75%

Vendor: PCIe DMA 带宽测试: CPU→GPU4 DMA 带宽实测 13.54 GB/s，预期 ≥55 GB/s，偏差 -75%
```

**Job log with error (internal) → Vendor ticket text:**
```
Internal (from ltp.sh logs or get_job_events):
[1,5]<stdout>:[1776794247.681677] [lg-cmc-demo-r01u01-h200-000001:242922:0] UCX ERROR ibv_create_ah(dlid=49152 sl=0 port=1 src_path_bits=0 dgid=::ffff:192.0.2.10 flow_label=0xffffffff sgid_index=3 traffic_class=106) for RC DEVX QP connect on mlx5_3 failed: Connection timed out

Vendor:
运行多卡通信测试时，IB 连接失败:
[1776794247.681677] [lg-cmc-demo-r01u01-h200-000001:242922:0] UCX ERROR ibv_create_ah(dlid=49152 sl=0 port=1 src_path_bits=0 dgid=::ffff:192.0.2.10 flow_label=0xffffffff sgid_index=3 traffic_class=106) for RC DEVX QP connect on mlx5_3 failed: Connection timed out
```
(Only the job name was removed. Everything else — MPI prefix, peer IP, full error — stays.)

**dmesg excerpt (no stripping needed):**
```
[12345.678] NVRM: Xid 79: GPU 0000:ab:00.0 has fallen off the bus
→ Vendor: (include as-is, this is pure hardware error)
```

**NodeCrash / NodeNotReady (K8s terminology → vendor-safe):**
```
Internal alert: NodeNotReady — node_name: lg-cmc-demo-r01u01-b300-000001 (192.0.2.10)
               ready=unknown, disk_pressure=unknown, memory_pressure=unknown

Vendor 【问题描述】:
节点于 2026-04-20 13:41 UTC 发生崩溃，节点对外不可达，所有服务健康状态均变为 unknown。
故障发生后 SSH 当前恢复可达，nvidia-fabricmanager 服务运行正常。

Vendor 【复现方式】:
通过 SSH 登录节点，检查系统日志（journalctl -xe 或 /var/log/syslog）确认崩溃原因；
执行 nvidia-smi 确认 GPU 状态及 ECC 错误计数。

NOT this (leaks orchestration layer):
✗ "Kubernetes 节点状态上报为 NotReady，ready=unknown，disk_pressure=unknown"
✗ "执行 kubectl get node <hostname> 查看节点状态"
```

---

## Canonical Mapping (from node_pipeline.py)

Use these mappings in order of precedence:

1. `recall for memory upgrade` (from latest action reason):
- summary: `内存需升级`
- reproducer: `sudo dmidecode -t memory 确认内存需升级`

2. `FaultCode in ["UnhealthyGPUNvidiasmi", "NodeGpuCountChanged"]`:
- summary: `GPU 掉卡或 nvidia-smi 有其他异常`
- reproducer: `nvidia-smi`

3. `FaultCode` contains `ECC`:
- summary: `GPU ECC Error`
- reproducer: `nvidia-smi`

4. `FaultCode == SKUMismatch`:
- summary: `机器配置不合规格`
- reproducer: use detail summary text

5. `FaultCode in ["AMDGPUDriverHang", "SuperBenchModelPerformanceDegradation"]`:
- include branches by signal:
  - fabricmanager not running:
    - summary: `nvidia-fabricmanager 启动失败`
    - reproducer: `sudo systemctl status nvidia-fabricmanager`
  - action reason contains `gemm-flops`:
    - summary: `GEMM TFLOPS 下降`
    - reproducer: GEMM TFLOPS 测试及阈值检查
  - action reason contains `ib-loopback`:
    - summary: `IB loopback 带宽下降`
    - reproducer: IB 回环带宽测试, 阈值 >= 42GB/s
  - action reason contains `nccl`:
    - summary: `NCCL 通信性能下降`
    - reproducer: NCCL 延迟/带宽测试
  - action reason contains `cpu-memory-bw-latency`:
    - summary: `CPU 内存带宽和延迟下降`
    - reproducer: MLC 测试及验收阈值
  - action reason contains `gpu-copy-bw`:
    - summary: `GPU p2p/d2h/h2d 性能下降`
    - reproducer: GPU p2p/d2h/h2d 带宽测试
  - action reason contains `disk`:
    - summary: `硬盘速度下降`
    - reproducer: 磁盘速度测试

6. `FaultCode in ["NodeCrash", "AmdGPUNodeCrash"]`:
- if any business IP is unreachable:
  - summary: `无法 SSH 连接到业务 IP ...`
  - reproducer: SSH attempts + `journalctl -u systemd-networkd` check
- if all business IPs are reachable:
  - treat as cannot reproduce crash
  - do not force hardware vendor ticket wording
  - escalate to platform path

7. `FaultCode == IBPortDown`:
- summary: `IB port down`
- reproducer: IB 回环带宽测试

8. `FaultCode == IBPortFlapping`:
- summary: `IB port flapping`
- reproducer: IB 回环带宽测试

9. `FaultCode == IBLinkFlapping`:
- summary: `IB link flapping`
- reproducer: IB 回环带宽测试

10. `FaultCode == NICNameMismatch`:
- summary: use detail summary
- reproducer: `ip addr`

11. `FaultCode == CUDADriverFailure`:
- summary: `CUDADriverFailure: ...`
- reproducer: `运行 GPU 负载`

12. `FaultCode == DiskError`:
- summary: use detail summary
- reproducer: `sudo lsblk; sudo nvme list`

13. `FaultCode == GPUUnhealthy`:
- summary: `GPUUnhealthy`
- reproducer: `nvidia-smi`

14. `FaultCode == GPUDriverHanging`:
- summary: `GPUDriverHanging`
- reproducer: `nvidia-smi`

15. `FaultCode == MMUFault`:
- summary: `MMUFault`
- reproducer: `sudo cat /var/log/kern.log | grep NVRM` or `sudo dmesg | grep NVRM`; run GPU stress test

16. `FaultCode == CPUMemoryLost`:
- summary: `CPUMemoryLost`
- reproducer: `sudo lsmem; sudo cat /proc/meminfo; sudo dmidecode -t memory`

17. `FaultCode == RecallForUpgrade`:
- summary: `RecallForUpgrade`
- reproducer: required memory/disk upgrade statement

18. Fallback from recent action reason:
- reason contains `IBPortDown` or `link down`:
  - summary: use reason text
  - reproducer: IB 回环带宽测试
- reason contains `mmu fault` or `illegal memory access`:
  - summary: use reason text
  - reproducer: NVRM 日志检查 + GPU 压力测试
- reason contains `GPUDriverFailure: system not yet initialized`:
  - summary: use reason text
  - reproducer: NVRM 日志检查 + GPU 压力测试

---

## Alert-First Interpretation Guidance

- When alert context is present, prefer alert + timestamp alignment with the latest transition to avoid stale history noise.
- If FaultCode and alert disagree:
  - treat as conflicting evidence,
  - go back and collect more (follow investigation-methodology.md),
  - use conservative wording in vendor ticket,
  - document discrepancy in `internal_notes`.

## b300 Handling

Default (pipeline-compatible):
- include firmware/Xid addenda in vendor-facing summary/reproducer.

Vendor-facing addenda to append by default for b300:
- `请帮助确认本台机器是否已经更新用于解决 Phison 和 HGX 上的 CX8 通讯问题的固件，这是造成 Onboard CX8 LAN 掉速或掉卡的主要原因。`
- `请帮助确认本台机器是否已经更新用于解决 B300 Xid 194 问题的固件。`
- plus corresponding reproducer/request lines for firmware confirmation.

If user explicitly requests internal-only handling:
- move firmware/Xid checklist to `internal_notes` and keep vendor-facing text concise.

## Unknown Pattern Playbook

When the case does not cleanly match canonical mappings:

1. Start with observed facts only:
   - write symptom + impact + reproducible check
   - avoid asserting unproven root cause
2. Minimum wording quality bar:
   - `summary` includes concrete abnormal signal (error message, check result, numeric value)
   - `reproducer` includes exact command or test description
3. Escalation rule:
   - if inputs conflict, request targeted deep-dive outputs before final wording
4. Output split:
   - vendor-facing text: concise, symptom-oriented, reproducible
   - internal notes: uncertainty, conflicting signals, next diagnostics
5. Reuse across replays:
   - preserve normalized fields (`fault_source`, `symptom`, `impact`, `confidence`, `evidence[]`) so replay validation can compare like-for-like.

## Guardrails

- **Evidence gate first.** Never write a ticket without passing the completeness gate.
- Vendor-facing ticket body must focus on fault symptoms and reproducibility.
- Include real error messages and log excerpts — they are the strongest evidence.
- Strip only workload identity (job names, model names, user names, platform names).
- **Never use Kubernetes/kubectl/kubelet terminology in vendor-facing text.** This includes:
  - K8s node conditions: `NodeNotReady`, `NodeUnschedulable`, `disk_pressure`, `memory_pressure`, `ready=unknown`
  - K8s CLI commands: `kubectl get node`, `kubectl describe node`
  - Internal alert names that expose our stack: `PaiServicePodNotReady`, `NodeUnschedulable`
  - Translate all such signals to hardware-observable terms: "节点对外不可达", "SSH 无法连接", "服务健康状态变为 unknown"
  - Reproducers must only contain commands the vendor can run via SSH: `nvidia-smi`, `journalctl`, `dmesg`, `ibstat`, `ip addr`, etc.
- If evidence is insufficient or conflicting, produce conservative wording and add details under `internal_notes`.
- Do not claim root cause certainty if only alert-level evidence exists.
- Prefer "observed symptom + reproducible check" over speculative diagnosis in vendor-facing text.
