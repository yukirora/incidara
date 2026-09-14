# Detection Agent

Proactive infrastructure monitoring agent. Detects switch failures, user-reported issues, and other infrastructure problems before they cascade.

## What It Does

- **Switch health monitoring** — SSH to switches, check uptime/PSU/fans/ports, detect reboots and growing port errors
- **Feishu user reports** — poll Feishu Bitable for user-reported node issues not yet in the alert pipeline
- **Correlation** — switch down → identify affected nodes; node issue → check if upstream switch is the root cause
- **Action** — submit alerts, cordon affected nodes, delegate to triage agent with context

## Architecture

Probe-based framework. Each detection source is a pluggable probe (skill + MCP tools). Adding a new source = 3 steps: add MCP tools, add skill, add to AGENT_SKILLS.

Current probes:
- `switch-health` — switch uptime, PSU, fans, port status, error counter trends
- `feishu-reports` — user-reported issues from Feishu Bitable

## MCP Servers

| Server | Purpose | Tools |
|--------|---------|-------|
| `switch-monitor` | Switch health checks + topology | `check_switch_tool`, `check_all_switches_tool`, `get_switch_topology_tool`, `lookup_node_switch_tool`, `list_switches_tool`, `reset_switch_tool`, `get_switch_state_tool`, `reload_topology_tool` |
| `feishu-bitable` | Feishu Bitable user reports | `list_tables_tool`, `get_table_schema_tool`, `query_table_tool`, `get_record_tool`, `list_issue_categories_tool`, `get_unprocessed_reports_tool`, `get_node_unhealthy_reports_tool` |
| `node-ops` | Node data reads + alert submission (diagnosis role) | Read tools + `submit_triage_alert`, `move_node_status`, `delegate_to_agent` |
| `agent-evidence` | Evidence persistence | `save_evidence_tool`, `get_node_evidence_tool` |

## Skills

| Skill | Description |
|-------|-------------|
| `detect-infrastructure` | Main cycle: run all probes → dedup → correlate → act → report |
| `switch-health` | On-demand switch health checks |
| `feishu-reports` | On-demand Feishu user report processing |
| `triage-nodes` | Shared skill — for node investigation context |

## Topology Files

Located at `/app/workspace/topology/` (mounted from `AGENT_DATA/topology/`):

- `switch_list.txt` — `hostname ip` lines (one per switch)
- `switch_topology.yaml` — switch→node port mapping

## Build & Deploy

```bash
# Build
cd agents/detection-agent && make build

# Deploy (requires .env in AGENT_DATA)
sudo make run

# Check health
make health
```

## Identity

- `AGENT_NAME=detection`
- `MCP_SERVER_ROLE=diagnosis`
- Port: `8007`
