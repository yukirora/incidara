import { useEffect, useState, useMemo } from "react";
import { useMe } from "../hooks/useMe";
import {
  fetchReliability,
  fetchMtbf,
  ReliabilityKpi,
  DailyFailureRow,
  FailureCategoryRow,
  RcaReasonRow,
  RecycleTimeRow,
  FailureDetailRow,
  MtbfResponse,
} from "../api/reports";

const CAT_COLORS: Record<string, string> = {
  hardware: "#f87171",
  platform: "#60a5fa",
  unknown: "#a1a1aa",
  user: "#fbbf24",
};
const CAT_LABELS: Record<string, string> = {
  hardware: "Hardware",
  platform: "Platform",
  unknown: "Unknown",
  user: "User",
};
const CAT_PILL: Record<string, string> = {
  hardware: "hw",
  platform: "pl",
  unknown: "uk",
  user: "usr",
};

const SKU_COLORS: Record<string, string> = {
  h200: "#60a5fa",
  b300: "#c084fc",
  cpu: "#34d399",
  storage: "#fbbf24",
  ctrl: "#f87171",
  unknown: "#a1a1aa",
};

function fmt(n: number) {
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function KpiCard({
  label,
  value,
  delta,
  deltaUnit,
}: {
  label: string;
  value: string;
  delta?: number;
  deltaUnit?: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-zinc-200 p-4">
      <div className="text-xs text-zinc-500 mb-1">{label}</div>
      <div className="text-2xl font-semibold text-zinc-900">{value}</div>
      {delta !== undefined && delta !== 0 && (
        <span
          className={`text-xs font-medium ${
            delta > 0 ? "text-red-500" : "text-emerald-600"
          }`}
        >
          {delta > 0 ? "↑" : "↓"} {Math.abs(delta).toFixed(1)}
          {deltaUnit}
        </span>
      )}
    </div>
  );
}

function SparkBars({ data, color }: { data: RecycleTimeRow[]; color: string }) {
  const maxVal = Math.max(...data.map((d) => d.avg_days), 0.1);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const W = 200, H = 40;
  const barW = W / data.length;

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minHeight: 30 }}>
        {data.map((d, i) => (
          <rect
            key={i}
            x={i * barW + 1}
            y={H - (d.avg_days / maxVal) * H}
            width={barW - 2}
            height={(d.avg_days / maxVal) * H}
            fill={hoverIdx === i ? color : color + "cc"}
            rx={1}
            onMouseEnter={() => setHoverIdx(i)}
            onMouseLeave={() => setHoverIdx(null)}
          />
        ))}
      </svg>
      {hoverIdx !== null && data[hoverIdx] && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 bg-zinc-800 text-white text-[10px] rounded shadow whitespace-nowrap pointer-events-none z-10">
          {data[hoverIdx].period_label}: {fmt(data[hoverIdx].avg_days)}d · {data[hoverIdx].count} events
        </div>
      )}
    </div>
  );
}

function CatPill({ category }: { category: string }) {
  const c = CAT_COLORS[category] ?? "#a1a1aa";
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold text-white uppercase"
      style={{ background: c }}
    >
      {CAT_PILL[category] ?? category.slice(0, 2)}
    </span>
  );
}

// ─── Daily Failures Area Chart ──────────────────────────────────────

function DailyFailureChart({ daily }: { daily: DailyFailureRow[] }) {
  const W = 420, H = 170;
  const pad = { l: 30, r: 15, t: 10, b: 20 };
  const cw = W - pad.l - pad.r;
  const ch = H - pad.t - pad.b;
  const maxVal = Math.max(...daily.map((d) => d.total), 1);
  const xScale = (i: number) => pad.l + (i / Math.max(daily.length - 1, 1)) * cw;
  const yScale = (v: number) => pad.t + ch - (v / maxVal) * ch;

  const groups = [
    { key: "hardware", color: CAT_COLORS.hardware },
    { key: "platform", color: CAT_COLORS.platform },
    { key: "unknown", color: CAT_COLORS.unknown },
    { key: "user", color: CAT_COLORS.user },
  ];

  const stacks = useMemo(() => {
    return groups.map((g) => {
      const pts: { y: number; h: number }[] = [];
      for (let di = 0; di < daily.length; di++) {
        const bottomGroups = groups.slice(0, groups.indexOf(g));
        const below = bottomGroups.reduce(
          (s, bg) => s + ((daily[di] as any)[bg.key] ?? 0),
          0
        );
        const val = (daily[di] as any)[g.key] ?? 0;
        const y = yScale(below + val);
        const h = yScale(below) - y;
        pts.push({ y, h });
      }
      return pts;
    });
  }, [daily]);

  const xLabelStep = Math.max(1, Math.floor(daily.length / 5));
  const [hoverDay, setHoverDay] = useState<number | null>(null);

  return (
    <div className="relative">
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minHeight: 140 }}>
      {/* Y grid */}
      {[0, 0.25, 0.5, 0.75, 1].map((f) => (
        <line key={f} x1={pad.l} y1={pad.t + ch * (1 - f)} x2={W - pad.r} y2={pad.t + ch * (1 - f)} stroke="#f4f4f5" strokeWidth={0.5} />
      ))}
      {/* Y labels */}
      {[0, 0.5, 1].map((f) => (
        <text key={f} x={pad.l - 4} y={pad.t + ch * (1 - f) + 3} fill="#a1a1aa" fontSize={7} textAnchor="end">
          {Math.round(maxVal * f)}
        </text>
      ))}
      {/* Stacked areas */}
      {groups.map((g, gi) => {
        const pts = stacks[gi];
        const areaPath = daily.map((_, di) => `${di === 0 ? "M" : "L"}${xScale(di)},${pts[di].y}`).join(" ");
        const closePath = daily.map((_, di) => `L${xScale(di)},${pts[di].y + pts[di].h}`).reverse().join(" ");
        return (
          <path
            key={g.key}
            d={`${areaPath} ${closePath} Z`}
            fill={g.color}
            opacity={0.7}
          />
        );
      })}
      {/* Hover line */}
      {hoverDay !== null && (
        <line x1={xScale(hoverDay)} y1={pad.t} x2={xScale(hoverDay)} y2={pad.t + ch} stroke="#71717a" strokeWidth={0.5} strokeDasharray="2 2" />
      )}
      {/* X labels */}
      {daily
        .filter((_, i) => i % xLabelStep === 0 || i === daily.length - 1)
        .map((d, idx) => {
          const i = Math.min(idx * xLabelStep, daily.length - 1);
          return (
            <text key={i} x={xScale(i)} y={H - 4} fill="#a1a1aa" fontSize={7} textAnchor="middle">
              {d.day.slice(5)}
            </text>
          );
        })}
      {/* Hover rects (invisible) */}
      {daily.map((_, i) => (
        <rect
          key={i}
          x={xScale(i) - cw / daily.length / 2}
          y={pad.t}
          width={cw / daily.length}
          height={ch}
          fill="transparent"
          onMouseEnter={() => setHoverDay(i)}
          onMouseLeave={() => setHoverDay(null)}
        />
      ))}
    </svg>
    {/* Tooltip */}
    {hoverDay !== null && daily[hoverDay] && (
      <div className="absolute top-1 left-1/2 -translate-x-1/2 px-2 py-1 bg-zinc-800 text-white text-[10px] rounded shadow whitespace-nowrap pointer-events-none z-10">
        <div className="font-semibold mb-0.5">{daily[hoverDay].day}</div>
        {groups.map(g => {
          const val = (daily[hoverDay] as any)[g.key] ?? 0;
          return val > 0 ? <div key={g.key} className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full" style={{background: g.color}} />{g.key}: {val}</div> : null;
        })}
        <div className="border-t border-zinc-600 mt-0.5 pt-0.5">Total: {daily[hoverDay].total}</div>
      </div>
    )}
    </div>
  );
}

// ─── Main Page ──────────────────────────────────────────────────────

export default function ReportReliability() {
  const { is_admin } = useMe();
  if (!is_admin) return <div className="p-8 text-zinc-400">Admin access required</div>;

  const today = new Date().toISOString().slice(0, 10);
  const twoWeeksAgo = new Date(Date.now() - 13 * 86400000).toISOString().slice(0, 10);

  const [from, setFrom] = useState(twoWeeksAgo);
  const [to, setTo] = useState(today);
  const [category, setCategory] = useState("all");
  const [reasonSearch, setReasonSearch] = useState("");
  const [nodeSearch, setNodeSearch] = useState("");
  const [mtbfType, setMtbfType] = useState("hardware");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<{
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
  } | null>(null);

  const [mtbf, setMtbf] = useState<MtbfResponse | null>(null);
  const [mtbfLoading, setMtbfLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      // Load reliability first (fast), MTBF loads separately (slow query)
      const res = await fetchReliability({ from, to, category });
      setData(res);
      setMtbfLoading(true);
      fetchMtbf({ from, to, category, reason_search: reasonSearch, node_search: nodeSearch, mtbf_type: mtbfType })
        .then(setMtbf)
        .catch((e) => console.error("MTBF fetch error:", e.message))
        .finally(() => setMtbfLoading(false));
    } catch (e: any) {
      console.error("Reliability fetch error:", e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [from, to, category, reasonSearch, nodeSearch, mtbfType]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!data) return <div className="p-8 text-zinc-400">{loading ? "Loading..." : "No data"}</div>;

  const { kpi, daily, categories, rca, ofr_recycle, cordon_recycle, details } = data;
  const alertLabel = kpi.alert_level === "normal" ? "✓ Normal" : kpi.alert_level === "warning" ? "⚠ Warning" : "🔴 Critical";
  const alertColor = kpi.alert_level === "normal" ? "#16a34a" : kpi.alert_level === "warning" ? "#f59e0b" : "#dc2626";

  const latestDay = daily.length > 0 ? daily[daily.length - 1] : null;
  const firstDay = daily.length > 0 ? daily[0] : null;
  const delta = firstDay && latestDay ? latestDay.total - firstDay.total : undefined;

  return (
    <div className="p-6 space-y-6" style={{ maxWidth: 1400 }}>
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-zinc-900">Reliability</h1>
        <p className="text-sm text-zinc-500">Node failure trends, categorization, and RCA breakdown</p>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <label className="text-xs text-zinc-500">
          From <input type="date" className="ml-1 border rounded px-2 py-1 text-xs" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label className="text-xs text-zinc-500">
          To <input type="date" className="ml-1 border rounded px-2 py-1 text-xs" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <select className="border rounded px-2 py-1 text-xs" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="all">All SKUs</option>
          <option value="h200">h200</option>
          <option value="b300">b300</option>
          <option value="cpu">cpu</option>
          <option value="storage">storage</option>
          <option value="ctrl">ctrl</option>
        </select>
        <button onClick={load} className="px-3 py-1 bg-indigo-500 text-white text-xs rounded hover:bg-indigo-600">↻ Refresh</button>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-4">
        <KpiCard label="Node Failures" value={fmt(kpi.total_failures)} delta={delta} deltaUnit=" vs first day" />
        <KpiCard label="Avg Failures / Day" value={fmt(kpi.avg_per_day)} />
        <KpiCard label="Avg Cordon → Fix" value={`${fmt(kpi.avg_cordon_fix_days)}d`} />
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-xs text-zinc-500 mb-1">Reliability Alert</div>
          <div className="text-2xl font-semibold" style={{ color: alertColor }}>{alertLabel}</div>
          <span className="text-xs text-zinc-400">Avg &lt; 15/day = Normal</span>
        </div>
      </div>

      {/* Row: Daily chart + Categorization */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-sm font-semibold text-zinc-800 mb-2">Daily Node Failures</div>
          <DailyFailureChart daily={daily} />
          {/* Legend */}
          <div className="flex gap-4 mt-2">
            {Object.entries(CAT_LABELS).map(([k, label]) => (
              <span key={k} className="flex items-center gap-1 text-[10px] text-zinc-500">
                <span className="w-2 h-2 rounded-sm inline-block" style={{ background: CAT_COLORS[k] }} />
                {label}
              </span>
            ))}
          </div>
        </div>
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-sm font-semibold text-zinc-800 mb-3">Failure Categorization</div>
          <div className="space-y-3">
            {categories.map((cat) => (
              <div key={cat.category}>
                <div className="flex justify-between items-center mb-1">
                  <span className="flex items-center gap-1.5">
                    <CatPill category={cat.category} />
                  </span>
                  <span className="text-xs font-semibold">{cat.count} <span className="text-zinc-400 font-normal">({fmt(cat.pct)}%)</span></span>
                </div>
                <div className="h-2 bg-zinc-100 rounded overflow-hidden flex">
                  {cat.reasons.map((r, ri) => {
                    const shades: Record<string, string[]> = {
                      hardware: ["#fca5a5", "#f87171", "#ef4444", "#dc2626"],
                      platform: ["#93c5fd", "#60a5fa", "#3b82f6"],
                      unknown: ["#d4d4d8"],
                      user: ["#fde047", "#fbbf24"],
                    };
                    const s = shades[cat.category] ?? ["#d4d4d8"];
                    return (
                      <div key={ri} style={{ width: `${(r.count / cat.count) * 100}%`, background: s[ri % s.length] }} />
                    );
                  })}
                </div>
                <div className="flex gap-3 mt-1">
                  {cat.reasons.slice(0, 4).map((r, ri) => (
                    <span key={ri} className="text-[10px] text-zinc-500">
                      {r.reason.length > 12 ? r.reason.slice(0, 12) + "…" : r.reason}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Row: RCA + Recycle */}
      <div className="grid grid-cols-5 gap-4">
        {/* Top RCA Reasons */}
        <div className="col-span-3 bg-white rounded-xl border border-zinc-200 p-4">
          <div className="text-sm font-semibold text-zinc-800 mb-2">Top RCA Reasons</div>
          <div className="max-h-[200px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-zinc-500 border-b">
                  <th className="py-1.5 pr-2">Reason</th>
                  <th className="py-1.5 pr-2">Category</th>
                  <th className="py-1.5 pr-2 text-right">Count</th>
                  <th className="py-1.5 pr-2 text-right">WoW</th>
                  <th className="py-1.5">Share</th>
                </tr>
              </thead>
              <tbody>
                {rca.map((r, i) => (
                  <tr key={i} className="border-b border-zinc-50">
                    <td className="py-1.5 pr-2 font-medium text-zinc-800">{r.reason}</td>
                    <td className="py-1.5 pr-2"><CatPill category={r.category} /></td>
                    <td className="py-1.5 pr-2 text-right font-semibold">{r.count}</td>
                    <td className="py-1.5 pr-2 text-right" style={{ color: r.wow > 0 ? "#dc2626" : r.wow < 0 ? "#16a34a" : "#71717a" }}>
                      {r.wow === 0 ? "—" : `${r.wow > 0 ? "↑" : "↓"} ${Math.abs(r.wow)}`}
                    </td>
                    <td className="py-1.5">
                      <div className="h-1.5 bg-zinc-100 rounded overflow-hidden">
                        <div className="h-full rounded" style={{ width: `${r.share}%`, background: CAT_COLORS[r.category] ?? "#a1a1aa" }} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Recycle times */}
        <div className="col-span-2 space-y-4">
          <div className="bg-white rounded-xl border border-zinc-200 p-4">
            <div className="flex justify-between items-center mb-1">
              <span className="text-sm font-semibold text-zinc-800">OFR Recycle Time</span>
              <span className="text-xs">
                <strong>{fmt(data.ofr_avg_days)}d</strong> · {data.ofr_count} fixes
              </span>
            </div>
            <div className="text-[10px] text-zinc-500 mb-2">Hardware failures: available → available</div>
            <SparkBars data={ofr_recycle} color="#fbbf24" />
            <div className="flex justify-between text-[9px] text-zinc-400 mt-1">
              {ofr_recycle.map((r, i) => i % 2 === 0 && <span key={i}>{r.period_label}</span>)}
            </div>
          </div>
          <div className="bg-white rounded-xl border border-zinc-200 p-4">
            <div className="flex justify-between items-center mb-1">
              <span className="text-sm font-semibold text-zinc-800">Cordon Recycle Time</span>
              <span className="text-xs">
                <strong>{fmt(data.cordon_avg_days)}d</strong> · {data.cordon_count} fixes
              </span>
            </div>
            <div className="text-[10px] text-zinc-500 mb-2">Non-hardware failures: available → available</div>
            <SparkBars data={cordon_recycle} color="#93c5fd" />
            <div className="flex justify-between text-[9px] text-zinc-400 mt-1">
              {cordon_recycle.map((r, i) => i % 2 === 0 && <span key={i}>{r.period_label}</span>)}
            </div>
          </div>
        </div>
      </div>

      {/* Failure Details */}
      <div className="bg-white rounded-xl border border-zinc-200 p-4">
        <div className="flex justify-between items-center mb-2">
          <span className="text-sm font-semibold text-zinc-800">Node Failure Details</span>
          <span className="text-xs text-zinc-400">{kpi.total_failures} failures in period</span>
        </div>
        <div className="max-h-[240px] overflow-y-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <th className="py-1.5 pr-2">Hostname</th>
                <th className="py-1.5 pr-2">IP</th>
                <th className="py-1.5 pr-2">Category</th>
                <th className="py-1.5 pr-2">Reason</th>
                <th className="py-1.5">Time</th>
              </tr>
            </thead>
            <tbody>
              {details.map((d, i) => (
                <tr key={i} className="border-b border-zinc-50 align-top">
                  <td className="py-1.5 pr-2 font-medium text-zinc-800 font-mono text-[11px]">{d.hostname}</td>
                  <td className="py-1.5 pr-2 text-zinc-500 font-mono text-[11px]">{d.ip}</td>
                  <td className="py-1.5 pr-2"><CatPill category={d.category} /></td>
                  <td className="py-1.5 pr-2 text-zinc-700">
                    <span>{d.reason}</span>
                    {d.detail && <span className="block text-zinc-400 text-[10px] mt-0.5 leading-tight">{d.detail.length > 200 ? d.detail.slice(0, 200) + '…' : d.detail}</span>}
                  </td>
                  <td className="py-1.5 text-zinc-400 whitespace-nowrap">{new Date(d.timestamp).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Node MTBF */}
      {mtbfLoading && (
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <span className="text-sm text-zinc-400">Loading MTBF data…</span>
        </div>
      )}
      {mtbf && (
        <div className="bg-white rounded-xl border border-zinc-200 p-4">
          <div className="flex justify-between items-center mb-3">
            <span className="text-sm font-semibold text-zinc-800">Node MTBF Rank</span>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1 bg-zinc-100 rounded-lg p-0.5">
                <button
                  onClick={() => setMtbfType("hardware")}
                  className={`px-2 py-0.5 text-[11px] rounded ${mtbfType === "hardware" ? "bg-white text-zinc-900 font-medium shadow-sm" : "text-zinc-500"}`}
                >HW Only</button>
                <button
                  onClick={() => setMtbfType("all")}
                  className={`px-2 py-0.5 text-[11px] rounded ${mtbfType === "all" ? "bg-white text-zinc-900 font-medium shadow-sm" : "text-zinc-500"}`}
                >All Failures</button>
              </div>
              <input
                type="text"
                placeholder="Search node name…"
                value={nodeSearch}
                onChange={(e) => setNodeSearch(e.target.value)}
                className="border border-zinc-300 rounded-lg px-2.5 py-1 text-xs w-44 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
              <input
                type="text"
                placeholder="Search reason:detail (e.g. NodeCrash, IBLink, ECC)…"
                value={reasonSearch}
                onChange={(e) => setReasonSearch(e.target.value)}
                className="border border-zinc-300 rounded-lg px-2.5 py-1 text-xs w-64 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
              <span className="text-xs text-zinc-400">
                {mtbf.summary.total_nodes} nodes · {mtbf.summary.total_failures} failures · avg {fmt(mtbf.summary.avg_mtbf_hours)}h ({fmt(mtbf.summary.avg_mtbf_days)}d)
              </span>
            </div>
          </div>
          {/* MTBF trend: cumulative line (primary) + weekly bars (secondary) */}
          {mtbf.trend.length > 0 && (
            <div className="mb-4">
              <div className="flex items-center gap-4 mb-2">
                <span className="text-xs text-zinc-500">MTBF Trend (hours)</span>
                <span className="flex items-center gap-1 text-[10px] text-zinc-400">
                  <span className="w-4 h-0.5 bg-orange-500 rounded" /> Cumulative
                </span>
                <span className="flex items-center gap-1 text-[10px] text-zinc-400">
                  <span className="w-2 h-2 rounded-sm bg-zinc-100 border border-zinc-200" /> Weekly
                </span>
              </div>
              {(() => {
                const W = 480;
                const H = 130;
                const padL = 8;
                const padB = 20;
                const plotW = W - padL - 8;
                const plotH = H - padB - 10;
                const maxVal = Math.max(...mtbf.trend.map(t => Math.max(t.avg_mtbf_hours, t.cumulative_mtbf_hours, 1)), 1);
                const slot = plotW / mtbf.trend.length;
                const xAt = (i: number) => padL + i * slot + slot / 2;
                const yAt = (v: number) => plotH + 10 - Math.max(2, (v / maxVal) * plotH);
                return (
                  <svg width={W} height={H} className="block">
                    <line x1={padL} y1={plotH + 10} x2={W - 8} y2={plotH + 10} stroke="#e4e4e7" />
                    {mtbf.trend.map((t, i) => {
                      const bh = Math.max(2, (t.avg_mtbf_hours / maxVal) * plotH);
                      const bx = xAt(i) - slot * 0.3;
                      const bw = slot * 0.6;
                      return <rect key={`b${i}`} x={bx} y={plotH + 10 - bh} width={bw} height={bh} fill="#f4f4f5" rx="2" />;
                    })}
                    <polyline
                      points={mtbf.trend.map((t, i) => `${xAt(i)},${yAt(t.cumulative_mtbf_hours)}`).join(' ')}
                      fill="none" stroke="#f97316" strokeWidth="2"
                    />
                    {mtbf.trend.map((t, i) => (
                      <circle key={`d${i}`} cx={xAt(i)} cy={yAt(t.cumulative_mtbf_hours)} r="3.5" fill="#f97316" stroke="white" strokeWidth="1.5">
                        <title>avg: {fmt(t.avg_mtbf_hours)}h | cum: {fmt(t.cumulative_mtbf_hours)}h | {t.failure_count} fails | {t.node_count} nodes</title>
                      </circle>
                    ))}
                    {mtbf.trend.map((t, i) => (
                      <text key={`l${i}`} x={xAt(i)} y={H - 6} fontSize="8" fill="#a1a1aa" textAnchor="middle">{t.week}</text>
                    ))}
                  </svg>
                );
              })()}
            </div>
          )}
          <div className="max-h-[320px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-zinc-500 border-b sticky top-0 bg-white">
                  <th className="py-1.5 pr-2">#</th>
                  <th className="py-1.5 pr-2">Hostname</th>
                  <th className="py-1.5 pr-2">SKU</th>
                  <th className="py-1.5 pr-2 text-right">Failures</th>
                  <th className="py-1.5 pr-2 text-right">MTBF (h)</th>
                  <th className="py-1.5 pr-2 text-right">MTBF (d)</th>
                  <th className="py-1.5">Failure Log</th>
                </tr>
              </thead>
              <tbody>
                {mtbf.nodes.map((n) => (
                  <tr key={n.hostname} className="border-b border-zinc-50 align-top">
                    <td className="py-1.5 pr-2 text-zinc-400">{n.rank}</td>
                    <td className="py-1.5 pr-2 font-mono text-[11px] text-zinc-800">{n.hostname}</td>
                    <td className="py-1.5 pr-2">
                      <span className="inline-flex items-center gap-1">
                        <span className="w-2 h-2 rounded-full" style={{ background: SKU_COLORS[n.category] ?? "#a1a1aa" }} />
                        <span className="text-zinc-600">{n.category}</span>
                      </span>
                    </td>
                    <td className="py-1.5 pr-2 text-right text-zinc-700">{n.failures}</td>
                    <td className="py-1.5 pr-2 text-right font-medium text-zinc-800">{n.mtbf_hours !== null ? fmt(n.mtbf_hours) : "—"}</td>
                    <td className="py-1.5 pr-2 text-right text-zinc-500">{n.mtbf_days !== null ? fmt(n.mtbf_days) : "—"}</td>
                    <td className="py-1.5 max-w-[320px]">
                      {n.failure_details && n.failure_details.length > 0 ? (
                        <div className="space-y-0.5">
                          {n.failure_details.slice(0, 5).map((fd, i) => {
                            // Match with reliability details by hostname + timestamp for full reason
                            const match = details.find(d => d.hostname === n.hostname && Math.abs(new Date(d.timestamp).getTime() - new Date(fd.timestamp).getTime()) < 60000);
                            const reason = match ? (match.reason + (match.detail ? ': ' + match.detail : '')) : fd.reason;
                            const cat = match ? match.category : fd.category;
                            return (
                              <div key={i} className="flex items-start gap-1 text-[10px]">
                                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1" style={{ background: CAT_COLORS[cat] ?? "#a1a1aa" }} />
                                <span className="text-zinc-400 whitespace-nowrap">{new Date(fd.timestamp).toLocaleDateString()}</span>
                                <span className="text-zinc-600 truncate" title={reason}>{reason || "—"}</span>
                              </div>
                            );
                          })}
                          {n.failure_details.length > 5 && (
                            <span className="text-[10px] text-zinc-400">+{n.failure_details.length - 5} more</span>
                          )}
                        </div>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
