import { useEffect, useState } from "react";
import { useMe } from "../hooks/useMe";
import { fetchJobMetrics, type JobMetricsResponse } from "../api/reports";

function fmt(n: number) {
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

const EXIT_COLORS: Record<string, string> = {
  "Hardware Failure": "#f87171",
  "Software Failure": "#fbbf24",
  "User Stop": "#60a5fa",
  "Succeed": "#34d399",
  "Unknown": "#a1a1aa",
  "Platform Failure": "#c084fc",
};

export function JobMetrics() {
  const { is_admin } = useMe();

  const today = new Date().toISOString().slice(0, 10);
  const twoWeeksAgo = new Date(Date.now() - 13 * 86400000).toISOString().slice(0, 10);

  const [from, setFrom] = useState(twoWeeksAgo);
  const [to, setTo] = useState(today);
  const [vc, setVc] = useState("all");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<JobMetricsResponse | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetchJobMetrics({ from, to, vc });
      setData(res);
    } catch (e: any) {
      console.error("Job metrics fetch error:", e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [from, to, vc]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!is_admin) return <div className="p-8 text-zinc-400">Admin access required</div>;
  if (!data) return <div className="p-8 text-zinc-400">{loading ? "Loading..." : "No data"}</div>;

  const { summary, job_duration, job_mtbi, job_mtbi_trend, virtual_clusters } = data;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-zinc-900">Job Metrics</h1>
        <div className="flex items-center gap-2 text-xs text-zinc-600">
          <label>
            From <input type="date" className="ml-1 border rounded px-2 py-1 text-xs" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label>
            To <input type="date" className="ml-1 border rounded px-2 py-1 text-xs" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          <select className="border rounded px-2 py-1 text-xs" value={vc} onChange={(e) => setVc(e.target.value)}>
            <option value="all">All VCs</option>
            {virtual_clusters.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-xs text-zinc-500 mb-1">Total Jobs</div>
          <div className="text-2xl font-semibold text-zinc-900">{fmt(summary.total_jobs)}</div>
        </div>
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-xs text-zinc-500 mb-1">All Failure Rate</div>
          <div className="text-2xl font-semibold text-zinc-900">{summary.failure_rate}%</div>
        </div>
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-xs text-zinc-500 mb-1">Avg MTBI (GPU-hours)</div>
          <div className="text-2xl font-semibold text-zinc-900">{fmt(summary.avg_mtbi_hours)}h</div>
        </div>
      </div>

      {/* Job Duration by Exit Category */}
      <div className="bg-white rounded-xl border border-zinc-200 p-4">
        <div className="flex justify-between items-center mb-2">
          <span className="text-sm font-semibold text-zinc-800">Job Duration by Exit Category</span>
          <span className="text-xs text-zinc-400">{job_duration.reduce((s, r) => s + r.count, 0)} jobs total</span>
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-zinc-500 border-b">
              <th className="py-1.5 pr-2">Exit Category</th>
              <th className="py-1.5 pr-2 text-right">Count</th>
              <th className="py-1.5 pr-2 text-right">Avg Duration (h)</th>
              <th className="py-1.5 text-right">Max Duration (h)</th>
            </tr>
          </thead>
          <tbody>
            {job_duration.map((r) => (
              <tr key={r.exit_category} className="border-b border-zinc-50">
                <td className="py-1.5 pr-2">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full" style={{ background: EXIT_COLORS[r.exit_category] ?? "#a1a1aa" }} />
                    {r.exit_category}
                  </span>
                </td>
                <td className="py-1.5 pr-2 text-right text-zinc-700">{fmt(r.count)}</td>
                <td className="py-1.5 pr-2 text-right text-zinc-700">{fmt(r.avg_hours)}</td>
                <td className="py-1.5 text-right text-zinc-500">{fmt(r.max_hours)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Weekly MTBI Trend */}
      {job_mtbi_trend.length > 0 && (
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-sm font-semibold text-zinc-800 mb-2">Weekly MTBI Trend (GPU-hours per HW failure)</div>
          <div className="flex items-end gap-2 h-32 border-b border-l border-zinc-200 pl-2 pb-1">
            {(() => {
              const chartH = 110;
              const maxVal = Math.max(...job_mtbi_trend.map(t => Math.max(t.avg_mtbi_hours, 1)), 1);
              return job_mtbi_trend.map((t, i) => {
                const h = Math.max(4, (t.avg_mtbi_hours / maxVal) * chartH);
                const barColor = t.avg_mtbi_hours < 100 ? "#f87171" : t.avg_mtbi_hours < 500 ? "#fbbf24" : "#34d399";
                return (
                  <div key={i} className="flex-1 flex flex-col items-center justify-end group relative h-full" style={{ minWidth: 40 }}>
                    <div className="absolute -top-10 opacity-0 group-hover:opacity-100 transition-opacity bg-zinc-800 text-white text-[10px] rounded px-2 py-1 whitespace-nowrap z-10 pointer-events-none">
                      {fmt(t.avg_mtbi_hours)}h · {t.hw_failure_count} HW fails · {fmt(t.total_runtime_hours)}h runtime
                    </div>
                    <div className="w-full rounded-t transition-all" style={{ height: `${h}px`, background: barColor }} />
                    <div className="text-[8px] text-zinc-400 mt-0.5 whitespace-nowrap">{t.week}</div>
                  </div>
                );
              });
            })()}
          </div>
        </div>
      )}

      {/* Job MTBI Rank */}
      <div className="bg-white rounded-xl border border-zinc-200 p-4">
        <div className="flex justify-between items-center mb-2">
          <span className="text-sm font-semibold text-zinc-800">Job MTBI Rank (by hardware failures)</span>
          <span className="text-xs text-zinc-400">{job_mtbi.length} jobs with HW failures</span>
        </div>
        <div className="max-h-[400px] overflow-y-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b sticky top-0 bg-white">
                <th className="py-1.5 pr-2">#</th>
                <th className="py-1.5 pr-2">Job Name</th>
                <th className="py-1.5 pr-2">VC</th>
                <th className="py-1.5 pr-2 text-right">Total Jobs</th>
                <th className="py-1.5 pr-2 text-right">HW Fails</th>
                <th className="py-1.5 pr-2 text-right">HW Fail Rate</th>
                <th className="py-1.5 text-right">MTBI (h)</th>
              </tr>
            </thead>
            <tbody>
              {job_mtbi.map((j) => (
                <tr key={j.job_hash} className="border-b border-zinc-50">
                  <td className="py-1.5 pr-2 text-zinc-400">{j.rank}</td>
                  <td className="py-1.5 pr-2 font-mono text-[11px] text-zinc-800 truncate max-w-[280px]">{j.job_name}</td>
                  <td className="py-1.5 pr-2 text-zinc-500">{j.virtual_cluster}</td>
                  <td className="py-1.5 pr-2 text-right text-zinc-700">{fmt(j.total_jobs)}</td>
                  <td className="py-1.5 pr-2 text-right text-red-600 font-medium">{j.hw_failure_jobs}</td>
                  <td className="py-1.5 pr-2 text-right text-zinc-500">{j.hw_failure_rate}%</td>
                  <td className="py-1.5 text-right font-medium text-zinc-800">{j.mtbi_hours !== null ? fmt(j.mtbi_hours) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
