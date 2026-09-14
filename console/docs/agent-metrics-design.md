# Agent Metrics Dashboard — Implementation Design

Based on: `docs/survey-agent-aiops-evaluation.md` §5.3 Metric Layers

## Architecture

- Endpoint: `GET /api/agent-metrics?from=YYYY-MM-DD&to=YYYY-MM-DD`
- Data sources: `tasks` table (chat-ui DB) + `ltp_sdk.node_actions` (LTP SDK DB on .19, only reachable from .23)
- Frontend: 4 sub-views (Trust & Safety, Quality, Performance, Cost)
- Monthly trend: always last 5 months regardless of from/to filter

---

## View 1: 🛡 Trust & Safety

**Question:** "Can we let it run unsupervised?"

### Per-Agent Table

| Column | Source | Status |
|--------|--------|--------|
| L-Level | tasks (auto% + approval) | ✅ Done |
| Auto % | tasks (no human_decision + completed) | ✅ Done |
| Approval Rate | tasks.human_decision | ✅ Done |
| Override Rate | tasks.human_decision | ✅ Done |
| Escalation Rate | 1 - (autonomous_completed / total) | Phase 1 |

### Monthly Trend

| Row | Source | Status |
|-----|--------|--------|
| Nodes cordoned | ltp_sdk.node_actions | ✅ Done |
| Resolved by agent | tasks (repair completed) | ✅ Done |
| Handled by human | cordoned - resolved | ✅ Done |
| Auto-resolve rate | resolved/cordoned % | ✅ Done |
| Escalation rate | tasks needing human / total % | Phase 1 |

### Safety Gates

| Gate | Source | Status |
|------|--------|--------|
| False-positive RMAs | safety_events | ✅ Done |
| Blast-radius blocks | safety_events | ✅ Done |
| Circuit breaker trips | safety_events | ✅ Done |
| Repair recurrence | safety_events | ✅ Done |
| Unsafe actions executed | safety_events (must = 0) | Phase 1 |
| Blast-radius distribution (avg/max) | repair task prompt parsing | Phase 1 |

### What to Act On

| Signal | Condition |
|--------|-----------|
| Override % too high | worst agent override > 10% |
| L3 candidate | highest approval rate agent at L2 |

---

## View 2: 🎯 Quality

**Question:** "Is the agent getting the right answers?"

### Per-Agent Table

| Column | Source | Status |
|--------|--------|--------|
| Accuracy | tasks.ground_truth (correct/total) | Phase 3 (needs review layer) |
| Alert Precision | confirmed_hw / total_cordoned | Phase 1 |
| Rollback Rate | re-cordoned within 7d / total recovered | Phase 1 |

### Monthly Trend

| Row | Source | Status |
|-----|--------|--------|
| Incident count (hw) | ltp_sdk.node_actions (available-cordoned) | Phase 1 |
| Incident count (platform) | node_actions + triage classification | Phase 1 |
| Alert precision | confirmed_hardware / total_cordoned % | Phase 1 |
| Rollback/recurrence rate | re-cordoned within 7d % | Phase 1 |
| False-positive cordons | cordoned → recovered without repair | Phase 2 |
| MTTD (detect → diagnose) | avg task duration | ✅ Done |

### Computation Details

**Alert Precision:**
```sql
-- Confirmed hardware fault = cordoned node went through UA path
SELECT date_trunc('month', c.timestamp) AS month,
       COUNT(*) AS total_cordoned,
       COUNT(*) FILTER (WHERE EXISTS(
         SELECT 1 FROM ltp_sdk.node_actions ua
         WHERE ua.hostname = c.hostname
           AND ua.action IN ('deallocated_ua-ua', 'ua-ready_ua')
           AND ua.timestamp > c.timestamp
           AND ua.timestamp < c.timestamp + interval '7 days'
       )) AS confirmed_hardware
FROM ltp_sdk.node_actions c
WHERE c.action = 'available-cordoned'
GROUP BY 1
```

**Rollback/Recurrence Rate:**
```sql
-- Node re-cordoned within 7 days of recovery
SELECT date_trunc('month', r.timestamp) AS month,
       COUNT(*) AS total_recoveries,
       COUNT(*) FILTER (WHERE EXISTS(
         SELECT 1 FROM ltp_sdk.node_actions re
         WHERE re.hostname = r.hostname
           AND re.action = 'available-cordoned'
           AND re.timestamp > r.timestamp
           AND re.timestamp < r.timestamp + interval '7 days'
       )) AS recurrences
FROM ltp_sdk.node_actions r
WHERE r.action = 'validating-available'
GROUP BY 1
```

---

## View 3: ⏱ Performance

**Question:** "Is the agent fast and reliable?"

### Per-Agent Table

| Column | Source | Status |
|--------|--------|--------|
| Tasks (✓/✗) | tasks | ✅ Done |
| Success % | completed/total | ✅ Done |
| p50 / p90 / p95 / p99 | tasks (ended_at - created_at) | ✅ Done |
| Error % (total) | status=error / total | Phase 1 |
| Tool Fail % | output_preview parse (timeout, ECONNREFUSED) | Phase 1 |
| LLM Error % | output_preview parse (rate limit, context overflow) | Phase 1 |

### Monthly Trend

| Row | Source | Status |
|-----|--------|--------|
| Tasks completed | tasks | ✅ Done |
| MTTD (detect → diagnose) | avg task duration | ✅ Done |
| MTTR (detect → resolved) | ltp_sdk.node_actions (recovery - fail) | Phase 1 |
| Toil hours saved | autonomous_tasks × (baseline_MTTR - agent_MTTR) | Phase 1 |

### Computation Details

**MTTR (monthly):**
```sql
SELECT date_trunc('month', fail_time) AS month,
       ROUND(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY EXTRACT(EPOCH FROM (recovery_time - fail_time)) / 3600
       ))::numeric(6,1) AS mttr_p50_hours
FROM (
  SELECT a.hostname, a.timestamp AS fail_time,
    (SELECT MIN(timestamp) FROM ltp_sdk.node_actions r
     WHERE r.hostname = a.hostname
       AND r.action = 'validating-available'
       AND r.timestamp > a.timestamp) AS recovery_time
  FROM ltp_sdk.node_actions a
  WHERE a.action = 'available-cordoned'
    AND a.timestamp >= date_trunc('month', CURRENT_DATE) - interval '5 months'
) sub
WHERE recovery_time IS NOT NULL
GROUP BY 1
```

**Toil Hours Saved:**
```
baseline_human_mttr = AVG(recovery - fail) for pre-agent period (Jan-Mar 2025)
agent_mttr = AVG(recovery - fail) for current period
autonomous_tasks = COUNT(tasks WHERE human_decision IS NULL AND status = 'completed')

toil_saved_hours = autonomous_tasks × (baseline_human_mttr - agent_mttr)
```

**Error Rate Breakdown:**
```sql
SELECT s.agent_id,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE t.status = 'error') AS errors,
       COUNT(*) FILTER (WHERE t.status = 'error' AND (
         t.output_preview ILIKE '%timeout%' OR
         t.output_preview ILIKE '%ECONNREFUSED%' OR
         t.output_preview ILIKE '%tool error%' OR
         t.output_preview ILIKE '%kubectl%error%'
       )) AS tool_errors,
       COUNT(*) FILTER (WHERE t.status = 'error' AND (
         t.output_preview ILIKE '%rate limit%' OR
         t.output_preview ILIKE '%429%' OR
         t.output_preview ILIKE '%context%overflow%' OR
         t.output_preview ILIKE '%overloaded%'
       )) AS llm_errors
FROM tasks t JOIN sessions s ON t.session_id = s.id
WHERE t.parent_task_id IS NULL
GROUP BY s.agent_id
```

---

## View 4: 💰 Cost

**Question:** "Is it worth it?"

### Per-Agent Table

| Column | Source | Status |
|--------|--------|--------|
| Tasks | tasks | ✅ Done |
| $/Task | task_usage | ✅ Done |
| Total Cost | task_usage | ✅ Done |
| Input/Output Tokens | task_usage | ✅ Done |
| Trend (vs prev period) | task_usage period comparison | ✅ Done |

### Monthly Trend

| Row | Source | Status |
|-----|--------|--------|
| Agent ops cost | task_usage | ✅ Done |
| Cost per incident | total_cost / incident_count | Phase 1 |
| Toil cost saved | toil_hours × avg_SRE_rate (if defined) | Phase 2 |

---

## Implementation Phases

### Phase 1 — From Existing Data (implement next)

| # | Metric | View | Source |
|---|--------|------|--------|
| 1 | MTTR monthly (p50) | Performance | ltp_sdk.node_actions |
| 2 | Toil hours saved | Performance | baseline MTTR × autonomous tasks |
| 3 | Error % (total + tool + LLM) | Performance | tasks.output_preview parsing |
| 4 | Incident count (hw/platform) | Quality | ltp_sdk.node_actions + triage |
| 5 | Alert precision | Quality | node_actions (confirmed_hw / cordoned) |
| 6 | Rollback/recurrence rate | Quality | node_actions (re-cordon within 7d) |
| 7 | Escalation rate | Trust & Safety | tasks (human-needed / total) |
| 8 | Unsafe actions executed | Trust & Safety | safety_events (must = 0) |
| 9 | Blast-radius distribution | Trust & Safety | repair task prompt parsing |
| 10 | Cost per incident | Cost | task_usage / incident_count |

### Phase 2 — Needs Light Instrumentation

| Metric | View | What's needed |
|--------|------|---------------|
| Refusal rate (precise) | Trust & Safety | Parse output for refusal patterns ("I cannot", "not authorized", "insufficient") |

### Phase 3 — Needs Review Layer

| Metric | View | What's needed |
|--------|------|---------------|
| Accuracy / ground_truth | Quality | Ticket resolution feedback loop |
| Hallucination rate | Quality | LLM-as-judge audit pipeline |
| Escalation appropriateness | Trust & Safety | Human review sampling |
| Silent-failure rate | Quality | Claimed success but actually wrong |

---

## Open Questions

1. **Baseline period for toil displacement:** Jan-Mar 2025 (pre-agent)? Or earliest available data?
2. **Alert precision definition:** "confirmed hardware" (went through UA) vs "any follow-up action"?
3. **Rollback window:** 7 days? 14 days? What counts as "same fault" (same hostname + same error type)?
4. **Error classification:** How to distinguish tool failure from agent logic error from LLM API error? (output_preview parsing heuristics above)
5. **Escalation rate denominator:** All tasks? Or only tasks from agents that CAN run autonomously (exclude interactive-only agents)?
6. **Cost per incident:** Divide by node_actions incidents? Or by tasks?
