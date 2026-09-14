# behavior-scan

Query Chat UI DB for agent behavior problems: error bursts, workflow loops, stuck schedules, decision backlogs, and cross-agent anomaly patterns.

This finds WHY agents are failing at the workflow level — not individual tool errors (that's capability-scan).

## Available Data Sources

- `CHAT_UI_DB_URL` env var — Chat UI PostgreSQL
- Tables: `sessions`, `tasks`, `schedules`
- `sessions.agent_id` = agent name (detection, repair, triage-unknown, recycler, feedback)
- `tasks.status` = completed | error | running | waiting_input | cancelled
- `sessions.gateway_session_id` = `sess_xxxxxxxx` → maps to session files

## Procedure

### 1. Error Burst — Any Agent Suddenly Failing

```bash
psql "$CHAT_UI_DB_URL" -c "
SELECT s.agent_id, DATE(t.created_at) as day,
       COUNT(CASE WHEN t.status='error' THEN 1 END) as errors,
       COUNT(CASE WHEN t.status='completed' THEN 1 END) as completed
FROM tasks t JOIN sessions s ON t.session_id = s.id
WHERE t.created_at > NOW() - INTERVAL '7 days'
GROUP BY s.agent_id, DATE(t.created_at)
ORDER BY s.agent_id, day DESC"
```

**Alert if**: errors jump from ~0 to ≥5 in a day for any agent.

### 2. Workflow Loop — Same Target Returning to Same Workflow

Generalized from repair loop to cover all agents. A target (node, job, finding) repeatedly entering the same workflow suggests the workflow isn't resolving the root cause.

```bash
psql "$CHAT_UI_DB_URL" -c "
SELECT s.agent_id,
       SUBSTRING(s.title FROM '(lg-cmc-b7r[0-9]+-[a-z0-9]+-[a-z0-9]+-[0-9]+)') as hostname,
       COUNT(*) as sessions, MIN(t.created_at) as first, MAX(t.created_at) as last
FROM sessions s JOIN tasks t ON t.session_id = s.id
WHERE t.created_at > NOW() - INTERVAL '14 days'
  AND t.status = 'completed'
GROUP BY s.agent_id, hostname
HAVING COUNT(*) >= 3 AND SUBSTRING(s.title FROM '(lg-cmc-b7r[0-9]+-[a-z0-9]+-[a-z0-9]+-[0-9]+)') IS NOT NULL
ORDER BY sessions DESC"
```

**Alert if**: same hostname ≥3 sessions for any agent. For repair → hardware loop. For triage → classification loop. For recycler → return-to-service loop. For detection → same finding re-investigated.

### 3. Agent Burst — Too Many Sessions in Short Window

Many sessions hitting the same agent in a short window suggests a detection sweep found a cluster of related issues, an upstream dependency failure, or a rack-level problem — not individual incidents.

```bash
psql "$CHAT_UI_DB_URL" -c "
SELECT s.agent_id,
       COUNT(*) as burst,
       MIN(t.created_at) as first,
       MAX(t.created_at) as last
FROM sessions s JOIN tasks t ON t.session_id = s.id
WHERE t.created_at > NOW() - INTERVAL '7 days'
  AND t.status IN ('completed', 'waiting_input')
GROUP BY s.agent_id, DATE_TRUNC('hour', t.created_at)
HAVING COUNT(*) >= 3
ORDER BY burst DESC"

-- Then for each burst, get the session titles to understand the pattern:
psql "$CHAT_UI_DB_URL" -c "
SELECT s.title, t.status, t.created_at
FROM sessions s JOIN tasks t ON t.session_id = s.id
WHERE s.agent_id = 'repair'
  AND t.status IN ('completed', 'waiting_input')
  AND t.created_at BETWEEN '{burst_start}' AND '{burst_end}'
ORDER BY t.created_at"
```

### 4. Recurring Tool Failures — Same Task Failing Repeatedly

Tasks that error out with the same prompt pattern indicate a broken script, misconfiguration, or infrastructure issue — not a one-off failure. Error details are in session event logs, but the DB shows which agents/prompts fail repeatedly.

```bash
psql "$CHAT_UI_DB_URL" -c "
SELECT s.agent_id,
       LEFT(t.prompt, 120) as prompt_head,
       COUNT(*) as fails,
       MIN(t.created_at) as first, MAX(t.created_at) as last
FROM tasks t JOIN sessions s ON t.session_id = s.id
WHERE t.status = 'error' AND t.created_at > NOW() - INTERVAL '7 days'
GROUP BY s.agent_id, LEFT(t.prompt, 120)
HAVING COUNT(*) >= 3
ORDER BY fails DESC"
```

**Alert if**: same prompt fails 3+ times. Then check the session event logs for the error details:
- Session files: `/mnt/sessions/<agent>/sess_*/events.jsonl`
- Look for `tool_result` with `is_error: true` or `exit_code` ≠ 0
- Common patterns: `exit code 2` = script syntax/arg error, `401` = auth failure, `connection refused` = service down

Report the error pattern, affected agent, count, and root cause if found in logs.

### 5. Scheduled Task Stuck — Failing Repeatedly

```bash
psql "$CHAT_UI_DB_URL" -c "
SELECT s.agent_id, LEFT(t.prompt, 80) as prompt, COUNT(*) as fails,
       MIN(t.created_at) as first, MAX(t.created_at) as last
FROM tasks t JOIN sessions s ON t.session_id = s.id
WHERE t.status = 'error' AND t.created_at > NOW() - INTERVAL '24 hours'
GROUP BY s.agent_id, LEFT(t.prompt, 80)
HAVING COUNT(*) >= 3 ORDER BY fails DESC"
```

**Alert if**: same prompt fails 3+ times in 24h.

### 5. Decision Backlog — Tasks Waiting Too Long

```bash
psql "$CHAT_UI_DB_URL" -c "
SELECT t.id, LEFT(s.title, 60) as title, s.agent_id, t.created_at, NOW() - t.created_at as age
FROM tasks t JOIN sessions s ON t.session_id = s.id
WHERE t.status = 'waiting_input' AND t.created_at < NOW() - INTERVAL '4 hours'
ORDER BY t.created_at"
```

**Alert if**: tasks waiting >4h. Also pass the `gateway_session_id` values to capability-scan for question classification.

### 6. Session Stuck — Hanging Without Progress

Sessions running >30min with no recent output. This catches model hangs, tool timeouts, infinite loops, or sessions producing no output at all.

```bash
psql "$CHAT_UI_DB_URL" -c "
SELECT s.agent_id, s.gateway_session_id, LEFT(s.title, 60) as title,
       EXTRACT(epoch FROM NOW() - t.created_at)/60 as minutes_running
FROM tasks t JOIN sessions s ON t.session_id = s.id
WHERE t.status = 'running'
  AND t.created_at < NOW() - INTERVAL '30 minutes'
ORDER BY t.created_at"
```

For each stuck session, check the events.jsonl:
- If file doesn't exist → **session has 0 output** — agent failed to start
- If file exists, check last event timestamp → if last event >10min ago → **session is stuck**
- Cross-reference with capability-scan: if `TOOL_HUNG` signals appear for the same session, a specific tool is hanging (e.g., `probe_ssh`).

### 7. Assess Impact

For each alert found, judge:
- How many agents/nodes/sessions affected?
- Is production work blocked or just noisy?
- Cross-reference with capability-scan: do tool errors in the same time window explain the behavior?
- Is this new or has it been ongoing?

### 7. Return Findings

Return a list of behavior findings to the parent skill for merging. Each finding should include: agent, issue type, count, time window, and suggested action.