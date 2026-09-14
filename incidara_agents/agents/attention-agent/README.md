# Attention Agent

Hourly report-only monitor that publishes the Agent Attention Queue in Chat UI.

The queue is organized by problem or follow-up task, not by agent. V1 does not mutate production state.

## Scheduled Prompt

Run agent-attention-queue and return the report.

## Data Dependencies

| Host Path | Container Path | Purpose |
|---|---|---|
| `/data/agents/triage-unknown/workspace/sessions` | `/mnt/sessions/triage` | Triage gateway events |
| `/data/agents/repair/workspace/sessions` | `/mnt/sessions/repair` | Repair gateway events |
| `/data/agents/recycler/workspace/sessions` | `/mnt/sessions/recycler` | Recycler gateway events |
| `/data/agents/detection/workspace/sessions` | `/mnt/sessions/detection` | Detection gateway events |
| `/data/agents/feedback/workspace/sessions` | `/mnt/sessions/feedback` | Feedback gateway events |

## Safety

This agent is report-only. It must not approve prompts, restart containers, submit alerts, delegate tasks, create tasks, or mutate node state.
