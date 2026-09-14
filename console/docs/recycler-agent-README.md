# Recycler Agent

Autonomous node lifecycle agent responsible for **re-allocating repaired nodes** back into the cluster and **tracking vendor RMA ticket status**.

## Purpose

After the repair agent completes hardware repair (RMA ticket submitted, node moved to `ua`), the recycler agent takes over:

1. **Ticket tracking** — Poll vendor RMA tickets for `ua` nodes. When repair is complete, close the ticket and move the node to `ready_ua`.
2. **Node reallocation** — Take `ready_ua` nodes through the full bring-up pipeline: reset, full config, sysinfo collection, onboard clone, and allocate.

The recycler closes the loop: `ua` → `ready_ua` → `allocated_ua`.

## Skills

### `node-reallocation`

Reallocate `ready_ua` nodes back into the cluster.

**Workflow:**
1. Fetch all `ready_ua` nodes via `get_nodes_by_status`
2. For each node (one at a time, ~20-30 min each):
   - Probe SSH — reset if unreachable
   - Run full config (7 stages + k8s)
   - Collect sysinfo (sbsysinfo, SKU, serial)
   - Clone onboard record
   - Move to `allocated_ua`
   - On failure → move to `triaged_unknown`
3. Write progress to working file after each node
4. Final report in `reports/reallocation_<date>.md`

**Key MCP tool:** `reallocate_node(hostname)` — long-running call (~20-30 min) that handles the entire pipeline.

**Context management:** Each node takes 20-30 min. If context gets heavy, write progress to working file and suggest starting a new session. Next session reads working file and skips completed nodes.

**Invocation:**
- Scheduled (cron: every 15 min)
- Manual: "reallocate nodes" or `node-reallocation`
- One-off: "reallocate h200-XXX"

### `ticket-check`

Poll vendor ticket API for all `ua` nodes. Complete RMA when repair is done, escalate anomalies.

**Workflow:**
1. Fetch `ua` nodes with ticket IDs via `get_ua_nodes_with_tickets`
2. For each node, check ticket status via `get_ticket_status`
3. Based on `repairStatus`:
   - **Terminal** (completed, repaired) → `complete_rma()` → moves to `ready_ua`
   - **In-progress** (repairing, pending) → skip, check next run
   - **Anomalous** (rejected, cancelled, on-hold) → move to `triaged_unknown` for re-diagnosis
4. Report in `reports/ticket_check_<date>.md`

**Invocation:**
- Scheduled (cron: every 30 min during business hours)
- Manual: "check tickets" or `ticket-check`

## MCP Tools

### `node-ops` MCP Server (role: ops)

#### Read-Only / Diagnosis

| Tool | Purpose |
|------|---------|
| `get_nodes_by_status` | List nodes in a given status with optional alert summaries |
| `get_node_detail` | SN, SKU, IPs, category, FaultCode |
| `get_node_history` | Full action history with optional alert enrichment |
| `get_node_alerts` | All alerts in the triage window |
| `get_node_recent_jobs` | Jobs on this node |
| `get_validation_job` | Find the validation (superbench) job |
| `get_job_details` | Job state, exit code, completion phrase |
| `get_job_events` | Job scheduling/failure events |
| `get_existing_reasons` | All reason values in the DB |
| `probe_ssh` | SSH reachability + GPU/IB/NVLink/dmesg diagnostics |
| `check_fabricmanager` | nvidia-fabricmanager status |
| `bmc_query` | BMC SEL/chassis/sensor/FRU via ipmitool |
| `bmc_screenshot` | BMC KVM console screenshot |
| `query_rma_cases` | Check existing RMA tickets |
| `get_ua_nodes_with_tickets` | List `ua` nodes with their RMA ticket IDs |
| `get_ticket_status` | Poll vendor ticket status |

#### Write / Lifecycle

| Tool | Purpose |
|------|---------|
| `move_node_status` | Persist a status transition |
| `reset_node` | Full reset sequence (teardown, create_debug_user, clear_node) |
| `run_full_config` | Run all 7 config stages + k8s setup |
| `run_config_stage` | Run a single config stage |
| `collect_sysinfo` | Collect sbsysinfo, SKU, serial |
| `clone_and_allocate` | Clone onboard record + move to `allocated_ua` |
| `reallocate_node` | End-to-end reallocation (~20-30 min) |
| `complete_rma` | Close RMA ticket + move to `ready_ua` |
| `submit_rma_ticket` | Submit an RMA ticket |
| `submit_validation` | Trigger revalidation |
| `submit_triage_alert` | Submit triage transition alert |
| `run_ssh_command` | Execute command on a node via SSH |
| `run_kubectl` | Execute kubectl command via SSH |

### `agent-evidence` MCP Server

| Tool | Purpose |
|------|---------|
| `save_evidence_tool` | Manual save for ad-hoc SSH output |
| `get_node_evidence_tool` | Get saved evidence for a node |
| `delete_node_evidence_tool` | Delete evidence for cleanup |

## Node Status Flow

```
                    Repair Agent
                    ───────────
                         │
                         ▼
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌────────────┐
│  ua     │───►│ ready_ua │───►│ allocated │    │ triaged_   │
│ (RMA    │    │ (ready   │    │  _ua      │    │  unknown   │
│  ticket │    │  to      │    │ (back in  │    │ (on        │
│  open)  │    │  recycle)│    │  cluster) │    │  failure)  │
└─────────┘    └──────────┘    └────────────┘    └────────────┘
     ▲                              ▲                   │
     │                              │                   ▼
     │         Recycler Agent       │            Triage Agent
     │         ─────────────        │            ────────────
     │                              │
     └── ticket-check ──────────────┘
         (close RMA ticket
          when repair done)

         node-reallocation ─────────┘
         (reset → config → allocate)
```

## Difference from Repair Agent

| | Repair Agent | Recycler Agent |
|---|---|---|
| **Input status** | `triaged_hardware`, `triaged_platform` | `ua`, `ready_ua` |
| **What it does** | Diagnose, collect evidence, submit RMA | Track RMA, reallocate nodes |
| **Runs full config?** | No (explicitly forbidden) | Yes (core responsibility) |
| **Ticket interaction** | Submit new RMA tickets | Poll & close existing RMA tickets |
| **Approval required?** | Yes (hardware repair) | No (automated) |
| **Typical duration** | 5-15 min per node | 20-30 min per node |

## Configuration

- **Port:** 8004
- **Image:** `claude-agent:recycler`
- **MCP role:** `ops` (read + write access to node operations)
- **Permissions:** All tools auto-allowed (no human approval needed)
