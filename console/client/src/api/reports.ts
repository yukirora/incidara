import { api } from "../lib/api";

export interface DailyAvailabilityRow {
  day: string;
  allocatable: number;
  allocated: number;
  validating: number;
  cordon: number;
  ofr: number;
  deallocated?: number;
  unallocatable?: number;
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

export interface AvailabilityResponse {
  daily: DailyAvailabilityRow[];
  current: CurrentSnapshotRow[];
  inventory: InventoryRow[];
  rma: RmaSummary;
}

export async function fetchAvailability(params: {
  from: string;
  to: string;
  category?: string;
}): Promise<AvailabilityResponse> {
  const qs = new URLSearchParams({
    from: params.from,
    to: params.to,
    ...(params.category && params.category !== "all"
      ? { category: params.category }
      : {}),
  });
  return api<AvailabilityResponse>("GET", `/api/reports/availability?${qs}`);
}

// ─── Reliability ────────────────────────────────────────────────────

export interface ReliabilityKpi {
  total_failures: number;
  avg_per_day: number;
  avg_cordon_fix_days: number;
  alert_level: string;
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
  category: string;
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

export async function fetchReliability(params: {
  from: string;
  to: string;
  category?: string;
}): Promise<ReliabilityResponse> {
  const qs = new URLSearchParams({
    from: params.from,
    to: params.to,
    ...(params.category && params.category !== "all"
      ? { category: params.category }
      : {}),
  });
  return api<ReliabilityResponse>("GET", `/api/reports/reliability?${qs}`);
}

// ─── Node MTBF ──────────────────────────────────────────────────────

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

export interface MtbfResponse {
  summary: MtbfSummary;
  nodes: MtbfNodeRow[];
  trend: MtbfTrendRow[];
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

export async function fetchMtbf(params: {
  from: string;
  to: string;
  category?: string;
  reason_search?: string;
  node_search?: string;
  mtbf_type?: string;
}): Promise<MtbfResponse> {
  const qs = new URLSearchParams({
    from: params.from,
    to: params.to,
    ...(params.category && params.category !== "all"
      ? { category: params.category }
      : {}),
    ...(params.reason_search ? { reason_search: params.reason_search } : {}),
    ...(params.node_search ? { node_search: params.node_search } : {}),
    ...(params.mtbf_type && params.mtbf_type !== "hardware"
      ? { mtbf_type: params.mtbf_type }
      : {}),
  });
  return api<MtbfResponse>("GET", `/api/reports/reliability/mtbf?${qs}`);
}

// ─── Job Metrics ────────────────────────────────────────────────────

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
  mtbi_hours: number | null;
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

export async function fetchJobMetrics(params: {
  from: string;
  to: string;
  vc?: string;
}): Promise<JobMetricsResponse> {
  const qs = new URLSearchParams({
    from: params.from,
    to: params.to,
    ...(params.vc && params.vc !== "all" ? { vc: params.vc } : {}),
  });
  return api<JobMetricsResponse>("GET", `/api/reports/job-metrics?${qs}`);
}
