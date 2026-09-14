# Detection Agent

Proactive infrastructure monitoring agent. Detects switch failures, user-reported issues, and other infrastructure problems before they cascade.

## What It Does

- **Switch health monitoring** — SSH to switches, check uptime/PSU/fans/ports, detect reboots and growing port errors
- **Feishu user reports** — poll Feishu Bitable for user-reported node issues not yet in the alert pipeline
- **Correlation** — switch down → identify affected nodes; node issue → check if upstream switch is the root cause
- **Action** — submit alerts, cordon affected nodes, delegate to triage agent with context

## Architecture

The agent combines scheduled discovery with delegated investigation:

```text
patrol findings + collector logs + Feishu reports
  → scan-cluster
  → inspect-infra-issue | job-incident-response
  → evidence, verdict, bounded action, or rule improvement
```

Infrastructure findings follow `inspect-infra-issue`. Job-failure reports enter the staged job-incident workflow. Repeated detection gaps and rejected findings feed `automate-detection-pattern`.

## MCP Servers

| Server | Purpose |
|--------|---------|
| `patrol-cron` | Collectors, rules, findings, replay, and scheduled detection |
| `node-operations` (`node-ops`) | Node/job data, diagnosis, alert submission, and delegation |
| `switch-operations` (`switch-ops`) | Switch inventory, collector logs, and interactive commands |
| `feishu-bitable` | User-submitted node and job reports |
| `agent-evidence` | Persistent investigation evidence |
| `agent-feedback` | Outcome and learning history |

## Skills

| Skill | Description |
|-------|-------------|
| `scan-cluster` | Find unjudged findings, collector anomalies, and user reports |
| `inspect-infra-issue` | Investigate infrastructure findings and record evidence/verdicts |
| `automate-detection-pattern` | Create, refine, replay, and graduate detection rules |
| `job-check` | Read job state, events, and logs |
| `job-incident-response` | Orchestrate the training-job incident lifecycle |
| `job-log-triage` | Identify the first anomaly and candidate hypotheses |
| `system-evidence-diagnosis` | Correlate cross-source infrastructure evidence |
| `job-recovery` | Perform evidence-gated isolation and recovery |
| `training-reproduction` | Run bounded reproduction and validation experiments |
| `rca-closeout` | Record RCA and promote validated knowledge |

## Topology Files

Located at `/app/workspace/topology/` (mounted from `AGENT_DATA/topology/`):

- `switch_list.txt` — `hostname ip` lines (one per switch)
- `switch_topology.yaml` — switch→node port mapping

## Build & Deploy

```bash
# Build
cd incidara_agents/agents/detection-agent && make build

# Deploy (requires .env in AGENT_DATA)
sudo make run

# Check health
make health
```

## Identity

- `AGENT_NAME=detection`
- `MCP_SERVER_ROLE=diagnosis`
- Port: `8007`
