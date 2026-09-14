/**
 * GPU Utilization store — queries PostgreSQL for historical snapshots
 * and Prometheus for live data
 */
import type pg from "pg";

const PROMETHEUS_URL = process.env.PROMETHEUS_URL || "http://192.0.2.10/prometheus";

// GPU-hours are computed using the ACTUAL time gap between consecutive snapshots
// (via LEAD window function), not a fixed 5-min assumption.
// This is accurate even when snapshots are missing or irregular.
const GPU_HOURS_CTE = `
  WITH intervals AS (
    SELECT *,
      EXTRACT(EPOCH FROM (
        LEAD(timestamp) OVER (PARTITION BY sku, virtual_cluster ORDER BY timestamp) - timestamp
      )) / 3600.0 AS interval_hours
    FROM gpu_utilization_snapshots
  )
`;

export interface UtilizationLiveVc {
  vc: string;
  sku: string;
  total: number;
  used: number;
  active: number;
  idle: number;
  avg_util: number;
}

export interface UtilizationLive {
  total_gpus: number;
  used_gpus: number;
  active_gpus: number;
  idle_gpus: number;
  avg_utilization: number;
  unhealthy_gpus: number;
  vcs: UtilizationLiveVc[];
}

export interface UtilizationDailyRow {
  day: string;
  total_gpu_hours: number;
  allocated_gpu_hours: number;
  active_gpu_hours: number;
  utilized_gpu_hours: number;
  idle_gpu_hours: number;
  non_used_gpu_hours: number;
  unhealthy_gpu_hours: number;
  avg_utilization: number;
}

export interface UtilizationSummary {
  total_gpu_hours: number;
  allocated_gpu_hours: number;
  active_gpu_hours: number;
  utilized_gpu_hours: number;
  idle_gpu_hours: number;
  non_used_gpu_hours: number;
  unhealthy_gpu_hours: number;
}

export interface UtilizationHistoryResponse {
  daily: UtilizationDailyRow[];
  summary: UtilizationSummary;
}

async function queryPrometheus(query: string): Promise<any[]> {
  const url = `${PROMETHEUS_URL}/api/v1/query?query=${encodeURIComponent(query)}`;
  const resp = await fetch(url, { signal: AbortSignal.timeout(15000) });
  if (!resp.ok) throw new Error(`Prometheus ${resp.status}`);
  const json = await resp.json() as { data?: { result?: any[] } };
  return json.data?.result ?? [];
}

export async function getLiveUtilization(sku?: string): Promise<UtilizationLive> {
  const skuFilter = sku && sku !== "all" ? `,sku="${sku}"` : "";

  const [totalRes, usedRes, activeRes, idleRes, avgRes, unhealthyRes] = await Promise.all([
    queryPrometheus(`sum by (vc_stat, sku)(virtual_cluster_stat{metric="resourcesTotal"${skuFilter}})`),
    queryPrometheus(`sum by (vc_stat, sku)(virtual_cluster_stat{metric="resourcesUsed"${skuFilter}})`),
    queryPrometheus(`count by (virtual_cluster)(task_gpu_percent>0)`),
    queryPrometheus(`count by (virtual_cluster)(task_gpu_percent==0)`),
    queryPrometheus(`avg by (virtual_cluster)(task_gpu_percent>0)`),
    queryPrometheus(`count((pai_node_count{node_name!~"aks-.*",unschedulable!="false"} or pai_node_count{node_name!~"aks-.*",ready!="true"})) * 8`).catch(() => []),
  ]);

  // Build per-VC maps
  const vcMap = new Map<string, UtilizationLiveVc>();

  for (const r of totalRes) {
    const vc = r.metric.vc_stat || "";
    const vcSku = r.metric.sku || "";
    if (!vc) continue;
    if (!vcMap.has(vc)) vcMap.set(vc, { vc, sku: vcSku, total: 0, used: 0, active: 0, idle: 0, avg_util: 0 });
    vcMap.get(vc)!.total += parseInt(r.value[1]) || 0;
    vcMap.get(vc)!.sku = vcSku;
  }
  for (const r of usedRes) {
    const vc = r.metric.vc_stat || "";
    if (vcMap.has(vc)) vcMap.get(vc)!.used += parseInt(r.value[1]) || 0;
  }
  for (const r of activeRes) {
    const vc = r.metric.virtual_cluster || "";
    if (vcMap.has(vc)) vcMap.get(vc)!.active = parseInt(r.value[1]) || 0;
  }
  for (const r of idleRes) {
    const vc = r.metric.virtual_cluster || "";
    if (vcMap.has(vc)) vcMap.get(vc)!.idle = parseInt(r.value[1]) || 0;
  }
  for (const r of avgRes) {
    const vc = r.metric.virtual_cluster || "";
    if (vcMap.has(vc)) vcMap.get(vc)!.avg_util = parseFloat(r.value[1]) || 0;
  }

  const vcs = Array.from(vcMap.values()).sort((a, b) => b.total - a.total);

  const totalGpus = vcs.reduce((s, v) => s + v.total, 0);
  const usedGpus = vcs.reduce((s, v) => s + v.used, 0);
  const activeGpus = vcs.reduce((s, v) => s + v.active, 0);
  const idleGpus = vcs.reduce((s, v) => s + v.idle, 0);
  const avgUtil = activeGpus > 0
    ? vcs.reduce((s, v) => s + v.avg_util * v.active, 0) / activeGpus
    : 0;
  const unhealthy = unhealthyRes[0] ? parseInt(unhealthyRes[0].value[1]) : 0;

  return {
    total_gpus: totalGpus,
    used_gpus: usedGpus,
    active_gpus: activeGpus,
    idle_gpus: idleGpus,
    avg_utilization: Math.round(avgUtil * 10) / 10,
    unhealthy_gpus: unhealthy,
    vcs,
  };
}

export async function getHistoryUtilization(
  pool: pg.Pool,
  from: string,
  to: string,
  sku?: string
): Promise<UtilizationHistoryResponse> {
  const skuFilter = sku && sku !== "all" ? `AND sku = '${sku.replace(/[^a-z0-9_]/gi, "")}'` : "";

  const dailySql = `
    ${GPU_HOURS_CTE}
    SELECT
      TO_CHAR(date_trunc('day', timestamp), 'YYYY-MM-DD') AS day,
      ROUND(SUM(total_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS total_gpu_hours,
      ROUND(SUM(used_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS allocated_gpu_hours,
      ROUND(SUM(active_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS active_gpu_hours,
      ROUND(SUM(sum_gpu_util * COALESCE(interval_hours, 0))::numeric, 1) AS utilized_gpu_hours,
      ROUND(SUM(idle_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS idle_gpu_hours,
      ROUND(SUM((total_gpus - used_gpus) * COALESCE(interval_hours, 0))::numeric, 1) AS non_used_gpu_hours,
      ROUND(SUM(COALESCE(unhealthy_gpus, 0) * COALESCE(interval_hours, 0))::numeric, 1) AS unhealthy_gpu_hours,
      ROUND(AVG(avg_gpu_util)::numeric, 1) AS avg_utilization
    FROM intervals
    WHERE timestamp >= $1::timestamptz
      AND timestamp < $2::timestamptz
      AND interval_hours IS NOT NULL
      AND interval_hours < 1  -- cap at 1 hour to ignore gaps
      ${skuFilter}
    GROUP BY day
    ORDER BY day`;

  const { rows: dailyRows } = await pool.query(dailySql, [from, to]);

  const daily: UtilizationDailyRow[] = dailyRows.map((r: any) => ({
    day: r.day,
    total_gpu_hours: parseFloat(r.total_gpu_hours) || 0,
    allocated_gpu_hours: parseFloat(r.allocated_gpu_hours) || 0,
    active_gpu_hours: parseFloat(r.active_gpu_hours) || 0,
    utilized_gpu_hours: parseFloat(r.utilized_gpu_hours) || 0,
    idle_gpu_hours: parseFloat(r.idle_gpu_hours) || 0,
    non_used_gpu_hours: parseFloat(r.non_used_gpu_hours) || 0,
    unhealthy_gpu_hours: parseFloat(r.unhealthy_gpu_hours) || 0,
    avg_utilization: parseFloat(r.avg_utilization) || 0,
  }));

  const summarySql = `
    ${GPU_HOURS_CTE}
    SELECT
      ROUND(SUM(total_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS total_gpu_hours,
      ROUND(SUM(used_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS allocated_gpu_hours,
      ROUND(SUM(active_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS active_gpu_hours,
      ROUND(SUM(sum_gpu_util * COALESCE(interval_hours, 0))::numeric, 1) AS utilized_gpu_hours,
      ROUND(SUM(idle_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS idle_gpu_hours,
      ROUND(SUM((total_gpus - used_gpus) * COALESCE(interval_hours, 0))::numeric, 1) AS non_used_gpu_hours,
      ROUND(SUM(COALESCE(unhealthy_gpus, 0) * COALESCE(interval_hours, 0))::numeric, 1) AS unhealthy_gpu_hours
    FROM intervals
    WHERE timestamp >= $1::timestamptz
      AND timestamp < $2::timestamptz
      AND interval_hours IS NOT NULL
      AND interval_hours < 1
      ${skuFilter}`;

  const { rows: summaryRows } = await pool.query(summarySql, [from, to]);
  const s = summaryRows[0] || {};

  return {
    daily,
    summary: {
      total_gpu_hours: parseFloat(s.total_gpu_hours) || 0,
      allocated_gpu_hours: parseFloat(s.allocated_gpu_hours) || 0,
      active_gpu_hours: parseFloat(s.active_gpu_hours) || 0,
      utilized_gpu_hours: parseFloat(s.utilized_gpu_hours) || 0,
      idle_gpu_hours: parseFloat(s.idle_gpu_hours) || 0,
      non_used_gpu_hours: parseFloat(s.non_used_gpu_hours) || 0,
      unhealthy_gpu_hours: parseFloat(s.unhealthy_gpu_hours) || 0,
    },
  };
}

export interface VcSummaryRow {
  virtual_cluster: string;
  total_gpu_hours: number;
  allocated_gpu_hours: number;
  active_gpu_hours: number;
  utilized_gpu_hours: number;
  idle_gpu_hours: number;
  non_used_gpu_hours: number;
  avg_utilization: number;
}

export async function getVcUtilizationSummary(
  pool: pg.Pool,
  from: string,
  to: string,
  sku?: string
): Promise<VcSummaryRow[]> {
  const skuFilter = sku && sku !== "all" ? `AND sku = '${sku.replace(/[^a-z0-9_]/gi, "")}'` : "";

  const sql = `
    ${GPU_HOURS_CTE}
    SELECT
      virtual_cluster,
      ROUND(SUM(total_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS total_gpu_hours,
      ROUND(SUM(used_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS allocated_gpu_hours,
      ROUND(SUM(active_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS active_gpu_hours,
      ROUND(SUM(sum_gpu_util * COALESCE(interval_hours, 0))::numeric, 1) AS utilized_gpu_hours,
      ROUND(SUM(idle_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS idle_gpu_hours,
      ROUND(SUM((total_gpus - used_gpus) * COALESCE(interval_hours, 0))::numeric, 1) AS non_used_gpu_hours,
      ROUND(AVG(avg_gpu_util)::numeric, 1) AS avg_utilization
    FROM intervals
    WHERE timestamp >= $1::timestamptz
      AND timestamp < $2::timestamptz
      AND interval_hours IS NOT NULL
      AND interval_hours < 1
      ${skuFilter}
    GROUP BY virtual_cluster
    ORDER BY total_gpu_hours DESC`;

  const { rows } = await pool.query(sql, [from, to]);
  return rows.map((r: any) => ({
    virtual_cluster: r.virtual_cluster,
    total_gpu_hours: parseFloat(r.total_gpu_hours) || 0,
    allocated_gpu_hours: parseFloat(r.allocated_gpu_hours) || 0,
    active_gpu_hours: parseFloat(r.active_gpu_hours) || 0,
    utilized_gpu_hours: parseFloat(r.utilized_gpu_hours) || 0,
    idle_gpu_hours: parseFloat(r.idle_gpu_hours) || 0,
    non_used_gpu_hours: parseFloat(r.non_used_gpu_hours) || 0,
    avg_utilization: parseFloat(r.avg_utilization) || 0,
  }));
}

export interface VcDailyRow {
  virtual_cluster: string;
  day: string;
  total_gpu_hours: number;
  allocated_gpu_hours: number;
  active_gpu_hours: number;
  utilized_gpu_hours: number;
  idle_gpu_hours: number;
  non_used_gpu_hours: number;
  avg_utilization: number;
}

export async function getVcDailyBreakdown(
  pool: pg.Pool,
  from: string,
  to: string,
  sku?: string
): Promise<VcDailyRow[]> {
  const skuFilter = sku && sku !== "all" ? `AND sku = '${sku.replace(/[^a-z0-9_]/gi, "")}'` : "";

  const sql = `
    ${GPU_HOURS_CTE}
    SELECT
      virtual_cluster,
      timestamp::date AS day,
      ROUND(SUM(total_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS total_gpu_hours,
      ROUND(SUM(used_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS allocated_gpu_hours,
      ROUND(SUM(active_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS active_gpu_hours,
      ROUND(SUM(sum_gpu_util * COALESCE(interval_hours, 0))::numeric, 1) AS utilized_gpu_hours,
      ROUND(SUM(idle_gpus * COALESCE(interval_hours, 0))::numeric, 1) AS idle_gpu_hours,
      ROUND(SUM((total_gpus - used_gpus) * COALESCE(interval_hours, 0))::numeric, 1) AS non_used_gpu_hours,
      ROUND(AVG(avg_gpu_util)::numeric, 1) AS avg_utilization
    FROM intervals
    WHERE timestamp >= $1::timestamptz
      AND timestamp < $2::timestamptz
      AND interval_hours IS NOT NULL
      AND interval_hours < 1
      ${skuFilter}
    GROUP BY virtual_cluster, timestamp::date
    ORDER BY virtual_cluster, day`;

  const { rows } = await pool.query(sql, [from, to]);
  return rows.map((r: any) => ({
    virtual_cluster: r.virtual_cluster,
    day: r.day?.toISOString?.().slice(0, 10) || String(r.day).slice(0, 10),
    total_gpu_hours: parseFloat(r.total_gpu_hours) || 0,
    allocated_gpu_hours: parseFloat(r.allocated_gpu_hours) || 0,
    active_gpu_hours: parseFloat(r.active_gpu_hours) || 0,
    utilized_gpu_hours: parseFloat(r.utilized_gpu_hours) || 0,
    idle_gpu_hours: parseFloat(r.idle_gpu_hours) || 0,
    non_used_gpu_hours: parseFloat(r.non_used_gpu_hours) || 0,
    avg_utilization: parseFloat(r.avg_utilization) || 0,
  }));
}
