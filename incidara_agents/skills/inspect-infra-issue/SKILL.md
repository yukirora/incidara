---
name: inspect-infra-issue
description: "Investigate infrastructure issues: nodes, switches, GPUs, NVSwitch, NVLink, IB, NIC, disk, BMC, and any other hardware/infra problems. Auto-trigger for tasks containing keywords like 'inspect', 'investigate', 'diagnose', 'check', 'smi hang', 'failure', 'error' with hostnames, IPs, or rack identifiers."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# inspect-infra-issue

Investigate a specific infrastructure finding. Gather evidence, attribute root cause, present to human for approval, record verdict. Spot recurring patterns and delegate to `automate-detection-pattern`.

## When to Use

**Auto-trigger**: Load this skill whenever the task involves investigating, inspecting, diagnosing, or checking an infrastructure issue. This includes any task with keywords like "inspect", "investigate", "diagnose", "check", "smi hang", "failure", "error" combined with node names, switch names, IPs, or rack identifiers.

- Chat UI task from `scan-cluster` (uninvestigated finding)
- Chat UI task from `automate-detection-pattern` (unjudged finding needing verdict)
- Chat UI task from cron rule at `create_task` stage
- User report via Feishu
- Manual: "investigate node h200-gpu123", "check why switch leaf03 has errors"
- Any task containing "smi hang", "inspect the issue", "diagnose", "investigate" with hostnames/IPs

## Available Tools

### Node Investigation (node-ops MCP)
- `get_node_detail(hostname)` — IP, SKU, switch port
- `get_node_alerts(hostname)` — recent alerts
- `get_node_history(hostname, days)` — status transitions
- `get_node_status_history(hostname)` — detailed status history
- `get_node_recent_jobs(hostname, days)` — recent jobs on this node
- `get_nodes_by_status(status)` — find nodes in a given status (cordoned, triaged_*, validating, etc.)
- `get_existing_reasons(hostname)` — past triage reasons
- `probe_ssh(ip, user, timeout)` — check if SSH reachable
- `check_fabricmanager(hostname)` — FM status (H200 only)
- `bmc_query(ip)` — BMC sensor data (temperature, fans, power)
- `bmc_screenshot(ip)` — BMC KVM screenshot (visual hardware state)
- `resolve_ip(ip)` — resolve IP → hostname, category, onboard_id
- `run_database_query(query, database, params)` — run SELECT queries against platform/evidence DB

### Job Investigation (node-ops MCP + job-check skill)
- `get_job_details(job_name)` — job config, framework, resources
- `get_job_events(job_name)` — job lifecycle events (created, running, failed, etc.)
- `get_validation_job(hostname)` — find validation job for a node (scheduled or unscheduled)
- `get_node_recent_jobs(hostname, days)` — find jobs that ran on this node
- For actual log content and error patterns: use `/job-check` skill — `ltp.sh logs` to read container logs, `ltp.sh events` for full event timeline

### Switch Investigation (switch-ops MCP)
- `run_switch_command(ip, command)` — SSH to a switch, run a command
- `list_switches(switch_type?)` — query switch inventory
- `read_collector_log(collector_name, filter?)` — read patrol-cron collector logs

### Fabric Manager Investigation (UFM via SSH)
- SSH to UFM hosts using `ssh xyz-admin@<ufm_ip>` with password from `SWITCH_SSH_PASSWORD` env var
- UFM IPs can be found in the `switch_inventory` table: `SELECT * FROM switch_inventory WHERE type = 'ufm'`
- **OpenSM log** (`/opt/ufm/files/log/opensm.log`) — the primary signal source for fabric-level issues:
  - `grep -c 'HEAVY SWEEP START' /opt/ufm/files/log/opensm.log` — sweep storm count
  - `grep -c 'ERR 1F07' /opt/ufm/files/log/opensm.log` — dead-end path errors (0 = healthy, >0 = fabric routing broken)
  - `grep -c 'ERR 5430' /opt/ufm/files/log/opensm.log` — SA send failures (path query failures)
  - `grep -c 'SUBNET UP' /opt/ufm/files/log/opensm.log` — subnet reconfiguration events
  - `grep 'changed state from ACTIVE to DOWN' /opt/ufm/files/log/opensm.log | tail -10` — ISL port flapping
  - `grep 'trap 131' /opt/ufm/files/log/opensm.log | tail -10` — flow control watchdog events
  - Rotated logs: `/opt/ufm/files/log/opensm.log.1.gz`, `.2.gz`, etc. — use `zcat` for historical analysis
- **Timeline analysis**: compare event rates before vs after the issue to identify escalation phases
- **Uptime check**: `cat /proc/uptime` on UFM host — detect recent restarts

### Feishu Reports (feishu-bitable MCP)
- `get_node_unhealthy_reports_tool(hostname)` — user-submitted "Node Unhealthy" reports
- `get_unprocessed_reports_tool()` — unprocessed Feishu reports

### Evidence + Verdict
- `save_evidence_tool(hostname, source, content, finding_id=None)` — persist raw investigation artifacts; always pass `finding_id` when investigating a finding (agent-evidence MCP)
- `get_node_evidence_tool(hostname)` — past evidence for one host
- `search_evidence_tool(summary_like, category?)` — cross-host evidence search
- `record_verdict(finding_id, verdict)` — confirmed or rejected (patrol-cron MCP)
- `list_findings(rule_id, target_id, resolved)` — past findings

### Action (node-ops MCP)
- `execute_node_action(hostname, action, triaged_label, summary)` — explicitly cordon or drain a node via the Alert Manager pipeline. `action` is one of: `"cordon"` (NoSchedule taint, stops new workloads), `"drain"` (evicts existing pods), `"alert"` (notify only). This is the single authoritative path — do NOT use `kubectl cordon/drain` directly.
- `get_agent_active_tasks(agent_id)` — check active tasks before any delegation

**Do NOT delegate** to other agents. You have the tool to act directly. For patterns, load the `/automate-detection-pattern` skill.

## Finding Format

A patrol_findings row:

| Field | Description |
|-------|-------------|
| `finding_id` | Unique ID (used for `record_verdict`) |
| `rule_id` | Which rule produced this finding |
| `target_id` | Hostname (node) or switch ID — depends on `target_type` |
| `target_type` | "node" or "switch" |
| `severity` | "critical", "warning", or "info" |
| `action` | Recommended action: "cordon_node", "cordon_switch_nodes", "alert", "create_task" |
| `evidence` | JSONB — rule-specific (e.g., fail_count, port_deltas, error messages) |
| `verdict` | NULL, "confirmed", or "rejected" — set by `record_verdict` |
| `resolved` | boolean — true after human judgment |
| `active` | boolean — true if target is still failing, false if recovered |
| `deactivated_at` | When the target recovered (null if still active) |
| `active_duration_sec` | How long the problem has been/was active |
| `detected_at` | When the finding was created |

## Workflow

### Step 1: Plan — Start Fresh, Form Your Own Questions

**The delegation prompt is background context, not instructions.** It may be wrong, incomplete, or focused on the wrong issue. Do NOT assume anything the prompt says is true.

Before touching any tools:
1. **Read the finding** — `list_findings(target_id=...)` or check the `evidence` field. What does the RAW data say? Not what the prompt claims.
2. **Formulate YOUR OWN investigation plan:**
   - **What questions must I answer?** (e.g., "Is the CRC error ongoing?", "Which nodes are behind this switch port?", "Is the prompt focused on the right issue or is there a bigger problem?")
   - **What evidence do I need for each question?** (e.g., current switch counters, downstream node status, past findings)
   - **What's my likely verdict path?** (e.g., "If errors stopped → rejected. If ongoing + nodes affected → confirmed hardware.")
3. **Look for what the prompt missed** — the finding might say "Xid errors" but the node might actually have NIC firmware issues flooding dmesg. The original finding might be wrong. Trust the data, not the prompt.

**For every finding, you must answer:**
- Is this still happening right now, or was it transient?
- What's the blast radius — isolated to one target or affecting multiple?
- What's the root cause category — hardware, software/config, or transient?
- **What is the exact time the issue happened?** — Extract the timestamp from the finding's `detected_at`, user report, or job failure logs. You need this to check logs **before and after** the event.

**For fabric-level issues (multiple nodes across sites failing simultaneously, IB RETRY_EXC, SM storms), also answer:**
- **Proactively SSH to UFM hosts** and check OpenSM logs — do NOT wait for the user to ask
- What happened in the OpenSM log **before** the issue time? (normal pattern baseline)
- What changed **at** the issue time? (the trigger event — port flap, sweep storm, trap flood)
- What escalated **after** the issue time? (dead-end paths, SA failures, routing collapse)
- Compare event rates before vs after to identify the escalation phase

**For switch findings, also answer:**
- Which nodes are downstream of this switch/port? Are they healthy?
- Is this one bad port, or is the whole switch failing?

**For node findings, also answer:**
- Are other nodes in the same rack/switch showing the same symptoms?
- Is there a job that triggered this, or did it happen idle?

Write your plan. Then execute it step by step, gathering evidence for each question.

### Step 2: Scope the Investigation Efficiently

**⚠️ Critical: Use database queries for inventory, NOT one-by-one SSH probes.**

Before SSHing into any node, determine the full scope:

**If the user reports multiple IPs or "all nodes in a site":**
1. **Query the DB for full inventory** — use `run_database_query` to get all nodes and IPs at once:
   ```
   SELECT hostname, ip, mgmt_ip, sku, category
   FROM ltp_sdk.physical_node_onboard_records
   WHERE ip::text LIKE '%<site_prefix>%' AND category = '<gpu_type>'
   ORDER BY id DESC
   ```
   Or filter by rack: use `get_node_detail(hostname)` on one node, note the site, then query all.
2. **Resolve IPs to hostnames** — use `resolve_ip(ip)` to map each IP.
3. **Check existing collectors** — `read_collector_log(collector_name)` may have already probed `nvidia-smi`, GPU health, or IB status across the fleet. Prefer collector data over new SSH probes.
4. **Only then SSH** to the specific nodes that need investigation — not the entire fleet.

**Never do this:** SSH into 50+ nodes one at a time to probe each one.
**Do this instead:** One DB query → identify affected nodes → SSH only to those.

### Step 3: Gather Evidence

**Always check the finding's evidence field FIRST** — before SSHing or running commands:
- `list_findings(rule_id=...)` or `get_rule_detail(rule_id)` — the `evidence` field may contain raw dmesg lines, collector output, or other data that triggered the detection.
- If the evidence has raw output (e.g., `raw_dmesg` lines), you can verify timestamps, error codes, and patterns without even SSHing. The dmesg buffer may have rotated by now — the evidence field is the only permanent record.
- Only SSH if the evidence field doesn't contain enough data to reach a conclusion.

**⚠️ Always wrap GPU commands with `timeout 30`.** nvidia-smi can hang on failed NVLink/NVSwitch. A hung SSH session blocks the investigation. Use `timeout 30` — long enough for healthy multi-GPU systems but short enough to not block when hung:
```
timeout 30 nvidia-smi -L; echo "exit_code=$?"
timeout 30 nvidia-smi nvlink -s 2>/dev/null; echo "exit_code=$?"
timeout 30 nvidia-smi --query-gpu=index,name,ecc.errors.uncorrected.volatile.total --format=csv,noheader; echo "exit_code=$?"
```
- exit_code=0 → success. exit_code=124 → timeout (GPU driver hung). exit_code=1 → error.
- **When nvidia-smi times out (exit_code=124), do NOT keep retrying** — the hang is the diagnostic signal. Record it and move to dmesg investigation.
- On healthy H200/B300 nodes, `nvidia-smi -L` typically completes in 2-5s.
- For dmesg, no timeout needed — use `sudo dmesg -T | grep ... | tail -30`.

**For node-type targets:**
- `get_node_detail(hostname)` → IP, SKU, switch port
- **Check node status first:** `get_node_history(hostname, days=7)` — is the node already cordoned/ua/triaged_hardware? If yes, the issue is already being handled. Confirm the verdict, save evidence, but don't propose new action. The repair pipeline is already working on it.
- `check_fabricmanager(hostname)` — FM status (H200)
- `ssh operator@<ip> "sudo dmesg -T | grep -i 'error\|fail\|AER\|Xid' | tail -50"` — errors with human-readable timestamps. Always use `dmesg -T`, never raw dmesg with boot seconds. For Xid code meanings and required actions, see `../triage-nodes/references/xid-reference.md`.
- `ssh operator@<ip> "nvidia-smi -q -d HEALTH | grep -A2 'GPU\|ECC\|Temp'"`
- `ssh operator@<ip> "ibstat | grep -A5 'State\|Rate\|Errors'"`
- **Always check time**: are errors actively happening now, or all historical? Compare `dmesg -T` timestamps to current time.
- **Timeline comparison**: when did the issue start? What was the pattern BEFORE vs AFTER? Use rotated logs (`zcat /var/log/...`) or `journalctl --list-boots` to find the boundary between normal and abnormal. Count events per hour to quantify the escalation.

**When the finding comes from `large_job_failure_v1` or `job_failure_investigation`:**
- **Start with job logs, not hardware checks.** The job container logs tell you the actual error
  (CUDA error, NCCL timeout, OOM, bus error) which points to the hardware subsystem.
- `fetch_job_logs(job_key=<job_name>)` (patrol-cron MCP) — fetch the failed job's container
  stderr/stdout. Look for: CUDA errors, NCCL timeouts, GPU bus errors, OOM kills.
- Only THEN check the specific hardware subsystem the job logs point to.
- **Common patterns:**
  - NCCL timeout → IB link issue or NVLink down (check `ibstat`, `nvidia-smi nvlink`)
  - CUDA error: illegal address → GPU memory fault (check ECC errors)
  - GPU bus error → GPU or NVSwitch hardware fault (check dmesg for Xid 79)
  - OOM → not a hardware issue, likely job config problem → reject finding

**General investigation methodology — trace the signal chain:**
Every finding has a `rule_id` and `evidence` field. Reason backwards from what the rule detected:
1. **What signal did the rule detect?** (from `rule_id` and `evidence.summary`)
2. **What data source produced that signal?** (from `rule_id` → collector name → data source type)
3. **What tool gives me that raw data?** → use it to see the full picture, not just what the rule flagged
4. **What does the raw data point to?** → investigate that subsystem

Example: `large_job_failure_v1` detected 3 jobs failing at rank 36.
- Signal: job task failures
- Data source: job logs (OpenPAI)
- Tool: `fetch_job_logs(job_key=...)` → see the actual CUDA/NCCL error
- Raw data points to: NCCL timeout → check IB; CUDA error → check GPU

Example: `b300_nic_health_v1` detected IB CRC errors.
- Signal: IB port error counters
- Data source: node logs (ibstat counters)
- Tool: `ssh_run(hostname, "ibstat")` → see current port state and error counts
- Raw data points to: specific port with errors → check cable/switch port

Example: `nvidia_ecc_error_v1` detected uncorrected ECC.
- Signal: GPU ECC counter increase
- Data source: node logs (nvidia-smi)
- Tool: `ssh_run(hostname, "nvidia-smi -q -d ECC")` → see which GPU, how many errors
- Raw data points to: specific GPU → check dmesg for Xid errors on that GPU

**CRITICAL: Verify workload impact.** The ultimate verification signal. If the node is running jobs successfully, the hardware is functionally OK regardless of dmesg noise:
  - `get_node_recent_jobs(hostname, days=7)` — any jobs on this node?
  - `get_job_events(job_name)` — did they succeed or fail?
  - **If jobs are RUNNING** → use `/job-check` to read container logs: `ltp.sh logs <user>~<job>` for output/errors, `ltp.sh events <user>~<job>` for lifecycle. `nvidia-smi` for GPU utilization — 100% utilization with normal temps/power = workload is actively running correctly.
  - **The job is the ground truth**: dmesg errors + job running correctly at full utilization = hardware functionally OK. dmesg errors + job failing = confirmed impact. No dmesg errors but job failing = problem invisible to dmesg.

**For switch-type targets:**
- Read the finding's `evidence` field — it already contains what the rule detected (port errors, uptime, PSU status, etc.)
- Verify via `run_switch_command(ip, command)` — e.g., `run_switch_command(ip, "show interfaces counters errors")` for Ruijie, `run_switch_command(ip, "show interfaces ib")` for IB
- Check connected nodes: `get_node_detail` for nodes connected to this switch — are they showing errors too?
- Key question: **"Is this a switch problem affecting nodes, or a single port issue?"**

**For fabric-level targets (UFM/SM/OpenSM issues):**
- **Proactively SSH to UFM hosts** and check OpenSM logs — do NOT wait for the user to ask
- Query `switch_inventory` table to find UFM IPs: `SELECT * FROM switch_inventory WHERE type = 'ufm'`
- Check OpenSM log at the **exact time the issue happened** — look before, at, and after:
  - **Before** (baseline): what does the normal log pattern look like? (sweep rate, error count)
  - **At** (trigger): what event caused the disruption? (port flap, trap flood, SM restart)
  - **After** (escalation): how did it escalate? (dead-end paths, SA failures, routing collapse)
- Compare event counts before vs after to identify the escalation phase
- Use rotated logs (`opensm.log.1.gz`, `.2.gz`) with `zcat` for historical comparison
- Key question: **"Is this a fabric management issue (SM/UFM) rather than individual node hardware?"**
- If multiple nodes across different sites fail simultaneously with IB errors, **always investigate the fabric manager first** — node hardware would not cause simultaneous cross-site failures

### Step 4: Attribute Root Cause

**For node targets:**

| Signal | Category |
|--------|----------|
| dmesg AER errors, Xid 79/48/64/95/109/158, ECC > 0, FM down, IB port errors | **HARDWARE** — see `../triage-nodes/references/xid-reference.md` for Xid action per code |
| FM config mismatch, driver version wrong, k8s pod issues | **PLATFORM** |
| AssertionError, OOMKilled, exit code 1, user-specific error | **USER CODE** |
| No clear signal, single occurrence | **TRANSIENT** |

**For switch targets:**

| Signal | Category |
|--------|----------|
| Port CRC/FCS errors growing, multiple ports failing, PSU down | **HARDWARE** |
| Switch recently rebooted (short uptime), config drift | **TRANSIENT** or **CONFIG** |
| Single port with errors after cable reseat | **CABLE/TRANSCEIVER** |

**For fabric-level targets (UFM/SM):**

| Signal | Category |
|--------|----------|
| ISL port flapping continuously (ACTIVE→DOWN loop), same port thousands of times | **HARDWARE** (bad cable/transceiver/switch port) |
| SM heavy sweep storm with ERR 1F07 (dead-end path) | **FABRIC** (routing table corruption from flapping) |
| SM SA send failures (ERR 5430) spiking | **FABRIC** (path resolution failing — causes RETRY_EXC on nodes) |
| UFM service restart with HA failover, zero fabric impact | **TRANSIENT** (system working as designed) |
| No standby SM configured (single point of failure) | **CONFIG** |

**Impact assessment — REQUIRED before verdict:**

| Impact | Default Verdict | Example |
|--------|-----------------|---------|
| **Real damage**: data loss, job failures, ongoing errors, nodes down | **confirmed** | Xid 79 + job crash, IB link down + 8 nodes offline |
| **No real impact**: auto-recovered, HA failover worked, zero downstream effect | **rejected** | UFM restart with no fabric disruption, transient Xid with job running fine |
| **Uncertain**: can't verify impact yet | **watching** (don't confirm) | Intermittent CRC errors but no job failures observed yet |

**Key principle: "System working as designed" is not a finding.** If a component crashed but:
- HA/redundancy kicked in successfully
- Zero downstream services/jobs/nodes were affected
- The system fully recovered without intervention

→ This is **the redundancy system doing its job**. Verdict: **rejected**. The event genuinely happened but is not actionable.

Common "no impact" patterns to reject:
- UFM/switch restart with zero fabric disruption and HA failover
- Transient Xid with job running at full utilization and no errors
- Single-port CRC errors on idle port with no connected workload
- BMC watchdog reset where node came back clean and jobs unaffected

### Step 5: Self-Review — Before Presenting to Human

Ask yourself before presenting:

1. **"Did this problem have real impact?"** — if auto-recovered with zero downstream effect → reject (not confirm). "System working as designed" is not actionable.
2. **"Did this problem affect other targets?"** — search evidence DB for similar cases.
3. **"Did I verify workload impact?"** — actual job logs, not just GPU utilization.
4. **"What evidence am I still missing?"** — secondary signals, correlated events.
5. **"Is this a pattern?"** — how many confirmed across the fleet? Recurring or false positive pattern?
6. **"Am I confirming because the event happened, or because it matters?"** — an event happening ≠ actionable finding. Only confirm if there's real, lasting impact.

**Before recommending rule creation, you MUST establish:**

7. **"What does the NORMAL pattern look like?"** — Before the issue, what was the baseline? (e.g., "SM activity rate was ~1K/s for 7 days", "ERR 1F07 count was 0 for 6.7 days", "IB symbol errors were 0 on all ports"). Without knowing the normal baseline, you cannot define a threshold — any threshold would be a guess that may cause massive false positives.

8. **"What is the VERTEX (the anti-pattern)?"** — The single signal that crossed from normal to abnormal. Not just "errors increased" — but the exact transition point where the system went from healthy to broken. For example:
   - "ERR 1F07 was 0 for 7 days, then jumped to 61,597 in 1 hour — the vertex is the first non-zero ERR 1F07"
   - "ISL port was flapping for 7 days without fabric impact, but fabric broke when trap 131 (FC watchdog) spiked to 1,310/hour — the vertex is trap 131 exceeding ~100/hour"
   - "SM activity was ~1K/s normally, spiked to 14K/s — but 14K/s alone didn't kill jobs, the SA send failures (ERR 5430) at 6,058/hour did — the vertex is ERR 5430 exceeding ~200/hour"

9. **"Why won't this rule fire on normal traffic?"** — What distinguishes the abnormal pattern from normal operation? If you can't answer this, the rule is overfitted and will cause false positives. The anti-pattern must be **absent during normal operation**, not just "present during the issue".

If gaps found → go back to Step 3. Loop until confident.

### Step 5: Select Action — Cordon vs Drain vs Alert vs Create Task

| Symptom | Action | Why |
|---------|--------|-----|
| `nvidia-smi` hangs (timeout) | **`drain`** | GPU driver is dead — any workload on this node is already stuck. Drain evicts running pods so they can reschedule elsewhere. Cordon alone leaves dead pods running. |
| NVLink/NVSwitch failure (smi hang + RxDetect errors) | **`drain`** | Same as above — GPU fabric is down. |
| GPU ECC/Xid errors but nvidia-smi works | **`cordon`** | Node can still serve running workloads until they complete. Cordon prevents new scheduling. |
| IB port errors, NIC errors, disk errors | **`cordon`** | Workloads may still be running. Cordon allows graceful completion. |
| Node physically offline / no SSH / no BMC | **`alert`** | Cannot act remotely — alert triage/repair pipeline. |
| BMC reachable but node won't boot | **`alert`** | Needs manual intervention (e.g., BMC power cycle). |
| Switch/fabric issue (SM storm, UFM instability, ISL flapping) | **`create_task`** | Not a node problem — don't cordon healthy nodes. Create investigation task for fabric-level diagnosis and vendor RMA if needed. |
| Transient, no ongoing issue | **none** | Don't act on transient noise. |

**Rule of thumb:** If `nvidia-smi` hangs or returns errors → always **drain**. If node is reachable and GPU works but has errors → **cordon**. If node is dead → **alert**. If the problem is at the fabric/switch level (not individual nodes) → **create_task**.

### Step 6: Present to Human — Investigation + Pattern + Action

Present ALL three together. Don't split them into separate steps:

```
## Investigation Result
**Target**: {target_id} ({target_type})
**Symptom**: {description}
**Root Cause**: {hardware | platform | user code | transient | cable}
**Evidence**: {summary}

## Pattern Decision
- Rule: {rule_id}
- Total findings: {n} (confirmed: {x}, rejected: {y}, unjudged: {z})
- This target recurring? {yes/no}
- Same root cause on other targets? {yes/no — which targets}
- Verdict: {no pattern | watching | recurring | false_positive_pattern}

## Recommended Actions
1. [Pattern action]: {delegate to automate-detection-pattern OR "watching — no action"}
2. [Node action]: {cordon / drain / alert / create_task / none}
3. [Concrete detection signal]: {if pattern found, identify ONE specific, collectable signal that could detect this issue proactively — e.g., "ERR 1F07 count > 0 in opensm.log", "sminfo activity rate > 5K/s sustained", "IB port link_downed counter delta > 0 on multiple nodes simultaneously"}
4. [Reasoning]

Awaiting your approval.
```

**Verdict options:**
- `no pattern` — 1-2 occurrences, not enough data. No action.
- `recurring` — 3+ confirmed with same root cause → delegate to automate-detection-pattern for new rule:

```
delegate_to_agent("detection",
  title="Recurring pattern: {description}",
  prompt="Pattern: {root cause description}\nConfirmed occurrences: {count}\nSample evidence: {summary}\nTarget type: {node|switch|job|fabric}\n\nConcrete detection signal: {the ONE specific collectable signal identified during investigation — e.g., 'ERR 1F07 count in opensm.log > 0', 'IB port link_downed delta > 0 on >3 nodes simultaneously', 'sminfo activity rate > 5K/s for >10s'}\n\nUse /automate-detection-pattern to create a detection rule for this pattern.",
  completion_mode="manual"
)
```

- `false_positive_pattern` — many rejected, common cause → delegate to automate-detection-pattern for refinement:

```
delegate_to_agent("detection",
  title="False positive pattern: {description}",
  prompt="Rule: {rule_id} has {n} rejected findings with the same cause: {pattern description}.\n\nFix: the rule should exclude {exclude_condition}.\nConfirmed: {x}, Rejected: {y}\n\nUse /automate-detection-pattern to refine this rule.",
  completion_mode="manual"
)
```

**Concrete signal identification — REQUIRED when pattern is recurring:**
When you identify a recurring pattern, you MUST also identify **one concrete, collectable signal** that could proactively detect this issue in the future. This signal must be:
- **Specific**: a single counter, log line pattern, or metric — not "check logs for errors"
- **Collectable**: obtainable via SSH command, sysfs path, or existing collector — not requiring new infrastructure
- **Binary or thresholdable**: either 0 vs non-zero (e.g., ERR 1F07 count), or has a clear normal vs abnormal threshold (e.g., SM activity rate >5K/s vs ~1K/s baseline)
- **Lead time**: ideally detectable before the impact (e.g., ISL flapping detected hours before fabric collapse)
- **Vertex-based**: the threshold must be derived from comparing the **normal baseline** (before the issue) vs the **abnormal pattern** (during the issue). The threshold is the point where the signal crosses from normal to abnormal — the "vertex". Without establishing the normal baseline first, any threshold is a guess that will cause false positives.

Present the signal as: "Signal: `{exact command or path}` → normal: `{value for X days}`, threshold: `{value}` (vertex: `{what changed at the transition point}`)"

- `watching` — 1-2 confirmed, not enough to automate yet

**Do NOT act until approved.** Present, wait, then execute.

### Step 7: Execute (after human approval)

- `record_verdict(finding_id, "confirmed"|"rejected")` — always record verdict
- `create_finding(...)` if no finding exists yet — **save the returned `finding_id`**
- `save_evidence_tool(hostname, source, content, finding_id=finding_id, ...)` — **always link raw evidence to the finding** so the detection agent can learn from it later
- `execute_node_action(hostname, action=...)` — cordon/drain/alert
- `delegate_to_agent("detection", title, prompt, completion_mode="manual")` — for patterns

## Important Rules

- **Never uncordon nodes** — platform auto-uncordons after validation
- **Human approval before any action** — present findings first, wait for explicit approval, then execute
- **If permission is denied, STOP.** A denied `execute_node_action`, `submit_rma_ticket`, or `reset_node` means the human explicitly rejected the action. Do NOT retry or find alternative ways to achieve the same outcome. Report "Action was denied by user" and move on.
- **Always record verdict**
- **Step 6 pattern decision is REQUIRED** — present before asking for approval
- **3+ confirmed = recurring** → delegate to automate-detection-pattern
- **Check evidence field first** — raw data survives when dmesg rotates
