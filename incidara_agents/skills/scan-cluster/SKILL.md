---
name: scan-cluster
description: "Scan for problems that no existing system has caught: unjudged patrol findings, collector anomalies, unprocessed user reports. Auto-trigger for scheduled scans and 'scan cluster' requests."
allowed-tools: Agent Bash Read Write Edit Grep Glob
---

# scan-cluster

Scan for problems that no existing system has caught. Focuses on the gaps — collector output with no rule coverage, unjudged patrol findings, unprocessed user reports.

## When to Use

- Scheduled cycle: cron creates Chat UI task every 30 min
- On-demand: "scan the cluster", "check for problems"

## What scan-cluster Does NOT Do

- Node status → that's triage agent + NFD + alert-manager
- Deep investigation → that's `inspect-infra-issue`
- Rule management → that's `automate-detection-pattern`

## Available Tools

### Patrol Database (patrol-cron MCP)
- `query_patrol_db(sql)` — run read-only SQL queries against the patrol DB. Use this for aggregation, counts, summaries. Much faster than `list_findings` for overview queries.
- `list_findings(...)` — individual findings with filters (active, resolved, verdict, rule_id, etc.)
- `list_rules` — see what rules and collectors are running
- `get_collector_health()` — collector status

### Collector Raw Output (log files)
- `grep -i "FAIL\|error\|timeout\|refused\|denied" /tmp/patrol_cron/logs/<collector>*.log | grep -v "NFD\|stub\|data_sources not available"` — find failures in collector logs that no rule covers

### User Reports (feishu-bitable MCP)
- `get_unprocessed_reports_tool()` — unprocessed Feishu "Node Unhealthy" reports

### Investigation Records (agent-evidence MCP)
- `get_node_evidence_tool(hostname)` — past investigation records (avoid re-delegating)

## Workflow

### Step 1: Check Unjudged Findings

First, get a summary of unjudged findings per rule using `query_patrol_db`:

```sql
SELECT rule_id,
       count(*) FILTER (WHERE rule_code_hash IS NOT NULL AND rule_code_hash != md5((SELECT analyze_code FROM patrol_rules WHERE rule_id = patrol_findings.rule_id))) as old_code,
       count(*) FILTER (WHERE rule_code_hash = md5((SELECT analyze_code FROM patrol_rules WHERE rule_id = patrol_findings.rule_id))) as current_code
FROM patrol_findings
WHERE NOT resolved AND verdict IS NULL
GROUP BY rule_id ORDER BY current_code DESC;
```

- `current_code` > 0: these findings need verdicts. Judge them.
- `old_code` > 0: findings from a previous rule version. Can be ignored.
- Findings with no hash are pre-provenance and also ignored.

Then for each rule with `current_code` > 0, list the details:

```
list_findings(rule_id="{rule_id}", current_code_only=True, resolved=False, verdict="", limit=500)
```

Delegate each finding to `inspect-infra-issue`. Active findings (target still failing) are higher priority but recovered findings also need verdicts — without them, rule accuracy cannot be measured and the feedback loop is starved.

**Dedup before delegating**: Check BOTH:
1. `get_node_evidence_tool(hostname)` — if already investigated recently (within 24h), skip.
2. Only delegate for findings where the patrol system has NOT already created a task. Findings at `create_task` stage or higher already have auto-created investigation tasks — scan-cluster should not create a second one. Only delegate for `log_only` findings or manual findings that have no auto-task.

### Step 2: Check Collector Logs for Anomalies

For each collector from `list_collectors`:
- `grep -i "FAIL\|error\|timeout\|refused\|denied" /tmp/patrol_cron/logs/<collector>*.log | grep -v "NFD\|stub\|data_sources not available"` — explicit failures
- If failures found and no corresponding finding exists → these are gaps. Create findings via `create_finding()` so `inspect-infra-issue` can investigate.
  Include the raw collector output in evidence: `create_finding(rule_id="manual", target_id=..., target_type=..., severity="info", action="log_only", evidence={"raw_output": <paste raw data>, "source": "scan-cluster"})`.
- Also check the OK samples — the collector marks a target OK when thresholds aren't exceeded, but the raw output may still show concerning patterns:
  - `grep -i "CRC\|FCS\|SymbolError\|discard\|overflow\|temperature.*[5-9][0-9]" /tmp/patrol_cron/logs/<collector>*.log | grep -v "NFD\|stub\|data_sources not available"` — abnormal values in OK output
  - A switch with CRC errors below threshold is still worth watching. If you see CRC/FCS/SymbolError in OK samples, create a finding with `severity="info"`.
  - You are NOT a rule engine — use judgment. Don't flag every single counter. Flag patterns that look like they're growing or concentrated on one target.

### Step 3: Check Unprocessed User Reports

Check both report categories:

**Node Unhealthy reports:**
- `get_unprocessed_reports_tool()` — any Feishu "Node Unhealthy" reports?
- If found → delegate to `inspect-infra-issue` for investigation (delegate to `detection`, yourself).

**Job Failure reports:**
- `get_unprocessed_reports_tool(category="Job Failure")` — any user-submitted job failure reports?
- If found → delegate to yourself (`detection`) with `/job-incident-response` skill:

```
delegate_to_agent("detection",
  title="Job triage: {job_name} — {brief issue from report}",
  prompt="User report: {original report text}\nJob: {job_name}\nUser: {username}\n\nUse /job-incident-response to investigate.",
  completion_mode="manual"
)
```

Do NOT investigate the job yourself — `job-incident-response` on the detection agent handles job failure investigation.

### Step 4: Delegate

For each anomaly that needs investigation:

```
delegate_to_agent("detection",
  title="{target_type} {target_id}: {symptom}",
  prompt="Source: scan-cluster\nTarget: {target_id} ({target_type})\nSymptom: {what was detected}\n\nRaw evidence:\n{Paste the actual raw output, dmesg lines, error messages. Do NOT summarize or paraphrase — the investigator needs the original data.}\n\nUse /inspect-infra-issue for full investigation.",
  completion_mode="manual"
)
```

**Include raw evidence in the prompt.** The investigator needs the original error messages, timestamps, log lines — not your summary of them. A summary like "Xid 31 on 6 GPUs" is useless compared to actual dmesg lines showing which PCI addresses, when, and what fault type. Paste what you found.

**Always delegate to `detection` (yourself).** Never delegate to `repair` or any other agent. The investigation skill (`inspect-infra-issue`) runs on the detection agent. Even if the problem seems already diagnosed from a previous session, the investigation must run again to confirm the current state before any repair action. Skipping investigation and delegating directly to repair is always wrong — repair without fresh investigation is dangerous.

**Do NOT add task lists.** "Investigation needed: 1. Check X 2. Verify Y" narrows the scope. The investigator's skill (`inspect-infra-issue`) already knows what to do. Raw evidence + "Use /inspect-infra-issue" is the complete prompt. Nothing more.

### Step 5: Report

Summary: what was scanned, what was found, what was delegated.

## Important Rules

- **Focus on gaps** — only look for things NOT already caught by rules/NFD/triage
- **Read before delegating** — check `list_findings` and `get_node_evidence_tool` to avoid re-delegating
- **Don't investigate** — scan-cluster only finds and delegates. `inspect-infra-issue` does the deep work.
- **Don't check node status** — that's the triage agent's job
- **Don't delegate to repair** — ALWAYS delegate to `detection` (yourself). Even if previous investigation already diagnosed the problem, `inspect-infra-issue` must confirm current state before any repair.
- **Don't interpret timestamps** — you don't know the current time relative to dmesg boot seconds or epoch timestamps. Raw dmesg seconds like `[2278131]` are NOT human-readable. Always pass raw evidence and let `inspect-infra-issue` use `dmesg -T` to determine if errors are current or historical. A finding that says "26 days of Xid errors, still active" is wrong if those errors happened 13 days ago and the node has been clean since. You CANNOT determine recency from raw collector output — that's investigation work.