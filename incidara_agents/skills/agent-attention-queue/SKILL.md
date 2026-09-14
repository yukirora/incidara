---
name: agent-attention-queue
description: "Generate hourly Agent Attention Queue report: capability scan + behavior scan, impact-ordered. Auto-trigger for 'attention queue', 'agent attention', 'hourly report', 'attention scan'."
---

# agent-attention-queue

Run this skill when asked to produce the hourly Agent Attention Queue report.

## Purpose

Generate a report-only, impact-ordered list of agent-system problems that need human attention. The report is problem-centered, not agent-centered. It merges two scans:

- **Capability scan**: gateway event failures — tool errors, MCP unavailable, auth failures, operational questions.
- **Behavior scan**: Chat UI DB queries — repair loops, stuck schedules, error bursts, decision backlogs.

## Procedure

1. Read and execute `sub-skills/capability-scan/SKILL.md`. Return all filtered capability findings.
2. Read and execute `sub-skills/behavior-scan/SKILL.md`. Return all behavior findings.
3. Merge both lists into one impact-ordered report.
4. Return the merged report as the final answer.
5. If both scans return empty, return: "No new attention items."
6. Do not approve prompts, restart agents, disable schedules, submit alerts, delegate tasks, create tasks, or mutate node state.

## Merge Rules

- Keep the queue ordered by impact — broken capabilities that block work rank above persistent patterns.
- Preserve evidence references from both scans.
- Keep affected agents as metadata, not top-level sections.
- Do not expand the report with full transcript dumps.
- Do not turn suggested next tasks into real tasks in v1.

## Output Format

```
## Agent Attention Queue — {timestamp}

### 🔴 Critical (causing damage or stuck)
- **{agent}**: {issue} — {impact}

### 🟡 Warning (broken but not causing damage)
- **{agent}**: {issue} — {impact}

### 🟢 OK
- **{agent}**: running normally

### Decisions Needed
- {operational question requiring human input}
```