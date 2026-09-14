import { Router, Request, Response } from "express";
import type pg from "pg";

let queryLtp: ((sql: string, params?: unknown[]) => Promise<any[]>) | null = null;

const NO_CHILD = "t.parent_task_id IS NULL";

export function makeAgentMetricsRouter(pool: pg.Pool): Router {
  const router = Router();

  // Try to load LTP SDK DB pool (may not be available on HK)
  import("../db/ltp-pool.js").then((mod) => { queryLtp = mod.queryLtp; }).catch(() => {});

  router.get("/", async (_req: Request, res: Response) => {
    try {
      // Support from/to date range or legacy days param
      const today = new Date().toISOString().slice(0, 10);
      let from: string, to: string;
      if (_req.query.from && _req.query.to) {
        from = _req.query.from as string;
        to = _req.query.to as string;
      } else {
        const days = Math.min(parseInt(_req.query.days as string ?? "7", 10), 365);
        const d = new Date(); d.setDate(d.getDate() - days);
        from = d.toISOString().slice(0, 10);
        to = today;
      }
      const category = (_req.query.category as string) || "all";
      const days = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 86400000) || 7;

      const [
        perAgent, approvals, accuracy, cost,
        fleetTasks, fleetCost, overallApproval,
        monthly, safetyResult,
      ] = await Promise.all([
        // Per-agent: tasks + duration + L-level
        pool.query(
          `SELECT s.agent_id,
             COUNT(t.id)::int as total,
             COUNT(t.id) FILTER (WHERE t.status = 'completed')::int as completed,
             COUNT(t.id) FILTER (WHERE t.status IN ('error','cancelled'))::int as failed,
             ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (t.ended_at - t.created_at))))::int as p50_s,
             ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (t.ended_at - t.created_at))))::int as p90_s,
             ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (t.ended_at - t.created_at))))::int as p95_s,
             ROUND(percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (t.ended_at - t.created_at))))::int as p99_s,
             COUNT(t.id) FILTER (WHERE t.completion_mode = 'auto' AND t.human_decision IS NULL AND t.status = 'completed')::int as l3,
             COUNT(t.id) FILTER (WHERE t.human_decision IS NOT NULL)::int as l2,
             COUNT(t.id) FILTER (WHERE t.human_decision = 'overridden')::int as overridden_count
           FROM (SELECT DISTINCT agent_id FROM sessions) s
           LEFT JOIN tasks t ON t.session_id IN (SELECT id FROM sessions WHERE agent_id = s.agent_id)
             AND ${NO_CHILD} AND t.created_at >= $1::date AND t.created_at < ($2::date + 1)
           GROUP BY s.agent_id ORDER BY total DESC`,
          [from, to]
        ),
        // Per-agent: approval breakdown
        pool.query(
          `SELECT s.agent_id, t.human_decision, COUNT(*)::int as count
           FROM tasks t JOIN sessions s ON t.session_id = s.id
           WHERE ${NO_CHILD} AND t.human_decision IS NOT NULL AND t.created_at >= $1::date AND t.created_at < ($2::date + 1)
           GROUP BY s.agent_id, t.human_decision`,
          [from, to]
        ),
        // Per-agent: accuracy from ground truth
        pool.query(
          `SELECT s.agent_id, t.ground_truth, COUNT(*)::int as count
           FROM tasks t JOIN sessions s ON t.session_id = s.id
           WHERE ${NO_CHILD} AND t.ground_truth IS NOT NULL AND t.created_at >= $1::date AND t.created_at < ($2::date + 1)
           GROUP BY s.agent_id, t.ground_truth`,
          [from, to]
        ),
        // Per-agent: cost + trend (compare to same-length previous period)
        pool.query(
          `SELECT s.agent_id,
             SUM(u.total_cost_usd) FILTER (WHERE t.created_at >= $1::date AND t.created_at < ($2::date + 1))::numeric(10,2) as current_cost,
             AVG(u.total_cost_usd) FILTER (WHERE t.created_at >= $1::date AND t.created_at < ($2::date + 1))::numeric(10,4) as avg_cost,
             SUM(u.total_input_tokens) FILTER (WHERE t.created_at >= $1::date AND t.created_at < ($2::date + 1))::bigint as tokens_in,
             SUM(u.total_output_tokens) FILTER (WHERE t.created_at >= $1::date AND t.created_at < ($2::date + 1))::bigint as tokens_out,
             SUM(u.total_cost_usd) FILTER (WHERE t.created_at >= ($1::date - ($2::date - $1::date)) AND t.created_at < $1::date)::numeric(10,2) as prev_cost
           FROM task_usage u JOIN tasks t ON u.task_id = t.id
           JOIN sessions s ON t.session_id = s.id
           WHERE ${NO_CHILD} AND t.created_at >= ($1::date - ($2::date - $1::date))
           GROUP BY s.agent_id ORDER BY current_cost DESC NULLS LAST`,
          [from, to]
        ),
        // Fleet-wide: task counts
        pool.query(
          `SELECT COUNT(*)::int as total,
             COUNT(*) FILTER (WHERE status = 'completed')::int as completed,
             COUNT(*) FILTER (WHERE status IN ('error','cancelled'))::int as failed,
             ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at - created_at))))::int as p50_s,
             ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at - created_at))))::int as p90_s,
             ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at - created_at))))::int as p95_s,
             ROUND(percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at - created_at))))::int as p99_s
           FROM tasks WHERE parent_task_id IS NULL AND created_at >= $1::date AND created_at < ($2::date + 1)`,
          [from, to]
        ),
        // Fleet-wide: cost
        pool.query(
          `SELECT SUM(u.total_cost_usd)::numeric(10,2) as total_cost,
             AVG(u.total_cost_usd)::numeric(10,4) as avg_cost
           FROM task_usage u JOIN tasks t ON u.task_id = t.id
           WHERE t.parent_task_id IS NULL AND t.created_at >= $1::date AND t.created_at < ($2::date + 1)`,
          [from, to]
        ),
        // Fleet-wide: approval
        pool.query(
          `SELECT human_decision, COUNT(*)::int as count
           FROM tasks WHERE parent_task_id IS NULL AND human_decision IS NOT NULL AND created_at >= $1::date AND created_at < ($2::date + 1)
           GROUP BY human_decision`,
          [from, to]
        ),
        // Monthly trend
        pool.query(
          `SELECT
             to_char(date_trunc('month', created_at), 'Mon') as month,
             date_trunc('month', created_at) as month_start,
             COUNT(*)::int as total,
             COUNT(*) FILTER (WHERE status = 'completed')::int as completed,
             COUNT(*) FILTER (WHERE status IN ('error','cancelled'))::int as failed,
             ROUND(AVG(EXTRACT(EPOCH FROM (ended_at - created_at))))::int as avg_s
           FROM tasks WHERE parent_task_id IS NULL
             AND created_at >= date_trunc('month', CURRENT_DATE) - interval '5 months'
           GROUP BY date_trunc('month', created_at)
           ORDER BY month_start`,
          []
        ),
        // Safety events (from safety_events table if it exists)
        pool.query(
          `SELECT event_type, COUNT(*)::int as count
           FROM safety_events
           WHERE created_at >= $1::date AND created_at < ($2::date + 1)
           GROUP BY event_type`,
          [from, to]
        ).catch(() => ({ rows: [] })),
      ]);

      // ── Monthly operations + Phase 1 metrics ──────────────────────────
      // Check LTP SDK connectivity once (with 2s timeout), skip all LTP queries if unreachable
      let ltpOk = false;
      if (queryLtp) {
        try {
          await Promise.race([queryLtp("SELECT 1"), new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 2000))]);
          ltpOk = true;
        } catch { /* unreachable */ }
      }

      // Run all independent queries in parallel
      const [monthlyCost, errorBreakdown, blastRadius, ...ltpResults] = await Promise.all([
        // Local DB queries (always fast)
        pool.query(
          `SELECT to_char(date_trunc('month', t.created_at), 'Mon') as month, SUM(u.total_cost_usd)::numeric(10,2) as cost
           FROM task_usage u JOIN tasks t ON u.task_id = t.id
           WHERE t.parent_task_id IS NULL AND t.created_at >= date_trunc('month', CURRENT_DATE) - interval '5 months'
           GROUP BY date_trunc('month', t.created_at) ORDER BY date_trunc('month', t.created_at)`
        ),
        pool.query(
          `SELECT s.agent_id,
             COUNT(*) FILTER (WHERE t.status = 'error')::int as total_errors,
             SUM(t.tool_error_count)::int as tool_errors,
             SUM(t.llm_error_count)::int as llm_errors
           FROM tasks t JOIN sessions s ON t.session_id = s.id
           WHERE ${NO_CHILD} AND t.created_at >= $1::date AND t.created_at < ($2::date + 1)
           GROUP BY s.agent_id`,
          [from, to]
        ),
        pool.query(
          `SELECT
             ROUND(AVG(array_length(regexp_split_to_array(t.prompt, 'lg-cmc-'), 1) - 1))::int as avg_nodes,
             MAX(array_length(regexp_split_to_array(t.prompt, 'lg-cmc-'), 1) - 1)::int as max_nodes
           FROM tasks t JOIN sessions s ON t.session_id = s.id
           WHERE s.agent_id = 'repair' AND ${NO_CHILD}
             AND t.created_at >= $1::date AND t.created_at < ($2::date + 1) AND t.prompt LIKE '%lg-cmc-%'`,
          [from, to]
        ),
        // LTP SDK queries (only if reachable, all in parallel)
        ...(ltpOk && queryLtp ? [
          // Nodes cordoned per month — only completed round-trips (→available)
          // Returns hostname + time window for each incident
          queryLtp(`
            WITH cat_hosts AS (
              SELECT DISTINCT hostname FROM ltp_sdk.physical_node_onboard_records
              ${category !== "all" ? `WHERE category = '${category.replace(/[^a-z0-9_]/gi, "")}'` : ""}
            ),
            cordoned_resolved AS (
              SELECT c.hostname, c.timestamp as cordon_time,
                (SELECT MIN(a.timestamp) FROM ltp_sdk.node_actions a
                 WHERE a.hostname = c.hostname AND a.action = 'validating-available'
                   AND a.timestamp > c.timestamp AND a.timestamp < c.timestamp + interval '60 days'
                ) as recovery_time,
                date_trunc('month', c.timestamp) as month
              FROM ltp_sdk.node_actions c
              WHERE c.action = 'available-cordoned'
                AND c.hostname IN (SELECT hostname FROM cat_hosts)
                AND c.timestamp >= date_trunc('month', CURRENT_DATE) - interval '5 months'
            )
            SELECT to_char(month, 'Mon') as month,
              json_agg(json_build_object('hostname', hostname, 'cordon_time', cordon_time, 'recovery_time', recovery_time)) as incidents
            FROM cordoned_resolved
            WHERE recovery_time IS NOT NULL
            GROUP BY month ORDER BY month`).catch(() => []),
          queryLtp(`
            WITH cat_hosts AS (
              SELECT DISTINCT hostname FROM ltp_sdk.physical_node_onboard_records
              ${category !== "all" ? `WHERE category = '${category.replace(/[^a-z0-9_]/gi, "")}'` : ""}
            )
            SELECT to_char(date_trunc('month', a.timestamp), 'Mon') as month,
              ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (r.recovery_time - a.timestamp)) / 3600)::numeric, 1) as mttr_p50_hours,
              ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (r.recovery_time - a.timestamp)) / 3600)::numeric, 1) as mttr_p95_hours,
              COUNT(*)::int as incidents
            FROM ltp_sdk.node_actions a
            CROSS JOIN LATERAL (SELECT MIN(timestamp) as recovery_time FROM ltp_sdk.node_actions r WHERE r.hostname = a.hostname AND r.action = 'validating-available' AND r.timestamp > a.timestamp) r
            WHERE a.action = 'available-cordoned'
              AND a.hostname IN (SELECT hostname FROM cat_hosts)
              AND a.timestamp >= date_trunc('month', CURRENT_DATE) - interval '5 months' AND r.recovery_time IS NOT NULL
            GROUP BY date_trunc('month', a.timestamp) ORDER BY date_trunc('month', a.timestamp)`).catch(() => []),
          queryLtp(`
            WITH cat_hosts AS (
              SELECT DISTINCT hostname FROM ltp_sdk.physical_node_onboard_records
              ${category !== "all" ? `WHERE category = '${category.replace(/[^a-z0-9_]/gi, "")}'` : ""}
            )
            SELECT to_char(date_trunc('month', c.timestamp), 'Mon') as month,
              COUNT(*)::int as total_cordoned,
              COUNT(*) FILTER (WHERE EXISTS(SELECT 1 FROM ltp_sdk.node_actions ua WHERE ua.hostname = c.hostname AND ua.action IN ('deallocated_ua-ua', 'ua-ready_ua') AND ua.timestamp > c.timestamp AND ua.timestamp < c.timestamp + interval '7 days'))::int as confirmed_hw
            FROM ltp_sdk.node_actions c WHERE c.action = 'available-cordoned'
              AND c.hostname IN (SELECT hostname FROM cat_hosts)
              AND c.timestamp >= date_trunc('month', CURRENT_DATE) - interval '5 months'
            GROUP BY date_trunc('month', c.timestamp) ORDER BY date_trunc('month', c.timestamp)`).catch(() => []),
          queryLtp(`
            WITH cat_hosts AS (
              SELECT DISTINCT hostname FROM ltp_sdk.physical_node_onboard_records
              ${category !== "all" ? `WHERE category = '${category.replace(/[^a-z0-9_]/gi, "")}'` : ""}
            )
            SELECT to_char(date_trunc('month', r.timestamp), 'Mon') as month,
              COUNT(*)::int as total_recoveries,
              COUNT(*) FILTER (WHERE EXISTS(SELECT 1 FROM ltp_sdk.node_actions re WHERE re.hostname = r.hostname AND re.action = 'available-cordoned' AND re.timestamp > r.timestamp AND re.timestamp < r.timestamp + interval '7 days'))::int as recurrences
            FROM ltp_sdk.node_actions r WHERE r.action = 'validating-available'
              AND r.hostname IN (SELECT hostname FROM cat_hosts)
              AND r.timestamp >= date_trunc('month', CURRENT_DATE) - interval '5 months'
            GROUP BY date_trunc('month', r.timestamp) ORDER BY date_trunc('month', r.timestamp)`).catch(() => []),
          queryLtp(`SELECT ROUND(AVG(EXTRACT(EPOCH FROM (r.recovery_time - a.timestamp)) / 3600)::numeric, 2) as avg_hours
            FROM ltp_sdk.node_actions a
            CROSS JOIN LATERAL (SELECT MIN(timestamp) as recovery_time FROM ltp_sdk.node_actions r2 WHERE r2.hostname = a.hostname AND r2.action = 'validating-available' AND r2.timestamp > a.timestamp) r
            WHERE a.action = 'available-cordoned' AND a.timestamp >= '2025-01-01' AND a.timestamp < '2025-04-01' AND r.recovery_time IS NOT NULL`).catch(() => []),
        ] : [Promise.resolve([]), Promise.resolve([]), Promise.resolve([]), Promise.resolve([]), Promise.resolve([])]),
      ]);

      // Unpack LTP results
      const monthlyCordon = (ltpResults[0] ?? []) as { month: string; incidents: { hostname: string; cordon_time: string; recovery_time: string }[] }[];
      const monthlyMttr = (ltpResults[1] ?? []) as { month: string; mttr_p50_hours: number; mttr_p95_hours: number; incidents: number }[];
      const monthlyPrecision = (ltpResults[2] ?? []) as { month: string; total_cordoned: number; confirmed_hw: number }[];
      const monthlyRollback = (ltpResults[3] ?? []) as { month: string; total_recoveries: number; recurrences: number }[];
      const baselineRows = (ltpResults[4] ?? []) as { avg_hours: number }[];

      // Compute toil saved
      const baselineMttr = baselineRows[0]?.avg_hours ?? 4;
      const toilSaved: { month: string; hours_saved: number }[] = [];
      for (const m of monthlyMttr) {
        const saved = Math.max(0, (baselineMttr - m.mttr_p50_hours) * m.incidents);
        toilSaved.push({ month: m.month, hours_saved: Math.round(saved) });
      }

      // Build lookup maps
      const cordonMap: Record<string, number> = {};
      for (const r of monthlyCordon) cordonMap[r.month] = (r.incidents || []).length;

      // Resolved by agent: match each cordon incident (hostname + time range) to agent tasks
      const resolvedMap: Record<string, number> = {};
      if (monthlyCordon.length > 0) {
        // Get all agent tasks with hostnames and their timestamps
        const handledResult = await pool.query(
          `SELECT t.prompt, t.created_at
           FROM tasks t JOIN sessions s ON t.session_id = s.id
           WHERE s.agent_id IN ('triage', 'triage-unknown', 'repair', 'recycler')
             AND t.status = 'completed'
             AND t.prompt ~ 'lg-cmc-'
             AND t.created_at >= date_trunc('month', CURRENT_DATE) - interval '5 months'`
        );
        // Build index: hostname → list of task timestamps
        const hostTaskTimes: Record<string, Date[]> = {};
        for (const row of handledResult.rows) {
          const ts = new Date(row.created_at);
          for (const m of (row.prompt as string).matchAll(/lg-cmc-[a-z0-9-]+/g)) {
            if (!hostTaskTimes[m[0]]) hostTaskTimes[m[0]] = [];
            hostTaskTimes[m[0]].push(ts);
          }
        }

        for (const r of monthlyCordon) {
          let matched = 0;
          for (const inc of (r.incidents || [])) {
            const cordonTime = new Date(inc.cordon_time);
            const recoveryTime = new Date(inc.recovery_time);
            const tasks = hostTaskTimes[inc.hostname] || [];
            if (tasks.some(t => t >= cordonTime && t <= recoveryTime)) {
              matched++;
            }
          }
          resolvedMap[r.month] = matched;
        }
      }
      const costMap: Record<string, number> = {};
      for (const r of monthlyCost.rows) costMap[r.month] = parseFloat(r.cost);
      const mttrMap: Record<string, { p50: number; p95: number; incidents: number }> = {};
      for (const r of monthlyMttr) mttrMap[r.month] = { p50: r.mttr_p50_hours, p95: r.mttr_p95_hours, incidents: r.incidents };
      const precisionMap: Record<string, { total: number; confirmed: number }> = {};
      for (const r of monthlyPrecision) precisionMap[r.month] = { total: r.total_cordoned, confirmed: r.confirmed_hw };
      const rollbackMap: Record<string, { total: number; recurrences: number }> = {};
      for (const r of monthlyRollback) rollbackMap[r.month] = { total: r.total_recoveries, recurrences: r.recurrences };
      const toilMap: Record<string, number> = {};
      for (const r of toilSaved) toilMap[r.month] = r.hours_saved;

      // Build per-agent map
      const agents: Record<string, any> = {};
      for (const row of perAgent.rows) {
        const total = row.total || 0;
        const l3Count = row.l3 || 0;
        const l2Count = row.l2 || 0;
        const l1Count = Math.max(0, total - l3Count - l2Count);

        agents[row.agent_id] = {
          tasks: { total, completed: row.completed, failed: row.failed },
          duration_s: { p50: row.p50_s, p90: row.p90_s, p95: row.p95_s, p99: row.p99_s },
          l_level: { l1: l1Count, l2: l2Count, l3: l3Count, dominant: "—", auto_pct: total > 0 ? Math.round(l3Count / total * 100) : 0, overridden: row.overridden_count || 0, direction: null as string | null },
          approval: { approved: 0, overridden: 0, declined: 0, rate: null as number | null },
          accuracy: {} as Record<string, number>,
          cost: { total: 0, avg: 0, tokens_in: 0, tokens_out: 0, trend_pct: null as number | null },
        };
      }
      for (const row of approvals.rows) {
        const a = agents[row.agent_id]; if (!a) continue;
        a.approval[row.human_decision] = row.count;
      }
      for (const id of Object.keys(agents)) {
        const a = agents[id];
        const t = a.approval.approved + a.approval.overridden + a.approval.declined;
        a.approval.rate = t > 0 ? Math.round((a.approval.approved / t) * 100) : null;

        // Compute L-level combining auto-rate + approval rate:
        // L3 = ≥98% auto (runs without human)
        // L2 = ≥90% approval rate (agent correct, just needs sign-off) OR 50-98% auto
        // L1 = <50% auto AND (<90% approval or no approval data)
        const autoPct = a.l_level.auto_pct;
        const approvalRate = a.approval.rate;
        if (a.tasks.total === 0) {
          a.l_level.dominant = "—";
        } else if (autoPct >= 98) {
          a.l_level.dominant = "L3";
        } else if (approvalRate !== null && approvalRate >= 90) {
          a.l_level.dominant = "L2";
        } else if (autoPct >= 50) {
          a.l_level.dominant = "L2";
        } else {
          a.l_level.dominant = "L1";
        }

        // Compute L-level direction: ↑ = earning trust, ↓ = losing trust
        // Require minimum 3 overrides AND >5% of total to signal "down"
        const overriddenCount = a.l_level.overridden;
        const overriddenRate = a.tasks.total > 0 ? overriddenCount / a.tasks.total : 0;
        if (a.l_level.dominant === "L3" && overriddenCount >= 3) {
          a.l_level.direction = "down";
        } else if (a.l_level.dominant === "L2" && overriddenRate < 0.05 && approvalRate !== null && approvalRate >= 95) {
          a.l_level.direction = "up";
        } else if (overriddenCount >= 3 && overriddenRate > 0.05) {
          a.l_level.direction = "down";
        } else {
          a.l_level.direction = "stable";
        }
      }
      for (const row of accuracy.rows) {
        const a = agents[row.agent_id]; if (!a) continue;
        a.accuracy[row.ground_truth] = row.count;
      }
      for (const row of cost.rows) {
        const a = agents[row.agent_id]; if (!a) continue;
        const current = row.current_cost ? parseFloat(row.current_cost) : 0;
        const prev = row.prev_cost ? parseFloat(row.prev_cost) : 0;
        const trend_pct = prev > 0 ? Math.round(((current - prev) / prev) * 100) : null;
        a.cost = {
          total: current,
          avg: row.avg_cost ? parseFloat(row.avg_cost) : 0,
          tokens_in: row.tokens_in ? parseInt(row.tokens_in) : 0,
          tokens_out: row.tokens_out ? parseInt(row.tokens_out) : 0,
          trend_pct,
        };
      }
      // Add error breakdown to per-agent
      for (const row of errorBreakdown.rows) {
        const a = agents[row.agent_id]; if (!a) continue;
        a.errors = {
          total: row.total_errors || 0,
          tool: row.tool_errors || 0,
          llm: row.llm_errors || 0,
          other: Math.max(0, (row.total_errors || 0) - (row.tool_errors || 0) - (row.llm_errors || 0)),
        };
      }
      // Default errors for agents without data
      for (const id of Object.keys(agents)) {
        if (!agents[id].errors) agents[id].errors = { total: 0, tool: 0, llm: 0, other: 0 };
      }

      const ft = fleetTasks.rows[0] || {};
      const fc = fleetCost.rows[0] || {};
      const appCounts: Record<string, number> = {};
      for (const row of overallApproval.rows) appCounts[row.human_decision] = row.count;
      const appTotal = Object.values(appCounts).reduce((a: number, b: number) => a + b, 0);

      // Build safety counts
      const safety: Record<string, number> = {};
      for (const row of (safetyResult as any).rows || []) {
        safety[row.event_type] = row.count;
      }
      // Also count NFF from case_memory if available
      const nffCount = safety["false_positive_rma"] ?? 0;

      res.json({
        period_days: days,
        from,
        to,
        agents,
        monthly: monthly.rows.map((r: any) => ({
          month: r.month,
          total: r.total,
          completed: r.completed,
          failed: r.failed,
          avg_duration_s: r.avg_s,
          nodes_cordoned: cordonMap[r.month] ?? null,
          resolved_by_agent: resolvedMap[r.month] ?? 0,
          agent_cost: costMap[r.month] ?? 0,
          // Phase 1 additions
          mttr_p50_hours: mttrMap[r.month]?.p50 ?? null,
          mttr_p95_hours: mttrMap[r.month]?.p95 ?? null,
          incidents_hw: mttrMap[r.month]?.incidents ?? null,
          alert_precision_pct: precisionMap[r.month]
            ? Math.round(100 * precisionMap[r.month].confirmed / Math.max(precisionMap[r.month].total, 1))
            : null,
          rollback_rate_pct: rollbackMap[r.month]
            ? Math.round(100 * rollbackMap[r.month].recurrences / Math.max(rollbackMap[r.month].total, 1))
            : null,
          toil_hours_saved: toilMap[r.month] ?? null,
        })),
        fleet: {
          tasks: { total: ft.total ?? 0, completed: ft.completed ?? 0, failed: ft.failed ?? 0 },
          duration_s: { p50: ft.p50_s ?? null, p90: ft.p90_s ?? null, p95: ft.p95_s ?? null, p99: ft.p99_s ?? null },
          cost: { total: fc.total_cost ? parseFloat(fc.total_cost) : 0, avg: fc.avg_cost ? parseFloat(fc.avg_cost) : 0 },
          approval: { ...appCounts, rate: appTotal > 0 ? Math.round(((appCounts["approved"] ?? 0) / appTotal) * 100) : null },
          safety: {
            blast_radius_blocked: safety["blast_radius_blocked"] ?? 0,
            circuit_breaker_tripped: safety["circuit_breaker_tripped"] ?? 0,
            repair_recurrence: safety["repair_recurrence"] ?? 0,
            false_positive_rma: safety["false_positive_rma"] ?? 0,
            unsafe_executed: safety["unsafe_executed"] ?? 0,
            blast_radius_avg: blastRadius.rows[0]?.avg_nodes ?? null,
            blast_radius_max: blastRadius.rows[0]?.max_nodes ?? null,
          },
        },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("[agent-metrics]", msg);
      res.status(500).json({ error: msg });
    }
  });

  return router;
}
