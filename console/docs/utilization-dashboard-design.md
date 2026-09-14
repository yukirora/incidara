# Utilization Dashboard — Final Design

## Overview

A real-time + historical GPU utilization dashboard that shows how cluster GPU resources are being used — how many GPUs are active, idle, allocated but unused, and their utilization rates — broken down by SKU (h200/b300/cpu) and virtual cluster.

## Problem

- **Prometheus range queries are slow**: `sum_over_time()` and `count_over_time()` take 30-60s for 4-week ranges
- **Data retention**: Prometheus retains data for ~30 days, not enough for long-term analysis
- **Query complexity**: The Grafana dashboards use nested subqueries with `count_over_time` that timeout on large ranges

## Solution: Hybrid Architecture

### Two data sources, two purposes

| Purpose | Source | Why |
|---------|--------|-----|
| **Live KPIs + Per-VC table** | Prometheus instant query | Shows "right now" state, <2s |
| **Historical charts (1 day, 1 week, 1 month)** | PostgreSQL snapshots | Prometheus can't do long-range `sum_over_time` fast |
| **Summary GPU-hours (any date range)** | PostgreSQL snapshots | Just SUM of stored rows |

When user selects a date range (e.g., June 16-23):

- **KPI cards**: Always show **live** data from Prometheus (current snapshot, not historical)
  - This is what the user sees right now, regardless of date filter
  - Like a speedometer — always shows current speed
- **Charts + Summary**: Use **PostgreSQL** snapshots for the selected date range
  - `SELECT FROM gpu_utilization_snapshots WHERE timestamp BETWEEN from AND to`
  - Daily aggregation: `GROUP BY date_trunc('day', timestamp)`
  - This is <100ms regardless of range

### The collector just saves snapshots — it doesn't compute

The collector's only job is: every 5 min, query Prometheus for point-in-time values, store them. All aggregation (daily/weekly/monthly sums) happens at query time in PostgreSQL.

## Database Schema

```sql
CREATE TABLE gpu_utilization_snapshots (
  id                  SERIAL PRIMARY KEY,
  timestamp           TIMESTAMPTZ NOT NULL DEFAULT now(),
  sku                 VARCHAR(32) NOT NULL,          -- h200, b300, cpu, cpu16c, ctrl, storage
  virtual_cluster     VARCHAR(64) NOT NULL,           -- h200agentic, h200pretraining, cpu2, etc.
  total_gpus          INTEGER,                        -- resourcesTotal: total GPUs in VC
  used_gpus           INTEGER,                        -- resourcesUsed: GPUs assigned to jobs
  active_gpus         INTEGER,                        -- count(task_gpu_percent > 0): GPUs doing compute
  idle_gpus           INTEGER,                        -- count(task_gpu_percent == 0): assigned but idle
  sum_gpu_util        FLOAT,                          -- sum(task_gpu_percent) / 100: utilized GPU-equivalents
  avg_gpu_util        FLOAT,                          -- avg(task_gpu_percent > 0): average utilization %
  unhealthy_gpus      INTEGER                         -- count(unhealthy nodes) × 8: unhealthy GPU count
);

CREATE INDEX idx_gpu_snap_ts  ON gpu_utilization_snapshots (timestamp);
CREATE INDEX idx_gpu_snap_vc  ON gpu_utilization_snapshots (virtual_cluster, timestamp);
CREATE INDEX idx_gpu_snap_sku ON gpu_utilization_snapshots (sku, timestamp);
```

### Field mapping to original Grafana

| Our field | Prometheus query | Grafana equivalent |
|-----------|-----------------|-------------------|
| `total_gpus` | `sum by (vc)(virtual_cluster_stat{metric="resourcesTotal"})` | `sum(resourcesTotal)` |
| `used_gpus` | `sum by (vc)(virtual_cluster_stat{metric="resourcesUsed"})` | `count(task_gpu_percent)` (≈) |
| `active_gpus` | `count by (vc)(task_gpu_percent > 0)` | `count(task_gpu_percent > 0)` |
| `idle_gpus` | `count by (vc)(task_gpu_percent == 0)` | `count(task_gpu_percent == 0)` |
| `sum_gpu_util` | `sum by (vc)(task_gpu_percent) / 100` | `sum(task_gpu_percent) / 100` |
| `avg_gpu_util` | `avg by (vc)(task_gpu_percent > 0)` | (not in original, bonus) |
| `unhealthy_gpus` | `count by (vc)(unhealthy nodes) × 8` | `count(unhealthy_nodes) * 8` |

## How PostgreSQL computes Grafana-equivalent metrics

Each snapshot row is a **5-minute point-in-time sample**. To compute "GPU-hours" for any time range, you multiply the point-in-time GPU count by the interval (5/60 hours) and sum:

```
GPU-hours = SUM(gpu_count_at_each_snapshot × 5/60)
```

This is the **rectangle rule** (Riemann sum) — the same way Prometheus `count_over_time` works internally, just pre-materialized.

### Example: h200agentic for 1 week (June 16-23)

PostgreSQL has 2016 rows for this VC (7 days × 288 snapshots/day):

```
timestamp              | total | used | active | idle | sum_gpu_util
2026-06-16 00:00:00    | 1072  | 1020 | 625    | 395  | 587.5
2026-06-16 00:05:00    | 1072  | 1020 | 630    | 390  | 590.2
2026-06-16 00:10:00    | 1072  | 1024 | 628    | 396  | 588.8
...2013 more rows...
2026-06-22 23:55:00    | 1072  | 1008 | 612    | 396  | 565.3
```

### Computing each Grafana metric:

```sql
-- 1. Total GPU-Hours (= count_over_time(pai_node_count) * 8 in Grafana)
SELECT SUM(total_gpus * 5.0/60) AS total_gpu_hours
FROM gpu_utilization_snapshots
WHERE sku = 'h200' AND timestamp >= '2026-06-16' AND timestamp < '2026-06-23';
-- Result: 1072 × 24 × 7 = 180,096 GPU-hours

-- 2. Allocated GPU-Hours (= count_over_time(task_gpu_percent) in Grafana)
SELECT SUM(used_gpus * 5.0/60) AS allocated_gpu_hours
FROM gpu_utilization_snapshots
WHERE sku = 'h200' AND timestamp >= '2026-06-16' AND timestamp < '2026-06-23';
-- Result: ~1020 × 168 ≈ 171,360 GPU-hours

-- 3. Utilized GPU-Hours (= sum_over_time(task_gpu_percent) / 100 in Grafana)
--    This is the KEY metric — uses sum_gpu_util directly
SELECT SUM(sum_gpu_util * 5.0/60) AS utilized_gpu_hours
FROM gpu_utilization_snapshots
WHERE sku = 'h200' AND timestamp >= '2026-06-16' AND timestamp < '2026-06-23';
-- Result: ~580 × 168 ≈ 97,440 GPU-hours

-- 4. Idle GPU-Hours (= count_over_time(task_gpu_percent == 0) in Grafana)
SELECT SUM(idle_gpus * 5.0/60) AS idle_gpu_hours
FROM gpu_utilization_snapshots
WHERE sku = 'h200' AND timestamp >= '2026-06-16' AND timestamp < '2026-06-23';
-- Result: ~395 × 168 ≈ 66,360 GPU-hours

-- 5. Non-used GPU-Hours (= resourcesGuaranteed - count(task_gpu_percent))
SELECT SUM((total_gpus - used_gpus) * 5.0/60) AS non_used_gpu_hours
FROM gpu_utilization_snapshots
WHERE sku = 'h200' AND timestamp >= '2026-06-16' AND timestamp < '2026-06-23';
-- Result: ~52 × 168 ≈ 8,736 GPU-hours

-- 6. Unhealthy GPU-Hours (= count_over_time(unhealthy_nodes) * 8)
SELECT SUM(unhealthy_gpus * 5.0/60) AS unhealthy_gpu_hours
FROM gpu_utilization_snapshots
WHERE sku = 'h200' AND timestamp >= '2026-06-16' AND timestamp < '2026-06-23';
```

### Daily breakdown for charts:

```sql
SELECT
  date_trunc('day', timestamp) AS day,
  SUM(total_gpus * 5.0/60)     AS total_gpu_hours,
  SUM(used_gpus * 5.0/60)     AS allocated_gpu_hours,
  SUM(sum_gpu_util * 5.0/60)  AS utilized_gpu_hours,
  SUM(idle_gpus * 5.0/60)     AS idle_gpu_hours,
  AVG(avg_gpu_util)            AS avg_utilization
FROM gpu_utilization_snapshots
WHERE sku = 'h200' AND timestamp >= '2026-06-16' AND timestamp < '2026-06-23'
GROUP BY day ORDER BY day;
```

### Accuracy vs Grafana/Prometheus

The rectangle rule has ~1% error compared to exact Prometheus `sum_over_time` — because we sample every 5 min instead of every 30s. The error comes from changes that happen between snapshots, but since GPU counts change gradually (not instantaneously), the error averages out.

- 5-min snapshots capture most changes (Prometheus scrapes every ~30s)
- GPU-hours error: <1% vs exact Prometheus range query
- All 6 Grafana metrics reproducible from snapshots

## Collector (every 5 minutes)

1. Query Prometheus: `sum by (vc_stat, sku)(virtual_cluster_stat{metric="resourcesTotal"})`
2. Query Prometheus: `sum by (vc_stat, sku)(virtual_cluster_stat{metric="resourcesUsed"})`
3. Query Prometheus: `count by (virtual_cluster)(task_gpu_percent > 0)`
4. Query Prometheus: `count by (virtual_cluster)(task_gpu_percent == 0)`
5. Query Prometheus: `sum by (virtual_cluster)(task_gpu_percent) / 100`
6. Query Prometheus: `avg by (virtual_cluster)(task_gpu_percent > 0)`
7. Query Prometheus: `count by (virtual_cluster)(unhealthy nodes) * 8`
8. Join results by VC, INSERT into PostgreSQL

### Accuracy analysis for 5-min intervals

| Interval | Snapshots/day | Misses | Accuracy | DB rows/day (~20 VCs) |
|----------|--------------|--------|-----------|------------------------|
| 5 min | 288 | Jobs <5min | High (<1% error) | ~5,760 |
| 10 min | 144 | Jobs <10min | Good (~2% error) | ~2,880 |
| 15 min | 96 | Jobs <15min | Fair (~3% error) | ~1,920 |
| 30 min | 48 | Jobs <30min | Poor (~5% error) | ~960 |

**5-minute chosen**:
- Captures most GPU count changes (data changes 286 times in 289 5-min points)
- <1% error for daily GPU-hours
- DB size: ~5,760 rows/day × 365 = ~2.1M rows/year (manageable)

## Query Performance

| Query | Source | Speed |
|-------|--------|-------|
| KPI cards (live) | Prometheus instant | <2s |
| Per-VC table (live) | Prometheus instant | <2s |
| 1-day chart | PostgreSQL | <50ms |
| 1-week chart | PostgreSQL | <100ms |
| 1-month chart | PostgreSQL | <200ms |
| 1-year chart | PostgreSQL | <500ms |

All PostgreSQL queries are simple `SUM() ... GROUP BY date_trunc(...)` — no complex subqueries, no joins. Fast at any range.

## What if there's no data yet (collector just started)?

The charts would be empty for the selected range. But the live KPIs still work from Prometheus. After the collector runs for a day, the charts start filling in.

## UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Utilization                                                    │
│  [From: 2026-06-16] [To: 2026-06-23] [SKU: h200 ▼]            │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Total    │  │ Active   │  │ Idle     │  │ Avg Util │       │
│  │ 10,224   │  │ 8,280   │  │ 2,297    │  │ 92.5%    │       │
│  │ GPUs     │  │ GPUs     │  │ GPUs     │  │          │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│  ● LIVE (from Prometheus instant query)                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐     │
│  │ GPU Allocation Trend (daily GPU-hours from DB)         │     │
│  │                                                        │     │
│  │   ██ ██ ██ ██ ██ ██ ██  ← Utilized (green)            │     │
│  │   ░░ ░░ ░░ ░░ ░░ ░░ ░░  ← Idle (amber)                │     │
│  │   ▓▓ ▓▓ ▓▓ ▓▓ ▓▓ ▓▓ ▓▓  ← Unallocated (gray)         │     │
│  │                                                        │     │
│  │   06/16  06/17  06/18  06/19  06/20  06/21  06/22    │     │
│  └───────────────────────────────────────────────────────┘     │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐     │
│  │ Avg GPU Utilization % (daily line chart from DB)        │     │
│  │                                                        │     │
│  │     ●─────●─────●─────●─────●─────●─────●             │     │
│  │   90% ──────────────────────────────────               │     │
│  │   80% ──────────────────────────────────               │     │
│  │                                                        │     │
│  │   06/16  06/17  06/18  06/19  06/20  06/21  06/22    │     │
│  └───────────────────────────────────────────────────────┘     │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐     │
│  │ Per Virtual Cluster Breakdown (LIVE from Prometheus)   │     │
│  │                                                        │     │
│  │ VC                │ Total │ Active │ Idle │ Avg Util  │     │
│  │ h200pretraining   │ 4248  │ 3800   │ 448  │ 93.2%     │     │
│  │ h200pretraining2  │ 2128  │ 1900   │ 228  │ 91.5%     │     │
│  │ h200agentic       │ 1072  │ 1052   │ 20   │ 94.8%     │     │
│  │ ...                                                     │     │
│  └───────────────────────────────────────────────────────┘     │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐     │
│  │ GPU-Hours Summary (from DB for selected date range)    │     │
│  │                                                        │     │
│  │ Total GPU-hours:     1,702,464                         │     │
│  │ Utilized GPU-hours:  1,402,033 (82.4%)                 │     │
│  │ Idle GPU-hours:      200,831 (11.8%)                   │     │
│  │ Unallocated:         99,600 (5.8%)                     │     │
│  │                                                        │     │
│  │   ██ Utilized  ░░ Idle  ▓▓ Unallocated                 │     │
│  └───────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

## API Design

### Real-time (Prometheus proxy)

```
GET /api/reports/utilization/live?sku=h200
  → {
      total_gpus: 10224,
      used_gpus: 10200,
      active_gpus: 8280,
      idle_gpus: 2297,
      avg_utilization: 92.5,
      unhealthy_gpus: 0,
      vcs: [
        { vc: "h200pretraining", total: 4248, active: 3800, idle: 448, avg_util: 93.2, unhealthy: 0 },
        { vc: "h200agentic", total: 1072, active: 1052, idle: 20, avg_util: 94.8, unhealthy: 0 },
        ...
      ]
    }
```

### Historical (PostgreSQL)

```
GET /api/reports/utilization/history?from=2026-06-16&to=2026-06-23&sku=h200
  → {
      daily: [
        {
          day: "2026-06-16",
          total_gpu_hours: 243216,
          allocated_gpu_hours: 243216,
          utilized_gpu_hours: 201200,
          idle_gpu_hours: 32016,
          unhealthy_gpu_hours: 0,
          avg_utilization: 91.2
        },
        ...
      ],
      summary: {
        total_gpu_hours: 1702464,
        allocated_gpu_hours: 1702464,
        utilized_gpu_hours: 1402033,
        idle_gpu_hours: 200831,
        unhealthy_gpu_hours: 0
      }
    }
```

## DB Size

- ~20 VCs × 288 snapshots/day = ~5,760 rows/day
- ~2.1M rows/year
- Auto-delete after 1 year (retention policy in collector)

## Data Flow

```
Prometheus (192.0.2.10:9090)
    │
    │ (every 5 min — collector)
    ▼
PostgreSQL (gpu_utilization_snapshots)
    │
    │ SELECT (historical — fast)
    ▼
Utilization Dashboard
    │
    │ + Prometheus instant query (live KPIs + per-VC table)
    ▼
User sees: real-time KPIs + historical trends from DB
```
