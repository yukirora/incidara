# Incidara architecture

Incidara retains the original operational boundaries: agents hold workflows and skills, MCP servers expose tools, and the Console coordinates human and agent activity.

## Components

| Agent | Primary skills | MCP dependencies |
|---|---|---|
| Detection | `scan-cluster`, `inspect-infra-issue`, `automate-detection-pattern`, `job-check` | `patrol-cron`, `node-operations`, `agent-evidence`, `agent-feedback`, `switch-operations`, `feishu-bitable` |
| Triage | `triage-nodes` | `node-operations`, `agent-evidence`, `agent-feedback` |
| Repair | `repair-nodes` | `node-operations`, `agent-evidence`, `agent-feedback` |
| Recycler | `ticket-check`, `node-reallocation` | `node-operations`, `agent-evidence`, `agent-feedback` |
| Feedback | `collect-rma-cases`, `analyze-rma-cases`, `skill-optimize` | `patrol-cron`, `node-operations`, `agent-evidence`, `agent-feedback` |
| Attention | `agent-attention-queue` | Console task and session records |

## Control flow

1. `patrol-cron`, switch monitoring, and user reports produce findings.
2. Detection correlates findings and creates investigation work.
3. Triage gathers evidence and classifies the problem.
4. Repair executes only the operations allowed by its MCP role and approval policy.
5. Recycler validates the resource before returning it to service.
6. Feedback reconciles outcomes with earlier decisions and proposes rule or skill improvements.
7. Attention collects work that is unsafe, ambiguous, or blocked.
8. The Console persists sessions, tasks, schedules, permissions, usage, and reports.

## Preserved integration boundary

This extraction is a rename and scope reduction, not a platform rewrite. Existing API contracts, environment variables, database schemas, skill behavior, and MCP server names remain unchanged. Deployments are responsible for providing the external services and credentials those integrations require.
