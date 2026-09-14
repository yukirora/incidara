# Incidara documentation

## Start here

- [System architecture and production evaluation](incidara-agent-sre.md) — the primary description of Incidara, its rollout, results, and validity limits.
- [Mission and design principles](mission.md) — why the system exists and how safety, evidence, and autonomy are prioritized.
- [Deployment](deployment.md) — configure, render, start, inspect, and incrementally deploy the stack.

## Agent and workflow design

- [Agent workflow protocol](agent-workflow-protocol.md) — lifecycle states, delegation, artifacts, approvals, and recovery.
- [Detection agent](detection-agent.md) — proactive probes, findings, investigation, and rule evolution.
- [Unified detection](unified-detection.md) — rule/agent boundary and shared detection model.
- [Detection feedback loop](detection-feedback-loop.md) — RMA reconciliation, attribution, replay, and rule/skill improvement.

## Operations

- [Backup](backup.md) — agent workspace, session, and transcript backup.
- [Publication checklist](publication.md) — gates that must pass before changing repository visibility.
- [Incidara Console](../console/README.md) — task-control server and web application.

## Evaluation artifacts

- [Aggregate metrics](evaluation/metrics.csv)
- [Figure generator](evaluation/generate_figures.py)
- [Generated figures](evaluation/figures/)

The design documents retain the implemented platform contracts and MCP server names. Deployment addresses, credentials, personal identities, and generated configuration are intentionally absent.
