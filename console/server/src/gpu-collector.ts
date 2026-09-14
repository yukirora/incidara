/**
 * GPU Utilization collector — queries Prometheus every 5 min, stores snapshots in PostgreSQL
 *
 * Metrics collected per (sku, virtual_cluster):
 *   - total_gpus:    resourcesTotal (from virtual_cluster_stat)
 *   - used_gpus:     count(task_gpu_percent) = active + idle (from job-exporter, same source)
 *   - active_gpus:   count(task_gpu_percent > 0)
 *   - idle_gpus:     count(task_gpu_percent == 0)
 *   - sum_gpu_util:  sum(task_gpu_percent) / 100
 *   - avg_gpu_util:  avg(task_gpu_percent > 0)
 *   - unhealthy_gpus: count(unhealthy nodes) * 8
 */
import type pg from "pg";

const PROMETHEUS_URL = process.env.PROMETHEUS_URL || "http://192.0.2.10/prometheus";
const COLLECT_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
const RETENTION_DAYS = 365;
const BACKFILL_DAYS = 30;
const STEP_SECONDS = 300; // 5 min

interface PromResult {
  metric: Record<string, string>;
  value: [number, string];
}

interface PromRangeResult {
  metric: Record<string, string>;
  values: [number, string][];
}

interface VcSnapshot {
  timestamp: Date;
  sku: string;
  virtual_cluster: string;
  total_gpus: number;
  used_gpus: number;
  active_gpus: number;
  idle_gpus: number;
  sum_gpu_util: number;
  avg_gpu_util: number;
  unhealthy_gpus: number;
}

async function queryPrometheus(query: string): Promise<PromResult[]> {
  const url = `${PROMETHEUS_URL}/api/v1/query?query=${encodeURIComponent(query)}`;
  const resp = await fetch(url, { signal: AbortSignal.timeout(15000) });
  if (!resp.ok) throw new Error(`Prometheus error: ${resp.status}`);
  const json = await resp.json() as { data?: { result?: PromResult[] } };
  return json.data?.result ?? [];
}

async function queryPrometheusRange(query: string, start: number, end: number, step: number): Promise<PromRangeResult[]> {
  const url = `${PROMETHEUS_URL}/api/v1/query_range?query=${encodeURIComponent(query)}&start=${start}&end=${end}&step=${step}`;
  const resp = await fetch(url, { signal: AbortSignal.timeout(60000) });
  if (!resp.ok) throw new Error(`Prometheus range error: ${resp.status}`);
  const json = await resp.json() as { data?: { result?: PromRangeResult[] } };
  return json.data?.result ?? [];
}

/**
 * Collect a single snapshot (current point in time) from Prometheus.
 * Used for ongoing 5-min collection.
 */
export async function collectGpuSnapshot(pool: pg.Pool): Promise<number> {
  const totalRes = await queryPrometheus('sum by (vc_stat, sku)(virtual_cluster_stat{metric="resourcesTotal"})');
  const usedRes = await queryPrometheus('count by (virtual_cluster)(task_gpu_percent)');
  const activeRes = await queryPrometheus('count by (virtual_cluster)(task_gpu_percent>0)');
  const idleRes = await queryPrometheus('count by (virtual_cluster)(task_gpu_percent==0)');
  const sumUtilRes = await queryPrometheus('sum by (virtual_cluster)(task_gpu_percent)');
  const avgUtilRes = await queryPrometheus('avg by (virtual_cluster)(task_gpu_percent>0)');
  const unhealthyRes = await queryPrometheus(
    'count by (virtual_cluster)((pai_node_count{node_name!~"aks-.*",unschedulable!="false"} or pai_node_count{node_name!~"aks-.*",ready!="true"} or pai_node_count{node_name!~"aks-.*",disk_pressure!="false"} or pai_node_count{node_name!~"aks-.*",memory_pressure!="false"})) * 8'
  ).catch(() => [] as PromResult[]);

  const snapshots = buildSnapshots(new Date(), totalRes, usedRes, activeRes, idleRes, sumUtilRes, avgUtilRes, unhealthyRes);
  if (snapshots.length === 0) return 0;

  await insertSnapshots(pool, snapshots);
  return snapshots.length;
}

/**
 * Backfill historical data from Prometheus (1 day at a time).
 * Called on first startup if the DB table is empty.
 */
export async function backfillHistory(pool: pg.Pool): Promise<number> {
  // Check if we already have data
  const { rows } = await pool.query("SELECT COUNT(*) AS cnt FROM gpu_utilization_snapshots");
  if (parseInt(rows[0]?.cnt) > 0) {
    console.log("[gpu-collector] DB already has data, skipping backfill");
    return 0;
  }

  console.log(`[gpu-collector] Backfilling ${BACKFILL_DAYS} days from Prometheus...`);
  let totalInserted = 0;

  const now = Math.floor(Date.now() / 1000);
  const startTs = now - BACKFILL_DAYS * 86400;

  // Process 1 day at a time to avoid Prometheus timeouts
  for (let dayStart = startTs; dayStart < now; dayStart += 86400) {
    const dayEnd = Math.min(dayStart + 86400, now);

    try {
      const [totalRes, usedRes, activeRes, idleRes, sumUtilRes, avgUtilRes] = await Promise.all([
        queryPrometheusRange('sum by (vc_stat, sku)(virtual_cluster_stat{metric="resourcesTotal"})', dayStart, dayEnd, STEP_SECONDS),
        queryPrometheusRange('count by (virtual_cluster)(task_gpu_percent)', dayStart, dayEnd, STEP_SECONDS),
        queryPrometheusRange('count by (virtual_cluster)(task_gpu_percent>0)', dayStart, dayEnd, STEP_SECONDS),
        queryPrometheusRange('count by (virtual_cluster)(task_gpu_percent==0)', dayStart, dayEnd, STEP_SECONDS),
        queryPrometheusRange('sum by (virtual_cluster)(task_gpu_percent)', dayStart, dayEnd, STEP_SECONDS),
        queryPrometheusRange('avg by (virtual_cluster)(task_gpu_percent>0)', dayStart, dayEnd, STEP_SECONDS),
      ]);

      // For each timestamp in the range, build snapshots
      const snapshots = buildSnapshotsFromRange(totalRes, usedRes, activeRes, idleRes, sumUtilRes, avgUtilRes);
      if (snapshots.length > 0) {
        await insertSnapshots(pool, snapshots);
        totalInserted += snapshots.length;
      }

      const dayStr = new Date(dayStart * 1000).toISOString().slice(0, 10);
      console.log(`[gpu-collector] Backfilled ${dayStr}: ${snapshots.length} snapshots`);
    } catch (err) {
      const dayStr = new Date(dayStart * 1000).toISOString().slice(0, 10);
      console.error(`[gpu-collector] Backfill ${dayStr} failed:`, err instanceof Error ? err.message : String(err));
      // Continue with next day
    }
  }

  console.log(`[gpu-collector] Backfill complete: ${totalInserted} total snapshots`);
  return totalInserted;
}

/**
 * Build snapshot objects from instant query results.
 */
function buildSnapshots(
  ts: Date,
  totalRes: PromResult[],
  usedRes: PromResult[],
  activeRes: PromResult[],
  idleRes: PromResult[],
  sumUtilRes: PromResult[],
  avgUtilRes: PromResult[],
  unhealthyRes: PromResult[] = [],
): VcSnapshot[] {
  const totalMap = new Map<string, { vc: string; sku: string; total: number }>();
  for (const r of totalRes) {
    const vc = r.metric.vc_stat || r.metric.virtual_cluster || "";
    const sku = r.metric.sku || "";
    const key = `${vc}|${sku}`;
    totalMap.set(key, { vc, sku, total: parseInt(r.value[1]) || 0 });
  }

  // usedRes now comes from count(task_gpu_percent) grouped by virtual_cluster
  const usedMap = new Map<string, number>();
  for (const r of usedRes) {
    const vc = r.metric.virtual_cluster || r.metric.vc_stat || "";
    usedMap.set(vc, (usedMap.get(vc) ?? 0) + (parseInt(r.value[1]) || 0));
  }

  const activeMap = new Map<string, number>();
  for (const r of activeRes) activeMap.set(r.metric.virtual_cluster || "", parseInt(r.value[1]) || 0);
  const idleMap = new Map<string, number>();
  for (const r of idleRes) idleMap.set(r.metric.virtual_cluster || "", parseInt(r.value[1]) || 0);
  const sumUtilMap = new Map<string, number>();
  for (const r of sumUtilRes) sumUtilMap.set(r.metric.virtual_cluster || "", parseFloat(r.value[1]) / 100 || 0);
  const avgUtilMap = new Map<string, number>();
  for (const r of avgUtilRes) avgUtilMap.set(r.metric.virtual_cluster || "", parseFloat(r.value[1]) || 0);
  const unhealthyMap = new Map<string, number>();
  for (const r of unhealthyRes) unhealthyMap.set(r.metric.virtual_cluster || "", parseInt(r.value[1]) || 0);

  // Collect ALL known VCs from both total (scheduler) and used (job-exporter)
  const allVcs = new Map<string, { vc: string; sku: string; total: number }>();
  for (const [key, val] of totalMap) {
    allVcs.set(val.vc, val);
  }
  // Add VCs that only appear in job-exporter (no totalMap entry)
  for (const vc of usedMap.keys()) {
    if (!allVcs.has(vc) && vc) {
      allVcs.set(vc, { vc, sku: "unknown", total: 0 });
    }
  }

  const snapshots: VcSnapshot[] = [];
  for (const [, { vc, sku, total }] of allVcs) {
    if (!vc) continue;
    const used = usedMap.get(vc) ?? 0;
    let active = activeMap.get(vc) ?? 0;
    let idle = idleMap.get(vc) ?? 0;
    let sumUtil = sumUtilMap.get(vc) ?? 0;
    let avgUtil = avgUtilMap.get(vc) ?? 0;

    // Sanity bounds
    if (active < 0) active = 0;
    if (idle < 0) idle = 0;
    if (avgUtil > 100) avgUtil = 100;
    if (avgUtil < 0) avgUtil = 0;
    if (sumUtil > active) sumUtil = active;
    if (sumUtil < 0) sumUtil = 0;

    snapshots.push({
      timestamp: ts,
      sku: sku || "unknown",
      virtual_cluster: vc,
      total_gpus: total,
      used_gpus: used,
      active_gpus: active,
      idle_gpus: idle,
      sum_gpu_util: sumUtil,
      avg_gpu_util: avgUtil,
      unhealthy_gpus: unhealthyMap.get(vc) ?? 0,
    });
  }
  return snapshots;
}

/**
 * Build snapshots from range query results (multiple timestamps per series).
 */
function buildSnapshotsFromRange(
  totalRes: PromRangeResult[],
  usedRes: PromRangeResult[],
  activeRes: PromRangeResult[],
  idleRes: PromRangeResult[],
  sumUtilRes: PromRangeResult[],
  avgUtilRes: PromRangeResult[],
): VcSnapshot[] {
  // Collect all unique timestamps
  const allTimestamps = new Set<number>();
  for (const r of totalRes) for (const [t] of r.values) allTimestamps.add(t);

  // For each timestamp, build a snapshot set
  const snapshots: VcSnapshot[] = [];

  for (const ts of Array.from(allTimestamps).sort()) {
    const timestamp = new Date(ts * 1000);

    // Build per-VC maps for this timestamp
    const totalMap = new Map<string, { vc: string; sku: string; total: number }>();
    for (const r of totalRes) {
      const vc = r.metric.vc_stat || r.metric.virtual_cluster || "";
      const sku = r.metric.sku || "";
      const val = r.values.find(([t]) => t === ts);
      if (val) totalMap.set(`${vc}|${sku}`, { vc, sku, total: parseInt(val[1]) || 0 });
    }

    // usedMap: now from count(task_gpu_percent), keyed by virtual_cluster
    const usedMap = new Map<string, number>();
    for (const r of usedRes) {
      const vc = r.metric.virtual_cluster || r.metric.vc_stat || "";
      const val = r.values.find(([t]) => t === ts);
      if (val) usedMap.set(vc, (usedMap.get(vc) ?? 0) + (parseInt(val[1]) || 0));
    }

    const activeMap = new Map<string, number>();
    for (const r of activeRes) {
      const val = r.values.find(([t]) => t === ts);
      if (val) activeMap.set(r.metric.virtual_cluster || "", parseInt(val[1]) || 0);
    }
    const idleMap = new Map<string, number>();
    for (const r of idleRes) {
      const val = r.values.find(([t]) => t === ts);
      if (val) idleMap.set(r.metric.virtual_cluster || "", parseInt(val[1]) || 0);
    }
    const sumUtilMap = new Map<string, number>();
    for (const r of sumUtilRes) {
      const val = r.values.find(([t]) => t === ts);
      if (val) sumUtilMap.set(r.metric.virtual_cluster || "", parseFloat(val[1]) / 100 || 0);
    }
    const avgUtilMap = new Map<string, number>();
    for (const r of avgUtilRes) {
      const val = r.values.find(([t]) => t === ts);
      if (val) avgUtilMap.set(r.metric.virtual_cluster || "", parseFloat(val[1]) || 0);
    }

    // Collect ALL VCs from both totalMap and usedMap (job-exporter)
    const allVcs = new Map<string, { vc: string; sku: string; total: number }>();
    for (const [, val] of totalMap) allVcs.set(val.vc, val);
    for (const vc of usedMap.keys()) {
      if (!allVcs.has(vc) && vc) allVcs.set(vc, { vc, sku: "unknown", total: 0 });
    }

    for (const [, { vc, sku, total }] of allVcs) {
      if (!vc) continue;
      const used = usedMap.get(vc) ?? 0;
      let active = activeMap.get(vc) ?? 0;
      let idle = idleMap.get(vc) ?? 0;
      let sumUtil = sumUtilMap.get(vc) ?? 0;
      let avgUtil = avgUtilMap.get(vc) ?? 0;

      // Sanity bounds
      if (active < 0) active = 0;
      if (idle < 0) idle = 0;
      if (avgUtil > 100) avgUtil = 100;
      if (avgUtil < 0) avgUtil = 0;
      if (sumUtil > active) sumUtil = active;
      if (sumUtil < 0) sumUtil = 0;

      snapshots.push({
        timestamp,
        sku: sku || "unknown",
        virtual_cluster: vc,
        total_gpus: total,
        used_gpus: used,
        active_gpus: active,
        idle_gpus: idle,
        sum_gpu_util: sumUtil,
        avg_gpu_util: avgUtil,
        unhealthy_gpus: 0,
      });
    }
  }

  return snapshots;
}

/**
 * Insert snapshot rows into PostgreSQL.
 */
async function insertSnapshots(pool: pg.Pool, snapshots: VcSnapshot[]): Promise<void> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    for (const s of snapshots) {
      await client.query(
        `INSERT INTO gpu_utilization_snapshots
          (timestamp, sku, virtual_cluster, total_gpus, used_gpus, active_gpus, idle_gpus, sum_gpu_util, avg_gpu_util, unhealthy_gpus)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
        [s.timestamp, s.sku, s.virtual_cluster, s.total_gpus, s.used_gpus, s.active_gpus, s.idle_gpus, s.sum_gpu_util, s.avg_gpu_util, s.unhealthy_gpus]
      );
    }
    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}

/**
 * Start the collector: backfill history on first run, then collect every 5 min.
 */
export function startGpuCollector(pool: pg.Pool): () => void {
  // Periodic collection
  const timer = setInterval(async () => {
    try {
      const count = await collectGpuSnapshot(pool);
      if (count > 0) console.log(`[gpu-collector] Collected ${count} VC snapshots`);
    } catch (err) {
      console.error("[gpu-collector] Error:", err instanceof Error ? err.message : String(err));
    }
  }, COLLECT_INTERVAL_MS);

  // On startup: backfill history (if empty), then collect once
  setTimeout(async () => {
    try {
      await backfillHistory(pool);
      const count = await collectGpuSnapshot(pool);
      console.log(`[gpu-collector] Initial collection: ${count} VC snapshots`);
    } catch (err) {
      console.error("[gpu-collector] Startup error:", err instanceof Error ? err.message : String(err));
    }
    // Cleanup old data once
    try {
      await pool.query(`DELETE FROM gpu_utilization_snapshots WHERE timestamp < now() - INTERVAL '${RETENTION_DAYS} days'`);
    } catch { /* ignore */ }
  }, 30_000);

  return () => clearInterval(timer);
}
