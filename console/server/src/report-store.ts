/**
 * Report data store — queries against ltp_sdk schema on .19
 *
 * Status grouping:
 *   Allocatable   = available
 *   Allocated     = allocated_ua, allocated_platform
 *   Validating    = ready_ua, validating, new
 *   Cordon        = cordoned, triaged_unknown, triaged_hardware, triaged_platform
 *   OFR           = ua, deallocated_ua
 *   Deallocated = deallocated_platform, deallocated_capacity
 *   Unmanaged     = in physical_node_onboard_records but not in node_status
 *
 * Availability = Allocatable / (Total - Deallocated)
 * Time-weighted: node-equivalents based on hours in each state per day
 */
import { queryLtp } from "./db/ltp-pool.js";

// ─── Types ───────────────────────────────────────────────────────────

export interface DailyAvailabilityRow {
  day: string;
  allocatable: number;
  allocated: number;
  validating: number;
  cordon: number;
  ofr: number;
  deallocated: number;
  total: number;
  avail_pct: number;
}

export interface CurrentSnapshotRow {
  status_group: string;
  count: number;
  pct: number;
}

export interface InventoryRow {
  category: string;
  nodes: number;
  racks: number;
  top_sku: string;
}

export interface RmaSummary {
  opened: number;
  completed: number;
  pending: number;
}

// ─── Status → group mapping ──────────────────────────────────────────

const STATUS_GROUP_SQL = `
  CASE
    WHEN status = 'available' THEN 'Available'
    WHEN status IN ('allocated_ua','allocated_platform') THEN 'Allocating'
    WHEN status IN ('ready_ua','validating','new') THEN 'Validating'
    WHEN status IN ('cordoned','triaged_unknown','triaged_hardware','triaged_platform') THEN 'Cordon'
    WHEN status IN ('ua','deallocated_ua') THEN 'OFR'
    WHEN status IN ('deallocated_platform','deallocated_capacity') THEN 'Deallocated'
    ELSE 'Other'
  END`;

// ─── Hostname filter by category ─────────────────────────────────────

// Returns a SQL fragment that filters hostnames by category.
// Uses a CTE approach to avoid JOIN alias scope issues.
function categoryFilterCTE(category: string | undefined): {
  cte: string;
  joinCondition: string; // e.g. "AND ns.hostname IN (SELECT hostname FROM cat_hosts)"
  params: string[];
} {
  if (!category || category === "all") {
    return { cte: "", joinCondition: "", params: [] };
  }
  // Sanitize: only allow alphanumeric + underscore
  const safe = category.replace(/[^a-z0-9_]/gi, "");
  return {
    cte: `,
    cat_hosts AS (
      SELECT DISTINCT hostname
      FROM ltp_sdk.physical_node_onboard_records
      WHERE category = '${safe}'
    )`,
    joinCondition: `AND hostname IN (SELECT hostname FROM cat_hosts)`,
    params: [],
  };
}

// ─── 1. Time-weighted daily availability ──────────────────────────────

export async function getDailyAvailability(
  from: string,
  to: string,
  category?: string
): Promise<DailyAvailabilityRow[]> {
  const { cte, joinCondition } = categoryFilterCTE(category);

  const sql = `
    WITH days AS (
      SELECT generate_series(
        $1::date, $2::date, '1 day'::interval
      )::date AS day
    ) ${cte},
    status_groups AS (
      SELECT hostname,
        ${STATUS_GROUP_SQL} AS grp,
        timestamp,
        LEAD(timestamp) OVER (PARTITION BY hostname ORDER BY timestamp) AS next_ts
      FROM ltp_sdk.node_status
      WHERE timestamp < ($2::date + 1)::timestamp
      ${joinCondition}
    ),
    day_calc AS (
      SELECT d.day, sg.grp,
        EXTRACT(EPOCH FROM (
          LEAST(COALESCE(sg.next_ts, (d.day + 1)::timestamp), (d.day + 1)::timestamp) -
          GREATEST(sg.timestamp, d.day::timestamp)
        )) / 3600 AS hours
      FROM days d
      CROSS JOIN LATERAL (
        SELECT grp, timestamp, next_ts FROM status_groups
        WHERE timestamp < (d.day + 1)::timestamp
      ) sg
      WHERE LEAST(COALESCE(sg.next_ts, (d.day + 1)::timestamp), (d.day + 1)::timestamp) > d.day::timestamp
    )
    SELECT
      day::text,
      COALESCE(ROUND(SUM(hours) FILTER (WHERE grp = 'Available') / 24, 1), 0) AS allocatable,
      COALESCE(ROUND(SUM(hours) FILTER (WHERE grp = 'Allocating') / 24, 1), 0) AS allocated,
      COALESCE(ROUND(SUM(hours) FILTER (WHERE grp = 'Validating') / 24, 1), 0) AS validating,
      COALESCE(ROUND(SUM(hours) FILTER (WHERE grp = 'Cordon') / 24, 1), 0) AS cordon,
      COALESCE(ROUND(SUM(hours) FILTER (WHERE grp = 'OFR') / 24, 1), 0) AS ofr,
      COALESCE(ROUND(SUM(hours) FILTER (WHERE grp = 'Deallocated') / 24, 1), 0) AS deallocated,
      COALESCE(ROUND(SUM(hours) / 24, 1), 0) AS total,
      COALESCE(ROUND(
        SUM(hours) FILTER (WHERE grp = 'Available') /
        NULLIF(SUM(hours) FILTER (WHERE grp != 'Deallocated'), 0) * 100, 2
      ), 0) AS avail_pct
    FROM day_calc
    GROUP BY day
    ORDER BY day`;

  return queryLtp<DailyAvailabilityRow>(sql, [from, to]);
}

// ─── 2. Current snapshot ─────────────────────────────────────────────

export async function getCurrentSnapshot(
  category?: string
): Promise<CurrentSnapshotRow[]> {
  const { cte, joinCondition } = categoryFilterCTE(category);

  // Build WITH clause: cat_hosts (if needed) must come before latest
  const withClause = cte
    ? `WITH cat_hosts AS (
      SELECT DISTINCT hostname
      FROM ltp_sdk.physical_node_onboard_records
      WHERE category = '${(category || "").replace(/[^a-z0-9_]/gi, "")}'
    ),
    latest AS (
      SELECT DISTINCT ON (hostname) status
      FROM ltp_sdk.node_status
      WHERE 1=1 ${joinCondition}
      ORDER BY hostname, timestamp DESC
    )`
    : `WITH latest AS (
      SELECT DISTINCT ON (hostname) status
      FROM ltp_sdk.node_status
      ORDER BY hostname, timestamp DESC
    )`;

  const sql = `
    ${withClause}
    SELECT
      ${STATUS_GROUP_SQL} AS status_group,
      COUNT(*)::int AS count,
      ROUND(COUNT(*)::numeric / NULLIF(SUM(COUNT(*)) OVER(), 0) * 100, 1) AS pct
    FROM latest
    WHERE status IS NOT NULL
    GROUP BY ${STATUS_GROUP_SQL}
    ORDER BY count DESC`;

  return queryLtp<CurrentSnapshotRow>(sql);
}

// ─── 3. Unmanaged nodes ──────────────────────────────────────────────

export async function getUnmanagedCount(
  category?: string
): Promise<number> {
  const catFilter =
    category && category !== "all"
      ? `AND category = '${category.replace(/[^a-z0-9_]/gi, "")}'`
      : "";

  const sql = `
    SELECT COUNT(*)::int AS cnt
    FROM (
      SELECT DISTINCT hostname FROM ltp_sdk.physical_node_onboard_records
      WHERE 1=1 ${catFilter}
    ) po
    WHERE NOT EXISTS (
      SELECT 1 FROM ltp_sdk.node_status ns WHERE ns.hostname = po.hostname
    )`;

  const rows = await queryLtp<{ cnt: number }>(sql);
  return rows[0]?.cnt ?? 0;
}

// ─── 4. Hardware inventory ───────────────────────────────────────────

export async function getHardwareInventory(): Promise<InventoryRow[]> {
  const sql = `
    WITH cat AS (
      SELECT DISTINCT ON (hostname) hostname, category
      FROM ltp_sdk.physical_node_onboard_records
      ORDER BY hostname, timestamp DESC
    ),
    sku_rank AS (
      SELECT category, sku, COUNT(*) AS cnt,
        ROW_NUMBER() OVER (PARTITION BY category ORDER BY COUNT(*) DESC) AS rn
      FROM ltp_sdk.physical_node_onboard_records
      GROUP BY category, sku
    )
    SELECT
      c.category,
      COUNT(DISTINCT c.hostname)::int AS nodes,
      (SELECT COUNT(DISTINCT rack)::int FROM ltp_sdk.physical_node_onboard_records WHERE category = c.category) AS racks,
      COALESCE(s.sku, '—') AS top_sku
    FROM cat c
    LEFT JOIN sku_rank s ON s.category = c.category AND s.rn = 1
    GROUP BY c.category, s.sku
    ORDER BY nodes DESC`;

  return queryLtp<InventoryRow>(sql);
}

// ─── 5. RMA summary ─────────────────────────────────────────────────

export async function getRmaSummary(
  from?: string,
  to?: string,
  category?: string
): Promise<RmaSummary> {
  const dateFilter =
    from && to
      ? `AND pna.timestamp >= $1::timestamp AND pna.timestamp < ($2::date + 1)::timestamp`
      : "";

  const { cte, joinCondition } = categoryFilterCTE(category);
  // For RMA we filter by onboard_id's category
  const catHostFilter =
    category && category !== "all"
      ? `AND pna.onboard_id IN (SELECT id FROM ltp_sdk.physical_node_onboard_records WHERE category = '${category.replace(/[^a-z0-9_]/gi, "")}')`
      : "";

  const params = from && to ? [from, to] : [];

  const sql = `
    WITH initiated AS (
      SELECT DISTINCT ON (pna.ticket_id) pna.ticket_id
      FROM ltp_sdk.physical_node_actions pna
      WHERE pna.op_type = 'InitiateRMA' ${dateFilter} ${catHostFilter}
    ),
    completed_anytime AS (
      SELECT DISTINCT ON (pna.ticket_id) pna.ticket_id
      FROM ltp_sdk.physical_node_actions pna
      WHERE pna.op_type = 'CompleteRMA' ${catHostFilter}
    ),
    pending AS (
      SELECT COUNT(*)::int AS cnt FROM initiated i
      WHERE NOT EXISTS (
        SELECT 1 FROM completed_anytime c WHERE c.ticket_id = i.ticket_id
      )
    )
    SELECT
      (SELECT COUNT(*)::int FROM initiated) AS opened,
      (SELECT COUNT(*)::int FROM initiated) - (SELECT cnt FROM pending) AS completed,
      (SELECT cnt FROM pending) AS pending`;

  const rows = await queryLtp<RmaSummary>(sql, params);
  return rows[0] ?? { opened: 0, completed: 0, pending: 0 };
}

// ─── Reliability ────────────────────────────────────────────────────

export interface ReliabilityKpi {
  total_failures: number;
  avg_per_day: number;
  avg_cordon_fix_days: number;
  alert_level: string; // "normal" | "warning" | "critical"
}

export interface DailyFailureRow {
  day: string;
  total: number;
  hardware: number;
  platform: number;
  unknown: number;
  user: number;
}

export interface FailureCategoryRow {
  category: string;   // hardware | platform | unknown | user
  count: number;
  pct: number;
  reasons: { reason: string; count: number }[];
}

export interface RcaReasonRow {
  reason: string;
  category: string;
  count: number;
  wow: number;
  share: number;
}

export interface RecycleTimeRow {
  period_label: string;
  avg_days: number;
  count: number;
}

export interface FailureDetailRow {
  hostname: string;
  ip: string;
  category: string;
  reason: string;
  detail: string;
  timestamp: string;
}

export interface ReliabilityResponse {
  kpi: ReliabilityKpi;
  daily: DailyFailureRow[];
  categories: FailureCategoryRow[];
  rca: RcaReasonRow[];
  ofr_recycle: RecycleTimeRow[];
  ofr_avg_days: number;
  ofr_count: number;
  cordon_recycle: RecycleTimeRow[];
  cordon_avg_days: number;
  cordon_count: number;
  details: FailureDetailRow[];
}

// Helper: CTE for real failures (available-cordoned excluding cordoned-new)
function failureCte(dateFilter: string, catHostFilter: string, params: any[]) {
  return {
    sql: `real_failures AS (
      SELECT c.id, c.hostname, c.timestamp AS fail_time
      FROM ltp_sdk.node_actions c
      WHERE c.action = 'available-cordoned'
        ${dateFilter}
        ${catHostFilter}
        AND NOT EXISTS (
          SELECT 1 FROM ltp_sdk.node_actions n
          WHERE n.hostname = c.hostname
            AND n.action = 'cordoned-new'
            AND n.timestamp > c.timestamp
            AND n.timestamp < c.timestamp + INTERVAL '1 hour'
        )
    )`,
    params,
  };
}

// Helper: CTE for next *-available after each failure
function nextAvailCte() {
  return `next_avail AS (
    SELECT DISTINCT ON (f.id) f.id AS fail_id, a.timestamp AS avail_time
    FROM real_failures f
    JOIN ltp_sdk.node_actions a ON a.hostname = f.hostname
      AND a.action LIKE '%-available'
      AND a.timestamp > f.fail_time
    ORDER BY f.id, a.timestamp ASC
  )`;
}

// Helper: CTE for last *-triaged_* category
// Case 1: node recovered → last triage before *-available
// Case 2: node not recovered → last triage after cordon (anytime)
function lastTriageCte() {
  return `last_triage AS (
    SELECT DISTINCT ON (f.id) f.id AS fail_id,
      NULLIF(t.category, '') AS category,
      t.reason
    FROM real_failures f
    LEFT JOIN next_avail na ON na.fail_id = f.id
    JOIN ltp_sdk.node_actions t ON t.hostname = f.hostname
      AND t.action LIKE '%triaged%'
      AND t.action NOT LIKE 'triaged_%-available'
      AND t.action NOT LIKE 'triaged_%-validating'
      AND t.action NOT LIKE 'triaged_%-allocated%'
      AND t.action NOT LIKE 'triaged_%-ready%'
      AND t.action NOT LIKE 'triaged_%-deallocated%'
      AND t.action NOT LIKE 'triaged_%-ua'
      AND t.timestamp > f.fail_time
      AND (na.avail_time IS NULL OR t.timestamp < na.avail_time)
    ORDER BY f.id, t.timestamp DESC
  )`;
}

// Helper: CTE for ua path fallback (hardware)
// Works for both recovered and not-yet-recovered nodes
function hasUaCte() {
  return `has_ua AS (
    SELECT DISTINCT ON (f.id) f.id AS fail_id
    FROM real_failures f
    LEFT JOIN next_avail na ON na.fail_id = f.id
    JOIN ltp_sdk.node_actions u ON u.hostname = f.hostname
      AND (u.action LIKE '%-ua' OR u.action LIKE 'ua-%' OR u.action = 'ua' OR u.action LIKE '%deallocated_ua%')
      AND u.timestamp > f.fail_time
      AND (na.avail_time IS NULL OR u.timestamp < na.avail_time)
  )`;
}

// Helper: CTE for cordon alert reason (from the available-cordoned row itself)
function cordonReasonCte() {
  return `cordon_reason AS (
    SELECT rf.id AS fail_id,
      COALESCE(NULLIF(SPLIT_PART(SPLIT_PART(na.reason, ':', 1), ' ', 1), ''), 'Unknown') AS alert_name
    FROM real_failures rf
    JOIN ltp_sdk.node_actions na ON na.id = rf.id
  )`;
}

// Helper: CTE for RMA reason (from triaged_hardware-deallocated_ua or physical_node_actions)
function rmaReasonCte() {
  return `rma_reason AS (
    SELECT DISTINCT ON (f.id) f.id AS fail_id, da.reason AS rma_reason
    FROM real_failures f
    LEFT JOIN next_avail na ON na.fail_id = f.id
    JOIN ltp_sdk.node_actions da ON da.hostname = f.hostname
      AND da.action = 'triaged_hardware-deallocated_ua'
      AND da.timestamp > f.fail_time
      AND (na.avail_time IS NULL OR da.timestamp < na.avail_time)
    ORDER BY f.id, da.timestamp ASC
  )`;
}

// Final reason: cordon alert → RMA reason → triage reason → Unknown
// Skip generic reasons: InitiateRMA, Submitting validation job, Retry reallocation
function finalReasonExpr() {
  // Priority: 1) cordon alert (unless generic), 2) RMA reason (unless generic), 3) triage reason, 4) Unknown
  // Generic alert names that aren't real root causes — skip to triage/RMA
  const genericAlerts = [
    'NodeNotReady', 'PaiServicePodNotRunning', 'PaiServicePodNotReady',
    'NodeDiskPressure', 'patrol_manual_action', 'RecoverValidatedNodes',
    'debug_test', 'test_cordon',
  ];
  const genericRma = [
    'InitiateRMA', 'Submitting validation job for VM', 'Retry reallocation',
    'SSH restored - retry reallocation',
  ];
  return `COALESCE(
    CASE WHEN cr.alert_name IN (${genericAlerts.map(a => `'${a}'`).join(', ')}) THEN NULL ELSE NULLIF(cr.alert_name, '') END,
    CASE WHEN rr.rma_reason IN (${genericRma.map(a => `'${a}'`).join(', ')}) THEN NULL ELSE NULLIF(rr.rma_reason, '') END,
    NULLIF(lt.reason, ''),
    NULLIF(cr.alert_name, ''),
    'Unknown'
  )`;
}
function finalCategoryExpr() {
  return `CASE WHEN hu.fail_id IS NOT NULL THEN 'hardware' ELSE COALESCE(NULLIF(lt.category, ''), 'unknown') END`;
}

export async function getReliabilityData(
  from?: string,
  to?: string,
  category?: string
): Promise<ReliabilityResponse> {
  const params: any[] = [];
  let dateFilter = '';
  if (from && to) {
    params.push(from, to);
    dateFilter = `AND c.timestamp >= $1::date AND c.timestamp < ($2::date + INTERVAL '1 day')`;
  }

  let catHostFilter = '';
  if (category && category !== 'all') {
    params.push(category);
    const pIdx = params.length;
    catHostFilter = `AND c.hostname IN (
      SELECT hostname FROM ltp_sdk.physical_node_onboard_records
      WHERE category = $${pIdx}
    )`;
  }

  const prevParams: any[] = [];
  let prevDateFilter = '';
  if (from && to) {
    const periodDays = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 86400000);
    const prevTo = new Date(from);
    const prevFrom = new Date(prevTo.getTime() - periodDays * 86400000);
    prevParams.push(prevFrom.toISOString().slice(0, 10), prevTo.toISOString().slice(0, 10));
    prevDateFilter = `AND c.timestamp >= $1::date AND c.timestamp < $2::date`;
  }
  if (category && category !== 'all') {
    prevParams.push(category);
  }

  // Build common CTEs
  const failCte = failureCte(dateFilter, catHostFilter, params);
  const ctes = [
    failCte.sql,
    nextAvailCte(),
    lastTriageCte(),
    hasUaCte(),
    cordonReasonCte(),
    rmaReasonCte(),
  ].join(', ');

  const fc = finalCategoryExpr();
  const fr = finalReasonExpr();

  // ── KPI ──
  const fullCtes = `WITH ${ctes}`;
  const kpiSql = `
    ${fullCtes}
    SELECT
      COUNT(*)::int AS total_failures,
      ROUND(COUNT(*)::numeric / GREATEST(EXTRACT(DOY FROM ($2::date + INTERVAL '1 day')::timestamp) -
        EXTRACT(DOY FROM $1::date::timestamp) + 1, 1), 1) AS avg_per_day,
      ROUND(AVG(EXTRACT(EPOCH FROM (na.avail_time - f.fail_time)) / 86400)::numeric, 1) AS avg_cordon_fix_days
    FROM real_failures f
    LEFT JOIN next_avail na ON na.fail_id = f.id
    LEFT JOIN last_triage lt ON lt.fail_id = f.id
    LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id`;

  const kpiRows = await queryLtp<any>(kpiSql, params);
  const kpiData = kpiRows[0] ?? {};
  const avgPerDay = parseFloat(kpiData.avg_per_day) || 0;
  const alertLevel = avgPerDay < 15 ? 'normal' : avgPerDay < 50 ? 'warning' : 'critical';

  // ── Daily failures ──
  const dailySql = `
    WITH ${ctes}
    SELECT DATE(f.fail_time)::text AS day,
      COUNT(*)::int AS total,
      COUNT(*) FILTER (WHERE ${fc} = 'hardware') AS hardware,
      COUNT(*) FILTER (WHERE ${fc} = 'platform') AS platform,
      COUNT(*) FILTER (WHERE ${fc} = 'unknown') AS unknown,
      COUNT(*) FILTER (WHERE ${fc} = 'user') AS user
    FROM real_failures f
    LEFT JOIN last_triage lt ON lt.fail_id = f.id
    LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
    GROUP BY DATE(f.fail_time)
    ORDER BY day`;

  const daily = await queryLtp<DailyFailureRow>(dailySql, params);

  // ── Failure categorization ──
  const catSql = `
    WITH ${cteSkipCheck(ctes)}
    SELECT ${fc} AS category,
      COUNT(*)::int AS count,
      ROUND(COUNT(*)::numeric * 100 / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS pct
    FROM real_failures f
    LEFT JOIN last_triage lt ON lt.fail_id = f.id
    LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
    GROUP BY ${fc}
    ORDER BY count DESC`;

  const catRows = await queryLtp<any>(catSql, params);

  // Get sub-reasons per category
  const reasonSql = `
    WITH ${cteSkipCheck(ctes)}
    SELECT ${fc} AS category, ${fr} AS reason, COUNT(*)::int AS count
    FROM real_failures f
    LEFT JOIN last_triage lt ON lt.fail_id = f.id
    LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
    GROUP BY ${fc}, ${fr}
    ORDER BY ${fc}, count DESC`;

  const reasonRows = await queryLtp<any>(reasonSql, params);

  const categories: FailureCategoryRow[] = catRows.map((cr: any) => ({
    category: cr.category,
    count: cr.count,
    pct: cr.pct,
    reasons: reasonRows
      .filter((rr: any) => rr.category === cr.category)
      .map((rr: any) => ({ reason: rr.reason, count: rr.count })),
  }));

  // ── RCA reasons ──
  // Build previous period CTEs for WoW
  const prevFailCte = failureCte(prevDateFilter, catHostFilter, prevParams);
  const prevCtes = [prevFailCte.sql, nextAvailCte(), lastTriageCte(), hasUaCte(), cordonReasonCte(), rmaReasonCte()].join(', ');

  const rcaSql = `
    WITH ${cteSkipCheck(ctes)}
    SELECT ${fr} AS reason,
      ${fc} AS category,
      COUNT(*)::int AS count,
      ROUND(COUNT(*)::numeric * 100 / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS share
    FROM real_failures f
    LEFT JOIN last_triage lt ON lt.fail_id = f.id
    LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
    GROUP BY ${fr}, ${fc}
    ORDER BY count DESC
    LIMIT 10`;

  const rcaRows = await queryLtp<any>(rcaSql, params);

  // Get previous period counts for WoW
  const prevRcaSql = `
    WITH ${cteSkipCheck(prevCtes)}
    SELECT ${fr} AS reason,
      ${finalCategoryExpr()} AS category,
      COUNT(*)::int AS count
    FROM real_failures f
    LEFT JOIN last_triage lt ON lt.fail_id = f.id
    LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
    GROUP BY ${fr}, ${fc}`;

  const prevRcaRows = await queryLtp<any>(prevRcaSql, prevParams);
  const prevRcaMap = new Map<string, number>(prevRcaRows.map((r: any) => [`${r.reason}|${r.category}`, r.count as number]));

  const rca: RcaReasonRow[] = rcaRows.map((r: any) => ({
    reason: r.reason,
    category: r.category,
    count: r.count,
    wow: r.count - (prevRcaMap.get(`${r.reason}|${r.category}`) ?? 0),
    share: r.share,
  }));

  // ── Recycle times ──
  // OFR = hardware category, Cordon = non-hardware
  const recycleSql = `
    WITH ${cteSkipCheck(ctes)},
    recycle AS (
      SELECT f.id, ${fc} AS category,
        EXTRACT(EPOCH FROM (na.avail_time - f.fail_time)) / 86400 AS days_to_fix
      FROM real_failures f
      JOIN next_avail na ON na.fail_id = f.id
      LEFT JOIN last_triage lt ON lt.fail_id = f.id
      LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
      WHERE na.avail_time IS NOT NULL
    )
    SELECT
      ROUND(AVG(days_to_fix) FILTER (WHERE category = 'hardware'), 1) AS ofr_avg_days,
      COUNT(*) FILTER (WHERE category = 'hardware') AS ofr_count,
      ROUND(AVG(days_to_fix) FILTER (WHERE category != 'hardware'), 1) AS cordon_avg_days,
      COUNT(*) FILTER (WHERE category != 'hardware') AS cordon_count
    FROM recycle`;

  const recycleRows = await queryLtp<any>(recycleSql, params);
  const recycleData = recycleRows[0] ?? {};

  // Weekly sparkline for recycle times (last 7 weeks)
  const ofrRecycle: RecycleTimeRow[] = [];
  const cordonRecycle: RecycleTimeRow[] = [];
  if (from && to) {
    const endDate = new Date(to);
    for (let w = 6; w >= 0; w--) {
      const wEnd = new Date(endDate.getTime() - w * 7 * 86400000);
      const wStart = new Date(wEnd.getTime() - 7 * 86400000);
      const wParams = [wStart.toISOString().slice(0, 10), wEnd.toISOString().slice(0, 10)];
      const wFailCte = failureCte(
        `AND c.timestamp >= $1::date AND c.timestamp < $2::date`,
        catHostFilter, [...wParams, ...params.slice(wParams.length)]
      );
      const wCtes = [wFailCte.sql, nextAvailCte(), lastTriageCte(), hasUaCte(), cordonReasonCte(), rmaReasonCte()].join(', ');
      const wSql = `
        WITH ${cteSkipCheck(wCtes)},
        recycle AS (
          SELECT f.id, ${fc} AS category,
            EXTRACT(EPOCH FROM (na.avail_time - f.fail_time)) / 86400 AS days_to_fix
          FROM real_failures f
          JOIN next_avail na ON na.fail_id = f.id
          LEFT JOIN last_triage lt ON lt.fail_id = f.id
          LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
          WHERE na.avail_time IS NOT NULL
        )
        SELECT
          ROUND(AVG(days_to_fix) FILTER (WHERE category = 'hardware'), 1) AS ofr_avg,
          COUNT(*) FILTER (WHERE category = 'hardware') AS ofr_cnt,
          ROUND(AVG(days_to_fix) FILTER (WHERE category != 'hardware'), 1) AS cordon_avg,
          COUNT(*) FILTER (WHERE category != 'hardware') AS cordon_cnt
        FROM recycle`;
      const wFullParams = [...wParams, ...params.slice(2)]; // add category if present
      const wRows = await queryLtp<any>(wSql, wFullParams);
      const wd = wRows[0] ?? {};
      const weekLabel = `W${getWeekNumber(wStart)}`;
      ofrRecycle.push({ period_label: weekLabel, avg_days: parseFloat(wd.ofr_avg) || 0, count: wd.ofr_cnt || 0 });
      cordonRecycle.push({ period_label: weekLabel, avg_days: parseFloat(wd.cordon_avg) || 0, count: wd.cordon_cnt || 0 });
    }
  }

  // ── Failure details ──
  const detailsSql = `
    WITH ${cteSkipCheck(ctes)},
    node_ip AS (
      SELECT DISTINCT ON (p.hostname, f.id) p.hostname,
        COALESCE(p.ip::json->>0, '—') AS ip,
        f.id AS fail_id
      FROM real_failures f
      JOIN ltp_sdk.physical_node_onboard_records p ON p.hostname = f.hostname
        AND p.timestamp < f.fail_time
      ORDER BY p.hostname, f.id, p.timestamp DESC
    ),
    detail_reason AS (
      SELECT DISTINCT ON (f.id) f.id AS fail_id,
        COALESCE(
          -- Best: plain-text detail from triaged_hardware actions (e.g. triaged_unknown-triaged_hardware has detailed investigation)
          NULLIF(
            CASE WHEN t.detail IS NOT NULL AND t.detail::text NOT IN ('', '{}', '[]', 'null') AND t.detail::text NOT LIKE '{%' AND t.detail::text NOT LIKE '[{"node_name"%' AND LENGTH(t.detail::text) > 20
              THEN substring(t.detail::text, 1, 500)
              ELSE '' END,
            ''
          ),
          -- Next: summary from JSON detail object (extract "summary":"..." via regex to avoid invalid JSON errors)
          NULLIF(
            CASE WHEN t.detail IS NOT NULL AND t.detail::text NOT IN ('', '{}', '[]', 'null') AND t.detail::text NOT LIKE '{"NodeId":%' AND t.detail::text NOT LIKE '{"node_name":%'
              THEN substring(t.detail::text from '"summary":\\s*"([^"]*)"')
              ELSE '' END,
            ''
          ),
          -- Fallback: meaningful reason text
          NULLIF(
            CASE WHEN t.reason NOT LIKE '%Patrol detection%' AND t.reason NOT LIKE 'Submitting%' AND t.reason NOT LIKE 'InitiateRMA' AND t.reason NOT LIKE 'RecoverValidatedNodes%' AND t.reason NOT IN ('patrol_manual_action', 'NodeNotReady', 'PaiServicePodNotRunning', 'PaiServicePodNotReady', 'NodeDiskPressure') THEN t.reason ELSE '' END,
            ''
          ),
          ''
        ) AS detail
      FROM real_failures f
      LEFT JOIN next_avail na ON na.fail_id = f.id
      JOIN ltp_sdk.node_actions t ON t.hostname = f.hostname
        AND t.action IN ('cordoned-triaged_hardware', 'cordoned-triaged_unknown', 'cordoned-triaged_platform', 'triaged_unknown-triaged_hardware', 'triaged_unknown-triaged_platform', 'triaged_hardware-deallocated_ua', 'deallocated_ua-ua', 'ua-ready_ua', 'available-cordoned')
        AND t.timestamp >= f.fail_time
        AND (na.avail_time IS NULL OR t.timestamp < na.avail_time)
        AND (
          (LENGTH(t.reason) > 20 AND t.reason NOT LIKE '%Patrol detection%' AND t.reason NOT LIKE 'Submitting%' AND t.reason NOT LIKE 'InitiateRMA%' AND t.reason NOT LIKE 'RecoverValidatedNodes%')
          OR (t.detail IS NOT NULL AND t.detail::text NOT IN ('', '{}', '[]', 'null') AND t.detail::text NOT LIKE '{"NodeId":%' AND LENGTH(t.detail::text) > 20)
        )
      ORDER BY f.id,
        CASE WHEN t.action IN ('triaged_unknown-triaged_hardware', 'triaged_unknown-triaged_platform') THEN 0
             WHEN t.action = 'cordoned-triaged_hardware' THEN 1
             WHEN t.action = 'triaged_hardware-deallocated_ua' THEN 2
             WHEN t.action IN ('deallocated_ua-ua', 'ua-ready_ua') THEN 3
             WHEN t.action = 'available-cordoned' THEN 4
             ELSE 5 END
    )
    SELECT f.hostname,
      COALESCE(ni.ip, '—') AS ip,
      ${fc} AS category,
      ${fr} AS reason,
      COALESCE(dr.detail, '') AS detail,
      f.fail_time AS timestamp
    FROM real_failures f
    LEFT JOIN last_triage lt ON lt.fail_id = f.id
    LEFT JOIN has_ua hu ON hu.fail_id = f.id
    LEFT JOIN cordon_reason cr ON cr.fail_id = f.id
    LEFT JOIN rma_reason rr ON rr.fail_id = f.id
    LEFT JOIN node_ip ni ON ni.fail_id = f.id
    LEFT JOIN detail_reason dr ON dr.fail_id = f.id
    ORDER BY f.fail_time DESC
    LIMIT 100`;

  const details = await queryLtp<FailureDetailRow>(detailsSql, params);

  return {
    kpi: {
      total_failures: kpiData.total_failures ?? 0,
      avg_per_day: avgPerDay,
      avg_cordon_fix_days: parseFloat(kpiData.avg_cordon_fix_days) || 0,
      alert_level: alertLevel,
    },
    daily,
    categories,
    rca,
    ofr_recycle: ofrRecycle,
    ofr_avg_days: parseFloat(recycleData.ofr_avg_days) || 0,
    ofr_count: recycleData.ofr_count || 0,
    cordon_recycle: cordonRecycle,
    cordon_avg_days: parseFloat(recycleData.cordon_avg_days) || 0,
    cordon_count: recycleData.cordon_count || 0,
    details,
  };
}

// Avoid duplicate CTE definitions — strip leading WITH from nested CTEs
function cteSkipCheck(ctes: string): string {
  return ctes;
}

function getWeekNumber(d: Date): number {
  const start = new Date(d.getFullYear(), 0, 1);
  const diff = d.getTime() - start.getTime();
  return Math.ceil((diff / 86400000 + start.getDay() + 1) / 7);
}

// ─── Node MTBF ───────────────────────────────────────────────────────
//
// MTBF = time between consecutive hardware failures.
// A "hardware failure" is an available-cordoned event where the recovery path
// (until the next validating-available) goes through UA state
// (deallocated_ua-ua or ua-ready_ua), indicating physical intervention.
//
// Non-hardware failures (available-cordoned without UA in recovery) do NOT
// reset the MTBF clock. The uptime period continues through them.
//
// Example:
//   00:00 hw fail → UA → 00:40 recovery (clock starts)
//   03:00 non-hw fail → 03:40 recovery (IGNORED, clock still at 00:40)
//   05:00 hw fail → MTBF = 05:00 - 00:40 = 4h20m

export interface MtbfNodeRow {
  rank: number;
  hostname: string;
  endpoint: string;
  category: string;
  failures: number;
  mtbf_hours: number | null;
  mtbf_days: number | null;
  failure_details: { timestamp: string; reason: string; category: string }[];
}

export interface MtbfSummary {
  total_nodes: number;
  total_failures: number;
  avg_mtbf_hours: number;
  avg_mtbf_days: number;
}

export interface MtbfTrendRow {
  week: string;
  avg_mtbf_hours: number;
  avg_mtbf_days: number;
  cumulative_mtbf_hours: number;
  cumulative_mtbf_days: number;
  node_count: number;
  failure_count: number;
}

export interface MtbfResponse {
  summary: MtbfSummary;
  nodes: MtbfNodeRow[];
  trend: MtbfTrendRow[];
}

export async function getMtbfData(
  from: string,
  to: string,
  category?: string,
  reasonSearch?: string,
  nodeSearchParam?: string,
  mtbfType?: string
): Promise<MtbfResponse> {
  const catFilter = category && category !== "all"
    ? `AND hostname IN (SELECT DISTINCT hostname FROM ltp_sdk.physical_node_onboard_records WHERE category = '${category.replace(/[^a-z0-9_]/gi, "")}')`
    : "";

  const reasonFilter = reasonSearch && reasonSearch.trim()
    ? `AND EXISTS(
      SELECT 1 FROM ltp_sdk.node_actions na2
      WHERE na2.hostname = a.hostname
        AND na2.timestamp >= a.timestamp
        AND na2.timestamp < COALESCE(
          (SELECT MIN(timestamp) FROM ltp_sdk.node_actions va WHERE va.hostname = a.hostname AND va.action = 'validating-available' AND va.timestamp > a.timestamp),
          a.timestamp + INTERVAL '7 days'
        )
        AND (na2.reason ILIKE '%${reasonSearch.replace(/'/g, "''").replace(/[%_]/g, c => '\\' + c)}%'
          OR na2.detail::text ILIKE '%${reasonSearch.replace(/'/g, "''").replace(/[%_]/g, c => '\\' + c)}%')
    )`
    : "";

  const nodeSearch = nodeSearchParam && nodeSearchParam.trim()
    ? `AND a.hostname ILIKE '%${nodeSearchParam.replace(/'/g, "''").replace(/[%_]/g, c => '\\' + c)}%'`
    : "";

  // When mtbfType = "all", count ALL failures (not just hardware with UA path)
  const hwFilter = mtbfType === "all" ? "1=1" : "is_hardware = true";

  // Cumulative calculation: use all historical data for MTBF computation
  // The time filter only controls which failures are displayed (in the query period)
  // MTBF for each failure uses the real previous hardware failure, even if months ago
  // Extend end date by 30 days to catch recoveries/UA actions that happen after the query period
  const lookbackDate = '2024-01-01';
  const endDateBuffer = new Date(new Date(to).getTime() + 30 * 86400000).toISOString().slice(0, 10);

  // Step 1: Get all available-cordoned and validating-available events
  // Step 2: For each available-cordoned, find the next validating-available (recovery)
  // Step 3: Check if recovery path includes UA actions → hardware failure
  // Step 4: For each hardware failure, MTBF = fail_time - previous hardware failure's recovery
  const sql = `
    WITH relevant_actions AS (
      SELECT hostname, timestamp, action, reason, endpoint
      FROM ltp_sdk.node_actions
      WHERE timestamp >= $1::timestamptz
        AND timestamp < $2::timestamptz
        ${catFilter}
    ),
    -- All available-cordoned events with their next recovery (validating-available)
    all_failures AS (
      SELECT hostname, timestamp AS fail_time, reason AS fail_reason, endpoint,
        (SELECT MIN(timestamp) FROM relevant_actions a2
         WHERE a2.hostname = a.hostname
           AND a2.action = 'validating-available'
           AND a2.timestamp > a.timestamp) AS recovery_time
      FROM relevant_actions a
      WHERE a.action = 'available-cordoned'
        ${reasonFilter}
        ${nodeSearch}
    ),
    -- Hardware failures: recovery path includes UA state
    -- UA state = deallocated_ua-ua (entering UA) or ua-ready_ua (leaving UA)
    hw_failures AS (
      SELECT af.*,
        EXISTS(
          SELECT 1 FROM relevant_actions aa
          WHERE aa.hostname = af.hostname
            AND aa.timestamp > af.fail_time
            AND (af.recovery_time IS NULL OR aa.timestamp < af.recovery_time)
            AND aa.action IN ('deallocated_ua-ua', 'ua-ready_ua')
        ) AS is_hardware
      FROM all_failures af
    ),
    -- Only keep hardware failures
    hw_only AS (
      SELECT hostname, fail_time, fail_reason, endpoint, recovery_time
      FROM hw_failures
      WHERE ${hwFilter}
    ),
    -- For each hardware failure, find the PREVIOUS hardware failure's recovery time
    -- (this is the uptime clock start — non-hw recoveries are skipped)
    mtbf_calc AS (
      SELECT ho.hostname, ho.fail_time, ho.fail_reason, ho.endpoint, ho.recovery_time,
        -- uptime_start priority:
        -- 1. Last validating-available before this failure (normal recovery)
        -- 2. Last available-cordoned before this failure (previous failure)
        -- 3. First new-validating for this node (onboard time)
        COALESCE(
          (SELECT MAX(a.timestamp) FROM relevant_actions a
           WHERE a.hostname = ho.hostname
             AND a.action = 'validating-available'
             AND a.timestamp < ho.fail_time
          ),
          (SELECT MAX(a.timestamp) FROM relevant_actions a
           WHERE a.hostname = ho.hostname
             AND a.action = 'available-cordoned'
             AND a.timestamp < ho.fail_time
          ),
          (SELECT MIN(a.timestamp) FROM relevant_actions a
           WHERE a.hostname = ho.hostname
             AND a.action = 'new-validating'
          )
        ) AS uptime_start
      FROM hw_only ho
    )
    SELECT
      mc.hostname,
      MAX(mc.endpoint) AS endpoint,
      COUNT(*) AS failures,
      ROUND(AVG(EXTRACT(EPOCH FROM (mc.fail_time - mc.uptime_start)) / 3600)::numeric, 1) AS avg_mtbf_hours,
      ROUND((AVG(EXTRACT(EPOCH FROM (mc.fail_time - mc.uptime_start)) / 3600) / 24)::numeric, 1) AS avg_mtbf_days
    FROM mtbf_calc mc
    WHERE mc.uptime_start IS NOT NULL
      AND mc.fail_time >= $3::timestamptz  -- only count failures in the query period
    GROUP BY mc.hostname
    ORDER BY failures DESC, avg_mtbf_hours ASC
    LIMIT 200`;

  const rows = await queryLtp<any>(sql, [lookbackDate, endDateBuffer, from]);

  // Fetch failure details (all hardware failures in the period, with triage category)
  const detailSql = `
    WITH relevant_actions AS (
      SELECT hostname, timestamp, action, reason, detail, endpoint, category
      FROM ltp_sdk.node_actions
      WHERE timestamp >= $1::timestamptz
        AND timestamp < $2::timestamptz
        ${catFilter}
    ),
    all_failures AS (
      SELECT hostname, timestamp AS fail_time, reason AS fail_reason, endpoint,
        (SELECT MIN(timestamp) FROM relevant_actions a2
         WHERE a2.hostname = a.hostname
           AND a2.action = 'validating-available'
           AND a2.timestamp > a.timestamp) AS recovery_time
      FROM relevant_actions a
      WHERE a.action = 'available-cordoned'
        ${reasonFilter}
        ${nodeSearch}
    ),
    hw_failures AS (
      SELECT af.*,
        EXISTS(
          SELECT 1 FROM relevant_actions aa
          WHERE aa.hostname = af.hostname
            AND aa.timestamp > af.fail_time
            AND (af.recovery_time IS NULL OR aa.timestamp < af.recovery_time)
            AND aa.action IN ('deallocated_ua-ua', 'ua-ready_ua')
        ) AS is_hardware
      FROM all_failures af
    )
    SELECT hf.hostname, hf.fail_time, hf.fail_reason,
      COALESCE(t.category, 'hardware') AS triage_cat,
      t.reason AS triage_reason,
      t.detail AS triage_detail
    FROM hw_failures hf
    LEFT JOIN LATERAL (
      -- Get triage + detail from cordoned-triaged_* OR triaged_*-deallocated_ua OR deallocated_ua-ua
      SELECT category, reason, detail FROM relevant_actions
      WHERE hostname = hf.hostname
        AND (
          action LIKE 'cordoned-triaged_%'
          OR action = 'triaged_hardware-deallocated_ua'
          OR action = 'deallocated_ua-ua'
        )
        AND timestamp >= hf.fail_time
        AND (hf.recovery_time IS NULL OR timestamp < hf.recovery_time)
        -- Prefer actions with non-trivial detail text
        AND (
          detail IS NOT NULL AND detail::text NOT IN ('', '{}', '[]', 'null')
        )
      ORDER BY 
        CASE WHEN detail::text LIKE '%"summary"%' THEN 0 ELSE 1 END,
        timestamp ASC
      LIMIT 1
    ) t ON true
    WHERE ${hwFilter}
      AND hf.fail_time >= $3::timestamptz
    ORDER BY hf.hostname, hf.fail_time DESC`;

  const detailRows = await queryLtp<any>(detailSql, [lookbackDate, endDateBuffer, from]);

  const detailsMap = new Map<string, { timestamp: string; reason: string; category: string }[]>();
  for (const dr of detailRows) {
    if (!detailsMap.has(dr.hostname)) detailsMap.set(dr.hostname, []);
    // Build display reason: triage_reason + detail (full investigation text)
    let displayReason = dr.triage_reason || dr.fail_reason || "";
    // Extract useful detail text (same logic as Node Failure Details)
    if (dr.triage_detail) {
      const detailText = String(dr.triage_detail);
      if (detailText && detailText !== '{}' && detailText !== '[]' && detailText !== 'null' && detailText.length > 20) {
        if (detailText.startsWith('{') || detailText.startsWith('[')) {
          // JSON detail — try to extract summary field
          const summaryMatch = detailText.match(/"summary":\s*"([^"]*)"/);
          if (summaryMatch) {
            displayReason = summaryMatch[1];
          }
        } else {
          // Plain text detail — use directly
          displayReason = detailText.slice(0, 500);
        }
      }
    }
    detailsMap.get(dr.hostname)!.push({
      timestamp: dr.fail_time,
      reason: displayReason,
      category: dr.triage_cat || "hardware",
    });
  }

  const nodes: MtbfNodeRow[] = rows.map((r, i) => ({
    rank: i + 1,
    hostname: r.hostname,
    endpoint: r.endpoint || "\u2014",
    category: r.category || "unknown",
    failures: parseInt(r.failures) || 0,
    mtbf_hours: r.avg_mtbf_hours ? parseFloat(r.avg_mtbf_hours) : null,
    mtbf_days: r.avg_mtbf_days ? parseFloat(r.avg_mtbf_days) : null,
    failure_details: detailsMap.get(r.hostname) || [],
  }));

  const totalFailures = nodes.reduce((s, n) => s + n.failures, 0);
  const nodesWithMtbf = nodes.filter((n) => n.mtbf_hours !== null && n.mtbf_hours > 0);
  const avgMtbf = nodesWithMtbf.length > 0
    ? Math.round(nodesWithMtbf.reduce((s, n) => s + (n.mtbf_hours ?? 0), 0) / nodesWithMtbf.length)
    : 0;

  // Weekly trend: avg MTBF per week
  const trendSql = `
    WITH relevant_actions AS (
      SELECT hostname, timestamp, action, reason
      FROM ltp_sdk.node_actions
      WHERE timestamp >= $1::timestamptz
        AND timestamp < $2::timestamptz
        ${catFilter}
    ),
    all_failures AS (
      SELECT hostname, timestamp AS fail_time,
        (SELECT MIN(timestamp) FROM relevant_actions a2
         WHERE a2.hostname = a.hostname
           AND a2.action = 'validating-available'
           AND a2.timestamp > a.timestamp) AS recovery_time
      FROM relevant_actions a
      WHERE a.action = 'available-cordoned'
        ${reasonFilter}
        ${nodeSearch}
    ),
    hw_failures AS (
      SELECT af.*,
        EXISTS(
          SELECT 1 FROM relevant_actions aa
          WHERE aa.hostname = af.hostname
            AND aa.timestamp > af.fail_time
            AND (af.recovery_time IS NULL OR aa.timestamp < af.recovery_time)
            AND aa.action IN ('deallocated_ua-ua', 'ua-ready_ua')
        ) AS is_hardware
      FROM all_failures af
    ),
    hw_only AS (
      SELECT hostname, fail_time, recovery_time
      FROM hw_failures
      WHERE ${hwFilter}
    ),
    mtbf_calc AS (
      SELECT ho.hostname, ho.fail_time,
        COALESCE(
          (SELECT MAX(a.timestamp) FROM relevant_actions a
           WHERE a.hostname = ho.hostname
             AND a.action = 'validating-available'
             AND a.timestamp < ho.fail_time
          ),
          (SELECT MAX(a.timestamp) FROM relevant_actions a
           WHERE a.hostname = ho.hostname
             AND a.action = 'available-cordoned'
             AND a.timestamp < ho.fail_time
          ),
          (SELECT MIN(a.timestamp) FROM relevant_actions a
           WHERE a.hostname = ho.hostname
             AND a.action = 'new-validating'
          )
        ) AS uptime_start
      FROM hw_only ho
    )
    SELECT
      week_start,
      week_end,
      week_date,
      ROUND(avg_mtbf_hours::numeric, 1) AS avg_mtbf_hours,
      ROUND((avg_mtbf_hours / 24)::numeric, 1) AS avg_mtbf_days,
      node_count,
      failure_count,
      ROUND(
        SUM(avg_mtbf_hours * failure_count) OVER (ORDER BY week_date)
        / NULLIF(SUM(failure_count) OVER (ORDER BY week_date), 0)
      , 1) AS cumulative_mtbf_hours,
      ROUND(
        (SUM(avg_mtbf_hours * failure_count) OVER (ORDER BY week_date)
        / NULLIF(SUM(failure_count) OVER (ORDER BY week_date), 0)) / 24
      , 1) AS cumulative_mtbf_days
    FROM (
      SELECT
        TO_CHAR(DATE_TRUNC('week', fail_time), 'YYYY-MM-DD') AS week_start,
        TO_CHAR(DATE_TRUNC('week', fail_time) + INTERVAL '6 days', 'YYYY-MM-DD') AS week_end,
        DATE_TRUNC('week', fail_time) AS week_date,
        AVG(EXTRACT(EPOCH FROM (fail_time - uptime_start)) / 3600) AS avg_mtbf_hours,
        COUNT(DISTINCT hostname) AS node_count,
        COUNT(*) AS failure_count
      FROM mtbf_calc
      WHERE uptime_start IS NOT NULL
        AND EXTRACT(EPOCH FROM (fail_time - uptime_start)) / 3600 >= 0
      GROUP BY week_start, week_end, week_date
    ) weekly
    WHERE week_date >= $3::timestamptz
    ORDER BY week_date`;

  const trendRows = await queryLtp<any>(trendSql, [lookbackDate, endDateBuffer, from]);
  const trend: MtbfTrendRow[] = trendRows.map((r) => ({
    week: `${r.week_start}~${r.week_end}`,
    avg_mtbf_hours: parseFloat(r.avg_mtbf_hours) || 0,
    avg_mtbf_days: parseFloat(r.avg_mtbf_days) || 0,
    cumulative_mtbf_hours: parseFloat(r.cumulative_mtbf_hours) || 0,
    cumulative_mtbf_days: parseFloat(r.cumulative_mtbf_days) || 0,
    node_count: parseInt(r.node_count) || 0,
    failure_count: parseInt(r.failure_count) || 0,
  }));

  // Need node category — fetch separately
  const catSql = `SELECT DISTINCT ON (hostname) hostname, category FROM ltp_sdk.physical_node_onboard_records ORDER BY hostname, timestamp DESC`;
  const catRows = await queryLtp<{ hostname: string; category: string }>(catSql);
  const catMap = new Map(catRows.map(r => [r.hostname, r.category]));

  // Fill in categories
  for (const n of nodes) {
    n.category = catMap.get(n.hostname) || "unknown";
  }

  return {
    summary: {
      total_nodes: nodes.length,
      total_failures: totalFailures,
      avg_mtbf_hours: avgMtbf,
      avg_mtbf_days: Math.round((avgMtbf / 24) * 10) / 10,
    },
    nodes,
    trend,
  };
}
// ─── Job Metrics (Duration + MTBI) ────────────────────────────────────

export interface JobDurationRow {
  exit_category: string;
  avg_hours: number;
  max_hours: number;
  count: number;
}

export interface JobMtbiRow {
  rank: number;
  job_hash: string;
  job_name: string;
  virtual_cluster: string;
  total_jobs: number;
  hw_failure_jobs: number;
  all_failure_jobs: number;
  hw_failure_rate: number;
  mtbi_hours: number;
}

export interface JobMetricsSummary {
  total_jobs: number;
  failure_rate: number;
  avg_mtbi_hours: number;
}

export interface JobMtbiTrendRow {
  week: string;
  avg_mtbi_hours: number;
  hw_failure_count: number;
  total_runtime_hours: number;
}

export interface JobMetricsResponse {
  summary: JobMetricsSummary;
  job_duration: JobDurationRow[];
  job_mtbi: JobMtbiRow[];
  job_mtbi_trend: JobMtbiTrendRow[];
  virtual_clusters: string[];
}

export async function getJobMetricsData(
  from: string,
  to: string,
  vc?: string,
  failureType?: string
): Promise<JobMetricsResponse> {
  const vcFilter = vc && vc !== "all"
    ? `AND virtual_cluster = '${vc.replace(/[^a-z0-9_]/gi, "")}'`
    : "";

  // Map failure type filter to exit_category values
  const failureTypeMap: Record<string, string[]> = {
    hardware: ["'Hardware Failure'"],
    software: ["'Software Failure'"],
    user_stop: ["'User Stop'"],
    all: ["'Hardware Failure'", "'Software Failure'", "'User Stop'"],
  };
  const failureCategories = failureTypeMap[failureType || "all"] || failureTypeMap.all;
  const failureTypeFilter = `AND exit_category IN (${failureCategories.join(", ")})`;

  // ── Job Duration: avg/max by exit_category ──
  const durationSql = `
    SELECT 
      exit_category,
      ROUND(AVG(job_duration_hours)::numeric, 2) AS avg_hours,
      ROUND(MAX(job_duration_hours)::numeric, 2) AS max_hours,
      COUNT(*) AS count
    FROM ltp_sdk.job_summary
    WHERE time_generated >= $1::timestamptz
      AND time_generated < $2::timestamptz
      AND exit_category IS NOT NULL
      AND exit_category != ''
      ${vcFilter}
    GROUP BY exit_category
    ORDER BY count DESC`;

  const durationRows = await queryLtp<any>(durationSql, [from, to]);

  const jobDuration: JobDurationRow[] = durationRows.map((r) => ({
    exit_category: r.exit_category,
    avg_hours: parseFloat(r.avg_hours) || 0,
    max_hours: parseFloat(r.max_hours) || 0,
    count: parseInt(r.count) || 0,
  }));

  // ── Job MTBI: per job_hash ──
  // MTBI = total time span / number of failure incidents
  // Failure = exit_category in (Hardware Failure, Software Failure, User Stop)
  // MTBI per job_hash: total runtime hours of ALL jobs / count of hardware failure jobs
  // "How many GPU-hours of runtime between hardware failures"
  const mtbiSql = `
    WITH job_stats AS (
      SELECT 
        job_hash,
        STRING_AGG(DISTINCT job_name, ', ' ORDER BY job_name) AS job_names,
        MAX(virtual_cluster) AS virtual_cluster,
        COUNT(*) AS total_jobs,
        COUNT(*) FILTER (WHERE exit_category = 'Hardware Failure') AS hw_failure_jobs,
        COUNT(*) FILTER (WHERE exit_category IN ('Hardware Failure', 'Software Failure', 'User Stop')) AS all_failure_jobs,
        SUM(job_duration_hours) AS total_runtime_hours
      FROM ltp_sdk.job_summary
      WHERE time_generated >= $1::timestamptz
        AND time_generated < $2::timestamptz
        AND job_hash IS NOT NULL
        AND job_hash != ''
        AND job_hash != 'unknown'
        ${vcFilter}
      GROUP BY job_hash
      HAVING COUNT(*) FILTER (WHERE exit_category = 'Hardware Failure') > 0
    )
    SELECT 
      job_hash,
      job_names,
      virtual_cluster,
      total_jobs,
      hw_failure_jobs,
      all_failure_jobs,
      ROUND(100.0 * hw_failure_jobs / NULLIF(total_jobs, 0), 1) AS hw_failure_rate,
      CASE WHEN hw_failure_jobs > 0 
        THEN ROUND((total_runtime_hours / hw_failure_jobs)::numeric, 1)
        ELSE NULL END AS mtbi_hours
    FROM job_stats
    ORDER BY hw_failure_jobs DESC, mtbi_hours ASC NULLS LAST
    LIMIT 100`;

  const mtbiRows = await queryLtp<any>(mtbiSql, [from, to]);

  const jobMtbi: JobMtbiRow[] = mtbiRows.map((r, i) => ({
    rank: i + 1,
    job_hash: r.job_hash,
    job_name: r.job_names || r.job_hash,
    virtual_cluster: r.virtual_cluster || "\u2014",
    total_jobs: parseInt(r.total_jobs) || 0,
    hw_failure_jobs: parseInt(r.hw_failure_jobs) || 0,
    all_failure_jobs: parseInt(r.all_failure_jobs) || 0,
    hw_failure_rate: parseFloat(r.hw_failure_rate) || 0,
    mtbi_hours: r.mtbi_hours ? parseFloat(r.mtbi_hours) : null as any,
  }));

  // ── Weekly MTBI trend ──
  const trendSql = `
    SELECT 
      TO_CHAR(DATE_TRUNC('week', time_generated), 'YYYY-MM-DD') AS week_start,
      TO_CHAR(DATE_TRUNC('week', time_generated) + INTERVAL '6 days', 'YYYY-MM-DD') AS week_end,
      SUM(job_duration_hours) AS total_runtime_hours,
      COUNT(*) FILTER (WHERE exit_category = 'Hardware Failure') AS hw_failure_count,
      CASE WHEN COUNT(*) FILTER (WHERE exit_category = 'Hardware Failure') > 0
        THEN ROUND((SUM(job_duration_hours) / COUNT(*) FILTER (WHERE exit_category = 'Hardware Failure'))::numeric, 1)
        ELSE NULL END AS avg_mtbi_hours
    FROM ltp_sdk.job_summary
    WHERE time_generated >= $1::timestamptz
      AND time_generated < $2::timestamptz
      ${vcFilter}
    GROUP BY week_start, week_end
    ORDER BY week_start`;

  const trendRows = await queryLtp<any>(trendSql, [from, to]);
  const jobMtbiTrend: JobMtbiTrendRow[] = trendRows.map((r) => ({
    week: `${r.week_start}~${r.week_end}`,
    avg_mtbi_hours: parseFloat(r.avg_mtbi_hours) || 0,
    hw_failure_count: parseInt(r.hw_failure_count) || 0,
    total_runtime_hours: Math.round(parseFloat(r.total_runtime_hours) || 0),
  }));

  // ── Virtual clusters list (for filter dropdown) ──
  const vcSql = `
    SELECT DISTINCT virtual_cluster 
    FROM ltp_sdk.job_summary 
    WHERE time_generated >= $1::timestamptz 
      AND time_generated < $2::timestamptz
      AND virtual_cluster IS NOT NULL
      AND virtual_cluster != ''
    ORDER BY virtual_cluster`;

  const vcRows = await queryLtp<{ virtual_cluster: string }>(vcSql, [from, to]);
  const virtualClusters = vcRows.map((r) => r.virtual_cluster);

  // ── Summary ──
  const simpleSummarySql = `
    SELECT 
      COUNT(*) AS total_jobs,
      COUNT(*) FILTER (WHERE exit_category = 'Hardware Failure') AS hw_failures,
      ROUND(100.0 * COUNT(*) FILTER (WHERE exit_category = 'Hardware Failure') / NULLIF(COUNT(*), 0), 2) AS hw_failure_rate,
      ROUND(100.0 * COUNT(*) FILTER (WHERE exit_category IN ('Hardware Failure', 'Software Failure', 'User Stop')) / NULLIF(COUNT(*), 0), 2) AS all_failure_rate,
      ROUND(SUM(job_duration_hours)::numeric, 1) AS total_runtime_hours
    FROM ltp_sdk.job_summary
    WHERE time_generated >= $1::timestamptz
      AND time_generated < $2::timestamptz
      ${vcFilter}`;

  const summaryRows = await queryLtp<any>(simpleSummarySql, [from, to]);
  const s = summaryRows[0] || {};

  // Summary avg MTBI = total_runtime_hours / total_hw_failures (from ALL jobs, including unknown hash)
  const totalRuntime = parseFloat(s.total_runtime_hours) || 0;
  const totalHwFailures = parseInt(s.hw_failures) || 0;
  const avgMtbi = totalHwFailures > 0 ? Math.round(totalRuntime / totalHwFailures) : 0;

  return {
    summary: {
      total_jobs: parseInt(s.total_jobs) || 0,
      failure_rate: parseFloat(s.all_failure_rate) || 0,
      avg_mtbi_hours: avgMtbi,
    },
    job_duration: jobDuration,
    job_mtbi: jobMtbi,
    job_mtbi_trend: jobMtbiTrend,
    virtual_clusters: virtualClusters,
  };
}
