import { useEffect, useState } from "react";
import { useMe } from "../hooks/useMe";
import { api } from "../lib/api";

function fmt(n: number) { return n.toLocaleString(undefined, { maximumFractionDigits: 1 }); }

interface LiveVc { vc: string; sku: string; total: number; used: number; active: number; idle: number; avg_util: number; }
interface LiveData { total_gpus: number; used_gpus: number; active_gpus: number; idle_gpus: number; avg_utilization: number; unhealthy_gpus: number; vcs: LiveVc[]; }
interface DailyRow { day: string; total_gpu_hours: number; allocated_gpu_hours: number; active_gpu_hours: number; utilized_gpu_hours: number; idle_gpu_hours: number; non_used_gpu_hours: number; avg_utilization: number; }
interface Summary { total_gpu_hours: number; allocated_gpu_hours: number; active_gpu_hours: number; utilized_gpu_hours: number; idle_gpu_hours: number; non_used_gpu_hours: number; }
interface HistoryData { daily: DailyRow[]; summary: Summary; }

async function fetchLive(sku: string): Promise<LiveData> { return api("GET", `/api/reports/utilization/live?sku=${sku}`); }
async function fetchHistory(from: string, to: string, sku: string): Promise<HistoryData> { return api("GET", `/api/reports/utilization/history?from=${from}&to=${to}&sku=${sku}`); }
interface VcSummaryRow { virtual_cluster: string; total_gpu_hours: number; allocated_gpu_hours: number; active_gpu_hours: number; utilized_gpu_hours: number; idle_gpu_hours: number; non_used_gpu_hours: number; avg_utilization: number; }
async function fetchVcSummary(from: string, to: string, sku: string): Promise<VcSummaryRow[]> { const r = await api<{ vcs: VcSummaryRow[] }>("GET", `/api/reports/utilization/vc-summary?from=${from}&to=${to}&sku=${sku}`); return r.vcs; }
interface VcDailyRow { virtual_cluster: string; day: string; total_gpu_hours: number; allocated_gpu_hours: number; active_gpu_hours: number; utilized_gpu_hours: number; idle_gpu_hours: number; non_used_gpu_hours: number; avg_utilization: number; }
async function fetchVcDaily(from: string, to: string, sku: string): Promise<VcDailyRow[]> { const r = await api<{ daily: VcDailyRow[] }>("GET", `/api/reports/utilization/vc-daily?from=${from}&to=${to}&sku=${sku}`); return r.daily; }

export function Utilization() {
  const { is_admin } = useMe();
  const today = new Date().toISOString().slice(0, 10);
  const weekAgo = new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10);
  const [from, setFrom] = useState(weekAgo);
  const [to, setTo] = useState(today);
  const [sku, setSku] = useState("h200");
  const [live, setLive] = useState<LiveData | null>(null);
  const [history, setHistory] = useState<HistoryData | null>(null);
  const [vcSummary, setVcSummary] = useState<VcSummaryRow[]>([]);
  const [vcDaily, setVcDaily] = useState<VcDailyRow[]>([]);
  const [liveLoading, setLiveLoading] = useState(false);
  const [histLoading, setHistLoading] = useState(false);

  useEffect(() => { setLiveLoading(true); fetchLive(sku).then(setLive).catch(e => console.error(e.message)).finally(() => setLiveLoading(false)); }, [sku]);
  useEffect(() => {
    setHistLoading(true);
    Promise.all([fetchHistory(from, to, sku), fetchVcSummary(from, to, sku), fetchVcDaily(from, to, sku)])
      .then(([h, vc, vd]) => { setHistory(h); setVcSummary(vc); setVcDaily(vd); })
      .catch(e => console.error(e.message))
      .finally(() => setHistLoading(false));
  }, [from, to, sku]);

  if (!is_admin) return <div className="p-8 text-zinc-400">Admin access required</div>;

  return (
    <div className="p-6 space-y-4 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-zinc-900">Utilization</h1>
        <div className="flex items-center gap-2 text-xs text-zinc-600">
          <label>From <input type="date" className="ml-1 border rounded px-2 py-1 text-xs" value={from} onChange={e => setFrom(e.target.value)} /></label>
          <label>To <input type="date" className="ml-1 border rounded px-2 py-1 text-xs" value={to} onChange={e => setTo(e.target.value)} /></label>
          <select className="border rounded px-2 py-1 text-xs" value={sku} onChange={e => setSku(e.target.value)}>
            <option value="all">All SKUs</option><option value="h200">h200</option><option value="b300">b300</option><option value="cpu">cpu</option><option value="storage">storage</option>
          </select>
        </div>
      </div>

      {/* ─── 1. Live KPI Cards ─── */}
      {live && (<div>
        <div className="flex items-center gap-2 mb-2">
          <span className="inline-flex items-center gap-1 text-[10px] text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full font-semibold"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" /> LIVE</span>
          {liveLoading && <span className="text-xs text-zinc-400">Updating…</span>}
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <div className="bg-white rounded-xl border border-zinc-200 p-3"><div className="text-[11px] text-zinc-500 mb-1">Total GPUs</div><div className="text-xl font-bold">{fmt(live.total_gpus)}</div><div className="text-[10px] text-zinc-400">{sku === "all" ? "all SKUs" : sku}</div></div>
          <div className="bg-white rounded-xl border border-zinc-200 p-3"><div className="text-[11px] text-zinc-500 mb-1">Used</div><div className="text-xl font-bold text-blue-600">{fmt(live.used_gpus)}</div><div className="text-[10px] text-zinc-400">{Math.round(100 * live.used_gpus / Math.max(live.total_gpus, 1))}% of total</div></div>
          <div className="bg-white rounded-xl border border-zinc-200 p-3"><div className="text-[11px] text-zinc-500 mb-1">Active</div><div className="text-xl font-bold text-emerald-600">{fmt(live.active_gpus)}</div><div className="text-[10px] text-zinc-400">{Math.round(100 * live.active_gpus / Math.max(live.used_gpus, 1))}% of used</div></div>
          <div className="bg-white rounded-xl border border-zinc-200 p-3"><div className="text-[11px] text-zinc-500 mb-1">Idle</div><div className="text-xl font-bold text-amber-500">{fmt(live.idle_gpus)}</div><div className="text-[10px] text-zinc-400">{Math.round(100 * live.idle_gpus / Math.max(live.used_gpus, 1))}% of used</div></div>
          <div className="bg-white rounded-xl border border-zinc-200 p-3"><div className="text-[11px] text-zinc-500 mb-1">Avg Utilization</div><div className="text-xl font-bold text-emerald-600">{fmt(live.avg_utilization)}%</div><div className="text-[10px] text-zinc-400">active GPUs</div></div>
        </div>
      </div>)}

      {/* ─── 2. Per Virtual Cluster (LIVE current snapshot) ─── */}
      {live && live.vcs.length > 0 && (<div className="bg-white rounded-xl border border-zinc-200 p-4">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-sm font-semibold text-zinc-800">Per Virtual Cluster</h3>
          <span className="text-[10px] text-emerald-600 font-medium">● LIVE (current snapshot)</span>
        </div>
        <table className="w-full text-xs"><thead><tr className="text-left text-zinc-500 border-b">
          <th className="py-1.5 pr-2">Virtual Cluster</th><th className="py-1.5 pr-2 text-right">Total</th><th className="py-1.5 pr-2 text-right">Used</th><th className="py-1.5 pr-2 text-right">Active</th><th className="py-1.5 pr-2 text-right">Idle</th><th className="py-1.5">Avg Utilization</th>
        </tr></thead><tbody>
          {live.vcs.map(v => (<tr key={v.vc} className="border-b border-zinc-50">
            <td className="py-1.5 pr-2 font-medium text-zinc-800">{v.vc}</td>
            <td className="py-1.5 pr-2 text-right text-zinc-700">{fmt(v.total)}</td>
            <td className="py-1.5 pr-2 text-right text-blue-600">{fmt(v.used)}</td>
            <td className="py-1.5 pr-2 text-right text-emerald-600">{fmt(v.active)}</td>
            <td className="py-1.5 pr-2 text-right text-amber-500">{fmt(v.idle)}</td>
            <td className="py-1.5"><span className="mr-1">{fmt(v.avg_util)}%</span><span className="inline-block w-16 h-1.5 rounded bg-zinc-100 overflow-hidden align-middle"><span className="block h-full rounded" style={{ width: `${v.avg_util}%`, background: v.avg_util >= 80 ? "#22c55e" : v.avg_util >= 50 ? "#f59e0b" : "#ef4444" }} /></span></td>
          </tr>))}
        </tbody></table>
      </div>)}

      {/* ─── 3. GPU-Hours Summary (date range, 3-layer hierarchy) ─── */}
      {history && history.summary.total_gpu_hours > 0 && (() => {
        const s = history.summary;
        const total = s.total_gpu_hours, alloc = s.allocated_gpu_hours, nonUsed = s.non_used_gpu_hours;
        const active = s.active_gpu_hours, util = s.utilized_gpu_hours, idle = s.idle_gpu_hours;
        const nonEff = Math.max(0, active - util);
        const allocPct = Math.round(100 * alloc / total), nonUsedPct = 100 - allocPct;
        const activePct = alloc > 0 ? Math.round(100 * active / alloc) : 0, idlePct = alloc > 0 ? Math.round(100 * idle / alloc) : 0;
        const utilPct = active > 0 ? Math.round(100 * util / active) : 0, nonEffPct = active > 0 ? Math.round(100 * nonEff / active) : 0;
        return (<div className="bg-white rounded-xl border border-zinc-200 p-4">
          <h3 className="text-sm font-semibold text-zinc-800 mb-4">GPU-Hours Summary ({from} to {to})</h3>
          {/* Bar 1 */}
          <div className="mb-4">
            <div className="flex items-center justify-between mb-1"><span className="text-xs font-semibold text-zinc-700">Cluster Allocation</span><span className="text-xs text-zinc-400">Total: {fmt(total)} GPU-hours</span></div>
            <div className="flex items-center gap-4 mb-1.5">
              <div><span className="text-lg font-bold text-blue-600">{fmt(alloc)}</span> <span className="text-xs text-zinc-500">Allocated ({allocPct}%)</span></div>
              <div><span className="text-lg font-bold text-zinc-400">{fmt(nonUsed)}</span> <span className="text-xs text-zinc-500">Non-used ({nonUsedPct}%)</span></div>
            </div>
            <div className="h-5 rounded-full overflow-hidden flex">
              <div style={{ width: `${allocPct}%` }} className="bg-blue-500 flex items-center justify-center text-[9px] text-white font-semibold">Allocated {allocPct}%</div>
              <div style={{ width: `${nonUsedPct}%` }} className="bg-zinc-200 flex items-center justify-center text-[9px] text-zinc-500 font-semibold">{nonUsedPct}%</div>
            </div>
          </div>
          {/* Bar 2 */}
          <div className="ml-4 pl-4 border-l-2 border-blue-200 mb-3">
            <div className="flex items-center justify-between mb-1"><span className="text-xs font-semibold text-zinc-700">Within Allocated <span className="text-zinc-400 font-normal">— Active vs Idle</span></span><span className="text-xs text-zinc-400">{fmt(alloc)} = 100%</span></div>
            <div className="flex items-center gap-4 mb-1.5">
              <div><span className="text-sm font-bold text-indigo-600">{fmt(active)}</span> <span className="text-[10px] text-zinc-500">Active ({activePct}%)</span></div>
              <div><span className="text-sm font-bold text-amber-500">{fmt(idle)}</span> <span className="text-[10px] text-zinc-500">Idle ({idlePct}%)</span></div>
            </div>
            <div className="h-4 rounded-full overflow-hidden flex">
              <div style={{ width: `${activePct}%` }} className="bg-indigo-500 flex items-center justify-center text-[8px] text-white font-semibold">Active {activePct}%</div>
              <div style={{ width: `${idlePct}%` }} className="bg-amber-400 flex items-center justify-center text-[8px] text-white font-semibold">Idle {idlePct}%</div>
            </div>
            {/* Bar 3 */}
            <div className="ml-4 pl-4 border-l-2 border-indigo-200 mt-3">
              <div className="flex items-center justify-between mb-1"><span className="text-xs font-semibold text-zinc-700">Within Active <span className="text-zinc-400 font-normal">— GPU Efficiency</span></span><span className="text-xs text-zinc-400">{fmt(active)} = 100%</span></div>
              <div className="flex items-center gap-4 mb-1.5">
                <div><span className="text-sm font-bold text-emerald-600">{fmt(util)}</span> <span className="text-[10px] text-zinc-500">Effectively Used ({utilPct}%)</span></div>
                <div><span className="text-sm font-bold text-orange-400">{fmt(nonEff)}</span> <span className="text-[10px] text-zinc-500">Non-Effectively ({nonEffPct}%)</span></div>
              </div>
              <div className="h-3 rounded-full overflow-hidden flex">
                <div style={{ width: `${utilPct}%` }} className="bg-emerald-500 flex items-center justify-center text-[8px] text-white font-semibold">{utilPct}%</div>
                <div style={{ width: `${nonEffPct}%` }} className="bg-orange-300 flex items-center justify-center text-[8px] text-white font-semibold">{nonEffPct}%</div>
              </div>
            </div>
          </div>
        </div>);
      })()}

      {histLoading && <div className="text-sm text-zinc-400">Loading historical data…</div>}

      {/* ─── 4. Avg GPU Utilization % (daily line chart with scroll) ─── */}
      {history && history.daily.length > 0 && (<div className="bg-white rounded-xl border border-zinc-200 p-4">
        <h3 className="text-sm font-semibold text-zinc-800 mb-2">Avg GPU Utilization % (daily)</h3>
        <div className="overflow-x-auto pt-12">
        {(() => {
          const dayW = 50; // fixed width per day
          const n = history.daily.length;
          const W = Math.max(500, n * dayW + 50), H = 150, padL = 35, padR = 10, padT = 20, padB = 25;
          const plotW = W - padL - padR, plotH = H - padT - padB;
          const vals = history.daily.map(d => d.avg_utilization);
          const minV = Math.max(0, Math.floor(Math.min(...vals) / 10) * 10 - 10);
          const maxV = Math.min(100, Math.ceil(Math.max(...vals) / 10) * 10);
          const xAt = (i: number) => padL + (i / Math.max(n - 1, 1)) * plotW;
          const yAt = (v: number) => padT + plotH - ((v - minV) / (maxV - minV)) * plotH;
          return (<svg width={W} height={H} className="block" style={{ minWidth: W }}>
            <line x1={padL} y1={padT + plotH} x2={W - padR} y2={padT + plotH} stroke="#e4e4e7" />
            <line x1={padL} y1={padT} x2={W - padR} y2={padT} stroke="#f4f4f5" strokeDasharray="3 3" />
            <text x={padL - 4} y={padT + 4} fontSize="9" fill="#a1a1aa" textAnchor="end">{maxV}%</text>
            <text x={padL - 4} y={padT + plotH + 4} fontSize="9" fill="#a1a1aa" textAnchor="end">{minV}%</text>
            <polyline points={history.daily.map((d, i) => `${xAt(i)},${yAt(d.avg_utilization)}`).join(' ')} fill="none" stroke="#f97316" strokeWidth="2" />
            {history.daily.map((d, i) => (<g key={i}>
              <circle cx={xAt(i)} cy={yAt(d.avg_utilization)} r="3.5" fill="#f97316" stroke="white" strokeWidth="1.5">
                <title>{d.day}: {fmt(d.avg_utilization)}%</title>
              </circle>
              <text x={xAt(i)} y={yAt(d.avg_utilization) - 8} fontSize="9" fill="#f97316" textAnchor="middle" fontWeight="600">{fmt(d.avg_utilization)}%</text>
            </g>))}
            {history.daily.map((d, i) => (<text key={`x${i}`} x={xAt(i)} y={H - 5} fontSize="8" fill="#a1a1aa" textAnchor="middle">{d.day.slice(5)}</text>))}
          </svg>);
        })()}
        </div>
      </div>)}

      {/* ─── 5. GPU-Hours Breakdown (daily stacked bar with scroll + full tooltips) ─── */}
      {history && history.daily.length > 0 && (<div className="bg-white rounded-xl border border-zinc-200 p-4">
        <h3 className="text-sm font-semibold text-zinc-800 mb-2">GPU-Hours Breakdown (daily)</h3>
        <div className="flex items-center gap-4 mb-2 text-[10px] text-zinc-500">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-emerald-500" /> Effectively Used</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-orange-300" /> Non-Effectively</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-amber-400" /> Idle</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-zinc-200" /> Non-used</span>
        </div>
        <div className="overflow-x-auto pt-12">
        {(() => {
          const dayW = 44; // fixed width per bar
          const barAreaW = Math.max(400, history.daily.length * dayW);
          const maxH = Math.max(...history.daily.map(d => d.total_gpu_hours), 1);
          return (<div className="flex items-end gap-1 h-36 border-b border-l border-zinc-200 pl-1 pb-5 relative" style={{ width: barAreaW, minWidth: barAreaW }}>
            {history.daily.map((d, i) => {
              const scale = 100 / maxH;
              const utilH = d.utilized_gpu_hours * scale;
              const nonEffH = Math.max(0, d.active_gpu_hours - d.utilized_gpu_hours) * scale;
              const idleH = d.idle_gpu_hours * scale;
              const nonUsedH = d.non_used_gpu_hours * scale;
              const allocPct = Math.round(100 * d.allocated_gpu_hours / d.total_gpu_hours);
              const activePct = d.allocated_gpu_hours > 0 ? Math.round(100 * d.active_gpu_hours / d.allocated_gpu_hours) : 0;
              const effPct = d.active_gpu_hours > 0 ? Math.round(100 * d.utilized_gpu_hours / d.active_gpu_hours) : 0;
              const idlePct = d.allocated_gpu_hours > 0 ? Math.round(100 * d.idle_gpu_hours / d.allocated_gpu_hours) : 0;
              return (<div key={i} className="flex flex-col items-center justify-end h-full group relative" style={{ width: dayW - 4 }}>
                <div className="absolute -top-12 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity bg-zinc-800 text-white text-[9px] rounded px-2 py-1.5 whitespace-nowrap z-10 pointer-events-none leading-relaxed">
                  <div>{d.day.slice(5)}</div>
                  <div>Alloc: {allocPct}% | Active: {activePct}% | Idle: {idlePct}%</div>
                  <div>Efficiency: {effPct}%</div>
                </div>
                <div className="w-full flex flex-col-reverse">
                  <div style={{ height: `${utilH}px` }} className="w-full bg-emerald-500 rounded-t" />
                  <div style={{ height: `${nonEffH}px` }} className="w-full bg-orange-300" />
                  <div style={{ height: `${idleH}px` }} className="w-full bg-amber-400" />
                  <div style={{ height: `${nonUsedH}px` }} className="w-full bg-zinc-200 rounded-t" />
                </div>
                <div className="text-[8px] text-zinc-400 mt-1 whitespace-nowrap">{d.day.slice(5)}</div>
              </div>);
            })}
          </div>);
        })()}
        </div>
      </div>)}

      {/* ─── 6. Per Virtual Cluster GPU-Hours (date range from DB) ─── */}
      {vcSummary.length > 0 && (
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <h3 className="text-sm font-semibold text-zinc-800 mb-2">Per Virtual Cluster GPU-Hours ({from} to {to})</h3>
          <div className="overflow-x-auto">
          <table className="w-full text-xs min-w-[700px]">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <th className="py-1.5 pr-2">Virtual Cluster</th>
                <th className="py-1.5 pr-2 text-right">Total (h)</th>
                <th className="py-1.5 pr-2 text-right">Allocated</th>
                <th className="py-1.5 pr-2 text-right">Active</th>
                <th className="py-1.5 pr-2 text-right">Eff. Used</th>
                <th className="py-1.5 pr-2 text-right">Idle</th>
                <th className="py-1.5 pr-2">Alloc%</th>
                <th className="py-1.5 pr-2">Active%</th>
                <th className="py-1.5 pr-2">Idle%</th>
                <th className="py-1.5">Efficiency</th>
              </tr>
            </thead>
            <tbody>
              {vcSummary.map(v => {
                const allocPct = v.total_gpu_hours > 0 ? Math.round(100 * v.allocated_gpu_hours / v.total_gpu_hours) : 0;
                const activePct = v.allocated_gpu_hours > 0 ? Math.round(100 * v.active_gpu_hours / v.allocated_gpu_hours) : 0;
                const idlePct = v.allocated_gpu_hours > 0 ? Math.round(100 * v.idle_gpu_hours / v.allocated_gpu_hours) : 0;
                const effPct = v.active_gpu_hours > 0 ? Math.round(100 * v.utilized_gpu_hours / v.active_gpu_hours) : 0;
                return (
                  <tr key={v.virtual_cluster} className="border-b border-zinc-50">
                    <td className="py-1.5 pr-2 font-medium text-zinc-800">{v.virtual_cluster}</td>
                    <td className="py-1.5 pr-2 text-right text-zinc-700">{fmt(v.total_gpu_hours)}</td>
                    <td className="py-1.5 pr-2 text-right text-blue-600">{fmt(v.allocated_gpu_hours)}</td>
                    <td className="py-1.5 pr-2 text-right text-indigo-600">{fmt(v.active_gpu_hours)}</td>
                    <td className="py-1.5 pr-2 text-right text-emerald-600">{fmt(v.utilized_gpu_hours)}</td>
                    <td className="py-1.5 pr-2 text-right text-amber-500">{fmt(v.idle_gpu_hours)}</td>
                    <td className="py-1.5 pr-2">
                      <span className="mr-1 text-zinc-700">{allocPct}%</span>
                      <span className="inline-block w-10 h-1.5 rounded bg-zinc-100 overflow-hidden align-middle">
                        <span className="block h-full rounded bg-blue-400" style={{ width: `${allocPct}%` }} />
                      </span>
                    </td>
                    <td className="py-1.5 pr-2">
                      <span className="mr-1 text-zinc-700">{activePct}%</span>
                      <span className="inline-block w-10 h-1.5 rounded bg-zinc-100 overflow-hidden align-middle">
                        <span className="block h-full rounded bg-indigo-400" style={{ width: `${activePct}%` }} />
                      </span>
                    </td>
                    <td className="py-1.5 pr-2">
                      <span className="mr-1 text-zinc-700">{idlePct}%</span>
                      <span className="inline-block w-10 h-1.5 rounded bg-zinc-100 overflow-hidden align-middle">
                        <span className="block h-full rounded bg-amber-400" style={{ width: `${idlePct}%` }} />
                      </span>
                    </td>
                    <td className="py-1.5">
                      <span className="mr-1 text-zinc-700">{effPct}%</span>
                      <span className="inline-block w-10 h-1.5 rounded bg-zinc-100 overflow-hidden align-middle">
                        <span className="block h-full rounded" style={{ width: `${effPct}%`, background: effPct >= 80 ? "#22c55e" : effPct >= 50 ? "#f59e0b" : "#ef4444" }} />
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {/* ─── 7. Per-VC GPU-Hours Breakdown (daily) + Avg Utilization ─── */}
      {vcDaily.length > 0 && vcSummary.length > 0 && (() => {
        // Group daily data by VC
        const vcMap = new Map<string, VcDailyRow[]>();
        for (const row of vcDaily) {
          if (!vcMap.has(row.virtual_cluster)) vcMap.set(row.virtual_cluster, []);
          vcMap.get(row.virtual_cluster)!.push(row);
        }
        // Sort VCs by total (from vcSummary order)
        const vcOrder = vcSummary.map(v => v.virtual_cluster);
        return (<div className="bg-white rounded-xl border border-zinc-200 p-4">
          <h3 className="text-sm font-semibold text-zinc-800 mb-3">Per Virtual Cluster — Daily Breakdown</h3>
          <div className="space-y-6">
            {vcOrder.filter(vc => vcMap.has(vc)).map(vc => {
              const days = vcMap.get(vc)!;
              const summary = vcSummary.find(v => v.virtual_cluster === vc)!;
              const allocPct = summary.total_gpu_hours > 0 ? Math.round(100 * summary.allocated_gpu_hours / summary.total_gpu_hours) : 0;
              const activePct = summary.allocated_gpu_hours > 0 ? Math.round(100 * summary.active_gpu_hours / summary.allocated_gpu_hours) : 0;
              const effPct = summary.active_gpu_hours > 0 ? Math.round(100 * summary.utilized_gpu_hours / summary.active_gpu_hours) : 0;
              return (<div key={vc} className="border-b border-zinc-100 pb-4 last:border-0">
                <div className="flex items-center gap-3 mb-2">
                  <span className="font-medium text-zinc-800 text-xs">{vc}</span>
                  <span className="text-[10px] text-zinc-400">Alloc {allocPct}% · Active {activePct}% · Eff {effPct}%</span>
                  <span className="text-[10px] text-zinc-400">Avg Util: {fmt(summary.avg_utilization)}%</span>
                </div>
                {/* Stacked bar chart */}
                <div className="overflow-x-auto pt-10">
                {(() => {
                  const dayW = 40;
                  const barAreaW = Math.max(300, days.length * dayW);
                  const maxH = Math.max(...days.map(d => d.total_gpu_hours), 1);
                  return (<div className="flex items-end gap-1 h-24 border-b border-l border-zinc-100 pl-1 pb-5 relative" style={{ width: barAreaW, minWidth: barAreaW }}>
                    {days.map((d, i) => {
                      const scale = 70 / maxH;
                      const utilH = d.utilized_gpu_hours * scale;
                      const nonEffH = Math.max(0, d.active_gpu_hours - d.utilized_gpu_hours) * scale;
                      const idleH = d.idle_gpu_hours * scale;
                      const nonUsedH = d.non_used_gpu_hours * scale;
                      const dAllocPct = d.total_gpu_hours > 0 ? Math.round(100 * d.allocated_gpu_hours / d.total_gpu_hours) : 0;
                      const dActivePct = d.allocated_gpu_hours > 0 ? Math.round(100 * d.active_gpu_hours / d.allocated_gpu_hours) : 0;
                      const dIdlePct = d.allocated_gpu_hours > 0 ? Math.round(100 * d.idle_gpu_hours / d.allocated_gpu_hours) : 0;
                      const dEffPct = d.active_gpu_hours > 0 ? Math.round(100 * d.utilized_gpu_hours / d.active_gpu_hours) : 0;
                      return (<div key={i} className="flex flex-col items-center justify-end h-full group relative" style={{ width: dayW - 4 }}>
                        <div className="absolute -top-9 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity bg-zinc-800 text-white text-[9px] rounded px-2 py-1 whitespace-nowrap z-10 pointer-events-none leading-relaxed">
                          {d.day.slice(5)} | A:{dAllocPct}% Act:{dActivePct}% Idle:{dIdlePct}% Eff:{dEffPct}%
                        </div>
                        <div className="w-full flex flex-col-reverse">
                          <div style={{ height: `${utilH}px` }} className="w-full bg-emerald-500 rounded-t" />
                          <div style={{ height: `${nonEffH}px` }} className="w-full bg-orange-300" />
                          <div style={{ height: `${idleH}px` }} className="w-full bg-amber-400" />
                          <div style={{ height: `${nonUsedH}px` }} className="w-full bg-zinc-200 rounded-t" />
                        </div>
                        <div className="text-[7px] text-zinc-400 mt-1 whitespace-nowrap">{d.day.slice(5)}</div>
                      </div>);
                    })}
                  </div>);
                })()}
                </div>
                {/* Utilization line */}
                <div className="overflow-x-auto mt-2">
                {(() => {
                  const dayW = 40;
                  const n = days.length;
                  const W = Math.max(300, n * dayW + 40), H = 60, padL = 28, padR = 5, padT = 12, padB = 14;
                  const plotW = W - padL - padR, plotH = H - padT - padB;
                  const vals = days.map(d => d.avg_utilization);
                  const minV = Math.max(0, Math.floor(Math.min(...vals) / 10) * 10 - 10);
                  const maxV = Math.min(100, Math.ceil(Math.max(...vals) / 10) * 10 + 10);
                  const xAt = (i: number) => padL + (i / Math.max(n - 1, 1)) * plotW;
                  const yAt = (v: number) => padT + plotH - ((v - minV) / (maxV - minV || 1)) * plotH;
                  return (<svg width={W} height={H} className="block" style={{ minWidth: W }}>
                    <line x1={padL} y1={padT + plotH} x2={W - padR} y2={padT + plotH} stroke="#f4f4f5" />
                    <text x={padL - 3} y={padT + 3} fontSize="7" fill="#a1a1aa" textAnchor="end">{maxV}%</text>
                    <text x={padL - 3} y={padT + plotH + 3} fontSize="7" fill="#a1a1aa" textAnchor="end">{minV}%</text>
                    <polyline points={days.map((d, i) => `${xAt(i)},${yAt(d.avg_utilization)}`).join(' ')} fill="none" stroke="#f97316" strokeWidth="1.5" />
                    {days.map((d, i) => (<circle key={i} cx={xAt(i)} cy={yAt(d.avg_utilization)} r="2.5" fill="#f97316" stroke="white" strokeWidth="1">
                      <title>{d.day.slice(5)}: {fmt(d.avg_utilization)}%</title>
                    </circle>))}
                  </svg>);
                })()}
                </div>
              </div>);
            })}
          </div>
        </div>);
      })()}

      {history && history.daily.length === 0 && !histLoading && (
        <div className="bg-white rounded-xl border border-zinc-200 p-6 text-center text-sm text-zinc-400">No historical data yet. The collector gathers data every 5 minutes.</div>
      )}
    </div>
  );
}
