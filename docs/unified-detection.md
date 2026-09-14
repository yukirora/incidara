# Unified Detection Architecture — v2

## The Key Question: What Goes Where?

```
RULE (service)     vs    MCP SERVER (tools)    vs    AGENT (LLM)
─────────────────       ──────────────────        ─────────────────
Always running           On-demand access           On-demand judgment
Deterministic            Data + action              Investigation + correlation
No LLM needed            Agent calls tools          Complex reasoning
Simple threshold         Read DB, SSH, API          "Is this switch or node?"
Fast, cheap              Submit alerts              "Should we cordon 20 nodes?"
```

The current system already has this split but it's implicit. Making it explicit:

| Layer | Runs | Knows | Decides | Examples |
|---|---|---|---|---|
| **Rules** | Always (Prometheus, cron) | Metrics, SSH results | Thresholds → fire alert | ECC > 0 → cordon. PSU down → alert. |
| **MCP Server** | On demand (agent calls) | DB, SSH, API, alert-manager | Nothing — just provides data + actions | `check_switch()`, `get_node_alerts()`, `submit_alert()` |
| **Agent** | On demand (Chat UI task) | Everything MCP provides | Judgment, correlation, investigation | "This switch is causing 20 node failures, escalate switch not nodes" |

## Layer 1: Rules (Services, Always Running)

Rules are **deterministic, threshold-based, always-on**. No LLM. No agent. They're the immune system — automatic, fast, cheap.

### Existing Rules (Prometheus alerting rules)

Already deployed, already working. These fire alerts to alert-manager which auto-cordons:

```yaml
# gpu.rules — already exists
- alert: NvidiaSmiDoubleEccError
  expr: nvidiasmi_ecc_error_count{type="double"} > 0
  for: 5m
  labels: { severity: error }
  → alert-manager → cordon

- alert: IBPortDown
  expr: task_ib_port_physical_state < 0
  for: 1m
  → alert-manager → cordon

- alert: IBLinkFlapRepeat
  expr: count_over_time((task_ib_port_physical_state == 0)[10m:1m]) > 8
  → alert-manager → cordon

# node.rules — already exists
- alert: NodeNotReady
  expr: pai_node_count{ready!="true"} > 0
  for: 1m
  → alert-manager → cordon
```

### Existing Rules (NFD cron patterns)

YAML specs that run on schedule, query Prometheus/logs, analyze with Python, POST to alert-manager:

```yaml
# nvidia_ecc_error.yaml, nvme_disk_error.yaml, large_job_failure.yaml
# Already working. Keep as-is.
```

### NEW Rules (switch monitor cron)

The switch SSH checks are **rules**, not agent tasks. They should fire alerts directly:

```python
# switch_monitor_cron.py — a RULE that runs every 2 minutes

# Rule 1: Switch unreachable (5 consecutive fails) → critical
if fail_count >= FAIL_THRESHOLD:
    severity = "critical"
    # Auto-submit alert for each affected node
    for node in affected_nodes:
        submit_alert(hostname=node, action="cordon", 
                     alertname="SwitchUnreachable",
                     triaged_label="triaged_hardware")

# Rule 2: Switch PSU failure → critical
if "psu" in reason:
    severity = "critical"
    for node in affected_nodes:
        submit_alert(hostname=node, action="cordon",
                     alertname="SwitchPSUFailure",
                     triaged_label="triaged_hardware")

# Rule 3: Switch port errors growing → warning (not cordon yet)
if "port_err_delta" in reason:
    severity = "warning"
    submit_alert(action="alert", alertname="SwitchPortErrors",
                 summary="SymbolErrors growing on switch port")
    # No cordon — this is a heads-up, not a confirmed failure
```

### NEW Rules (Feishu user reports)

```python
# feishu_cron.py — runs every 5 minutes

# Rule: Unprocessed "Node Unhealthy" report → create investigation task
reports = get_unprocessed_reports(category="Node Unhealthy")
for report in reports:
    if report.has_node_hostname:
        submit_alert(hostname=report.hostname, action="alert",
                     alertname="UserReportedIssue",
                     triaged_label="triaged_unknown")
    else:
        # No hostname — needs agent to investigate
        create_agent_task(prompt=report.description)
```

### NEW Rules (Prometheus — add to existing gpu.rules)

```yaml
# Add to gpu.rules
- alert: FabricManagerDown
  expr: process_up{process="nv-fabricmanager"} == 0
  for: 5m
  labels: { severity: error }
  → alert-manager → cordon

- alert: GPUThermalThrottle
  expr: nvidiasmi_temperature > 90
  for: 10m
  labels: { severity: warn }
  → alert-manager → alert (not cordon, just notify)
```

### What makes a good rule?

A detection should be a RULE when:
- ✅ Threshold is clear and objective (ECC > 0, PSU down, temp > 90)
- ✅ Action is deterministic (always cordon for ECC, always alert for temp)
- ✅ No investigation needed (the fact IS the diagnosis)
- ✅ Needs to run always, not on-demand

A detection should NOT be a rule when:
- ❌ Needs multi-source correlation ("is it the switch or the node?")
- ❌ Action depends on context ("should I cordon 20 nodes for one switch?")
- ❌ Needs SSH investigation ("why is validation undefined?")
- ❌ Needs judgment ("is this user report real or a misunderstanding?")

## Layer 2: MCP Server (Tools, On-Demand)

MCP servers are **pure data access + action execution**. No detection logic. No thresholds. No "if this then that". They serve whatever the agent asks for.

### Current MCP Tools (keep as-is)

| Server | Tool | What it does |
|---|---|---|
| switch-monitor | `check_switch_tool(hostname)` | SSH check one switch |
| switch-monitor | `check_all_switches_tool()` | SSH check all switches |
| switch-monitor | `query_switch_history_tool(hostname)` | Read check history from DB |
| switch-monitor | `list_switches_tool()` | List inventory |
| switch-monitor | `lookup_node_switch_tool(node)` | Find which switch a node connects to |
| node-operations | `get_node_alerts(hostname)` | Read alert_records |
| node-operations | `get_node_status(hostname)` | Read node_status |
| node-operations | `run_ssh_command(hostname, cmd)` | SSH into node |
| node-operations | `submit_triage_alert(hostname, ...)` | Submit alert to alert-manager |
| node-operations | `submit_validation(hostname)` | Trigger revalidation |
| feishu-bitable | `get_unprocessed_reports()` | Read Feishu user reports |
| agent-evidence | `query_evidence(hostname)` | Read past investigation evidence |

### NEW MCP Tools needed

| Server | Tool | Why |
|---|---|---|
| switch-monitor | `submit_switch_alert(switch_hostname, action, affected_nodes)` | Rule cron uses this (same as submit_triage_alert but for switches) |
| node-operations | `get_node_history(hostname, days)` | Agent needs to see what happened over time |
| node-operations | `get_failed_jobs(node, hours)` | Agent needs to see what jobs failed on a node |

### What makes a good MCP tool?

- ✅ Single responsibility (read one thing, do one action)
- ✅ No embedded logic (caller decides what to do with the data)
- ✅ Composable (agent chains multiple tool calls)
- ❌ Not a "detect and act" combo — that's a rule or an agent

## Layer 3: Agent (LLM, On-Demand)

The agent handles **everything rules can't** — investigation, correlation, judgment, user communication.

### When does the agent get involved?

The agent is woken up when:

1. **Rule fires but action is ambiguous** — e.g., `SwitchPortErrors` warning fires. Is it affecting jobs? Should we cordon? The rule can't decide → agent investigates.

2. **Rule fires but scope is unclear** — e.g., a switch goes critical. The rule auto-cordons directly-affected nodes from topology. But what about nodes without topology mapping? Agent investigates.

3. **No rule exists yet** — e.g., a new type of failure appears. Agent investigates, we codify the pattern as a rule afterward.

4. **User report needs investigation** — e.g., "my job keeps failing". Agent diagnoses: is it the node? the network? the config?

5. **Recurring problem** — e.g., a node keeps cycling through triage. Agent looks at history, finds pattern, escalates.

### Agent investigation flow

```
Alert/Task arrives
  │
  ├─ Rule already handled it? → Done (agent just reviews)
  │
  ├─ Need more data? → Call MCP tools
  │   ├─ check_switch_tool(hostname)
  │   ├─ get_node_alerts(hostname)
  │   ├─ get_node_history(hostname, days=7)
  │   ├─ run_ssh_command(hostname, "dmesg | tail -100")
  │   └─ lookup_node_switch_tool(hostname)
  │
  ├─ Need to correlate? → Compare across sources
  │   ├─ "Same switch has errors → these 3 nodes failing → switch is root cause"
  │   ├─ "User report matches IBPortDown alert 2h ago → known issue"
  │   └─ "Node keeps cycling triage → check if fix is effective"
  │
  ├─ Need to act? → Call MCP action tools
  │   ├─ submit_triage_alert(hostname, action="cordon", ...)
  │   ├─ submit_validation(hostname)  (after repair)
  │   └─ create_user_notification(message="Your job OOM'd, increase memory")
  │
  └─ Record findings → agent-evidence MCP
      └─ save_evidence(hostname, "investigation", findings)
```

## The Complete Picture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         RULES (always running)                      │
│                                                                     │
│  Prometheus rules          NFD cron           Switch cron           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │ ECC > 0      │    │ nvidia_ecc   │    │ port down    │          │
│  │ IBPortDown   │    │ nvme_disk    │    │ PSU failure  │          │
│  │ NodeNotReady │    │ large_job    │    │ switch reboot│          │
│  │ IBLinkFlap   │    │              │    │ err growing  │          │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘          │
│         │                   │                   │                   │
│         ▼                   ▼                   ▼                   │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │              ALERT-MANAGER API                           │       │
│  │                                                         │       │
│  │  Deterministic routing:                                 │       │
│  │  • action=cordon  → k8s cordon + status→triaged_*       │       │
│  │  • action=drain   → cordon + evict pods                 │       │
│  │  • action=reboot  → cordon + drain + reboot pod         │       │
│  │  • action=alert   → email admin + log                   │       │
│  │  • action=investigate → create agent task ← NEW         │       │
│  └────────────────────────────┬────────────────────────────┘       │
│                               │                                     │
│  Clear-cut cases ─────────────┘ (auto-handled, no agent)           │
│                                                                     │
│  Ambiguous cases ──────┐                                            │
│                        │                                            │
└────────────────────────┼────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    AGENT (LLM, on-demand)                            │
│                                                                     │
│  Woken up for:                                                      │
│  • action=investigate alerts                                        │
│  • User reports without clear node mapping                          │
│  • Nodes cycling through triage repeatedly                          │
│  • Validation undefined/timeout (3 sub-causes to disambiguate)      │
│  • Job failure diagnosis (user-facing)                              │
│                                                                     │
│  Uses MCP tools to:                                                 │
│  ┌──────────────────────────────────────────────┐                   │
│  │ READ: check_switch, get_node_alerts,          │                   │
│  │       get_node_history, run_ssh_command,      │                   │
│  │       query_evidence, get_failed_jobs          │                   │
│  │                                               │                   │
│  │ ACT: submit_triage_alert, submit_validation,  │                   │
│  │      create_user_notification                  │                   │
│  └──────────────────────────────────────────────┘                   │
│                                                                     │
│  Records: evidence DB (for future rules + feedback loop)            │
└─────────────────────────────────────────────────────────────────────┘
```

## How Detection Evolves

The system improves over time through a **rule learning loop**:

```
1. New failure type appears
   → Agent investigates (ad-hoc, LLM-powered)
   → Agent records findings in evidence DB

2. Pattern recognized (by feedback agent or human review)
   → "We've seen this 5 times, always the same root cause, same action"
   → Codify as a rule

3. Rule deployed
   → Next occurrence: rule auto-handles it
   → Agent not needed anymore for this pattern

4. Agent freed up for new unknown patterns
```

Example:
- **Month 1**: FM crash appears → agent investigates → finds `nv-fabricmanager` died → cordons node → records evidence
- **Month 3**: 5 FM crash cases in evidence DB → feedback agent spots pattern → we add Prometheus rule `FabricManagerDown`
- **Month 4**: FM crash → rule auto-cordons → agent never woken up

**The agent is the exploratory probe. Rules are the learned reflexes.**

## Rule Inventory: Current + Proposed

| # | Rule | Source | Mode | Action | Status |
|---|---|---|---|---|---|
| 1 | NvidiaSmiDoubleEccError | Prometheus | Reactive | cordon | ✅ Deployed |
| 2 | IBPortDown | Prometheus | Reactive | cordon | ✅ Deployed |
| 3 | IBLinkFlapRepeat | Prometheus | Reactive | cordon | ✅ Deployed |
| 4 | NodeNotReady | Prometheus | Reactive | cordon | ✅ Deployed |
| 5 | NodeFilesystemUsage | Prometheus | Reactive | alert | ✅ Deployed |
| 6 | NvidiaSmiLatencyTooLarge | Prometheus | Reactive | alert | ✅ Deployed |
| 7 | nvidia_ecc_error (NFD) | Prom+logs | Reactive | cordon | ✅ Deployed |
| 8 | nvme_disk_error (NFD) | Node logs | Reactive | cordon | ✅ Deployed |
| 9 | large_job_failure (NFD) | Job logs | Reactive | cordon | ✅ Deployed |
| 10 | SwitchUnreachable | SSH cron | Reactive | cordon | 🟡 Need alert submission |
| 11 | SwitchPSUFailure | SSH cron | Reactive | cordon | 🟡 Need alert submission |
| 12 | SwitchPortErrors | SSH cron | Proactive | alert | 🟡 Need alert submission |
| 13 | SwitchRebootDetected | SSH cron | Reactive | cordon | 🟡 Need alert submission |
| 14 | UserReportedIssue | Feishu cron | Diagnostic | investigate | 🟡 Need Feishu cron |
| 15 | FabricManagerDown | Prometheus | Reactive | cordon | ❌ Need new Prom rule |
| 16 | GPUThermalThrottle | Prometheus | Proactive | alert | ❌ Need new Prom rule |
| 17 | JobOOM | Job logs | Diagnostic | notify_user | ❌ Need NFD spec |
| 18 | JobImagePullError | Job metadata | Diagnostic | notify_user | ❌ Need NFD spec |
| 19 | ECCSingleBitTrend | Prometheus | Proactive | alert | ❌ Need new Prom rule |

## alert-manager Routing: Add `investigate` Action

Currently alert-manager routes to: `cordon`, `drain`, `reboot`, `alert`, `email`.

We need one new route:

```
action=investigate → create Chat UI task for detection agent
```

This is the bridge between rules and agent. When a rule fires but can't determine the right action, it uses `action=investigate` and the agent gets a task.

Implementation: either modify alert-handler to call Chat UI API, or have the detection cron directly create a Chat UI task when it sets `action=investigate` (simpler, no alert-manager code changes).

## Key Decisions

1. **Rules are the backbone** — most detections should be rules. Agent is for the long tail.
2. **Rules fire alerts to alert-manager** — single action gateway, no parallel paths.
3. **MCP tools are pure I/O** — no detection logic in tools. Agent decides what to check and what to do.
4. **Agent is for judgment** — investigation, correlation, ambiguous cases, user communication.
5. **Rule learning loop** — agent discovers patterns → feedback agent recognizes → we codify as rules → agent freed up.
6. **New rules are cheap** — Prometheus rule = 5 lines YAML. Switch rule = 1 Python if-statement. NFD spec = 1 YAML file + 1 Python pattern.
7. **Don't build a framework** — YAML-driven probe framework sounds nice but is over-engineering. Just write rules as code/config in the existing systems. When we have 20+ rules, then consider a framework.
