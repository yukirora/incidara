import { useState, useEffect } from "react";
import { api } from "../lib/api";

interface AgentRow {
  tasks: { total: number; completed: number; failed: number };
  duration_s: { p50: number | null; p90: number | null; p95: number | null; p99: number | null };
  l_level: { l1: number; l2: number; l3: number; dominant: string; auto_pct: number; direction: string | null };
  approval: { approved: number; overridden: number; declined: number; rate: number | null };
  accuracy: Record<string, number>;
  cost: { total: number; avg: number; tokens_in: number; tokens_out: number; trend_pct: number | null };
  errors: { total: number; tool: number; llm: number; other: number };
}

interface FleetData {
  tasks: { total: number; completed: number; failed: number };
  duration_s: { p50: number | null; p90: number | null; p95: number | null; p99: number | null };
  cost: { total: number; avg: number };
  approval: Record<string, number> & { rate: number | null };
  safety: { blast_radius_blocked: number; circuit_breaker_tripped: number; repair_recurrence: number; false_positive_rma: number; unsafe_executed: number; blast_radius_avg: number | null; blast_radius_max: number | null };
}

interface MonthRow {
  month: string; total: number; completed: number; failed: number; avg_duration_s: number | null;
  nodes_cordoned: number | null; resolved_by_agent: number; agent_cost: number;
  mttr_p50_hours: number | null; mttr_p95_hours: number | null; incidents_hw: number | null;
  alert_precision_pct: number | null; rollback_rate_pct: number | null; toil_hours_saved: number | null;
}

interface MetricsResponse {
  period_days: number; from: string; to: string;
  agents: Record<string, AgentRow>;
  monthly: MonthRow[];
  fleet: FleetData;
}

type View = "trust" | "quality" | "performance" | "cost";

function fmtDuration(s: number | null): string {
  if (s === null || isNaN(s)) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
function fmtCost(n: number): string { return n >= 1 ? `$${n.toFixed(0)}` : n > 0 ? `$${n.toFixed(2)}` : "—"; }
function fmtCost2(n: number): string { return `$${n.toFixed(2)}`; }

function KPI({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="bg-white rounded-lg border p-3">
      <div className="text-[10px] text-zinc-400 uppercase tracking-wide">{label}</div>
      <div className="text-lg font-bold text-zinc-800 mt-0.5">{value}</div>
      <div className="text-[10px] text-zinc-500 mt-0.5">{sub}</div>
    </div>
  );
}

function Pill({ label, color }: { label: string; color: "green" | "yellow" | "red" | "gray" }) {
  const bg = { green: "bg-emerald-100 text-emerald-700", yellow: "bg-amber-100 text-amber-700", red: "bg-red-100 text-red-700", gray: "bg-zinc-100 text-zinc-600" };
  return <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${bg[color]}`}>{label}</span>;
}

function LLevel({ data }: { data: { dominant: string; auto_pct: number; direction: string | null } }) {
  const colors: Record<string, string> = { L1: "bg-blue-100 text-blue-700", L2: "bg-amber-100 text-amber-700", L3: "bg-emerald-100 text-emerald-700" };
  const arrows: Record<string, string> = { up: "↑", down: "↓", stable: "" };
  const arrowColors: Record<string, string> = { up: "text-emerald-600", down: "text-red-500", stable: "" };
  const dir = data.direction ?? "stable";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${colors[data.dominant] ?? "bg-zinc-100 text-zinc-500"}`}>
      {data.dominant}{arrows[dir] && <span className={arrowColors[dir]}> {arrows[dir]}</span>}
    </span>
  );
}

function Trend({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-zinc-400">—</span>;
  const color = pct > 10 ? "text-red-500" : pct < -10 ? "text-emerald-600" : "text-zinc-500";
  return <span className={`text-xs font-medium ${color}`}>{pct > 0 ? "+" : ""}{pct}%</span>;
}

export function AgentMetrics() {
  const [data, setData] = useState<MetricsResponse | null>(null);
  const today = new Date().toISOString().slice(0, 10);
  const [from, setFrom] = useState(() => { const d = new Date(); d.setDate(d.getDate() - 30); return d.toISOString().slice(0, 10); });
  const [to, setTo] = useState(today);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState<View>("trust");

  useEffect(() => {
    setLoading(true);
    api<MetricsResponse>("GET", `/api/agent-metrics?from=${from}&to=${to}`)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [from, to]);

  if (loading) return <div className="p-6 text-zinc-500">Loading metrics…</div>;
  if (error) return <div className="p-6 text-red-500">Error: {error}</div>;
  if (!data) return null;

  const agentList = Object.entries(data.agents).sort((a, b) => b[1].tasks.total - a[1].tasks.total);
  const f = data.fleet;

  let worstAgent = "", worstOverride = 0, closestToL3 = "", bestApproval = 0;
  for (const [id, ad] of agentList) {
    const appTotal = ad.approval.approved + ad.approval.overridden + ad.approval.declined;
    const or = appTotal > 0 ? Math.round((ad.approval.overridden / appTotal) * 100) : 0;
    if (or > worstOverride) { worstOverride = or; worstAgent = id; }
    if (ad.approval.rate !== null && ad.approval.rate > bestApproval) { bestApproval = ad.approval.rate; closestToL3 = id; }
  }

  const views: { key: View; label: string }[] = [
    { key: "trust", label: "🛡 Trust & Safety" },
    { key: "quality", label: "🎯 Quality" },
    { key: "performance", label: "⏱ Performance" },
    { key: "cost", label: "💰 Cost" },
  ];

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-zinc-800">Agent Metrics</h1>
        <div className="flex items-center gap-2 text-sm">
          <input type="date" value={from} onChange={e => setFrom(e.target.value)} className="border rounded px-2 py-1" />
          <span className="text-zinc-400">→</span>
          <input type="date" value={to} onChange={e => setTo(e.target.value)} className="border rounded px-2 py-1" />
        </div>
      </div>

      {/* Fleet KPIs */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <KPI label="Total Tasks" value={String(f.tasks.total)} sub={`${f.tasks.completed} ✓ · ${f.tasks.failed} ✗`} />
        <KPI label="Duration p50" value={fmtDuration(f.duration_s.p50)} sub={`p90: ${fmtDuration(f.duration_s.p90)} · p99: ${fmtDuration(f.duration_s.p99)}`} />
        <KPI label="Cost / Task" value={fmtCost2(f.cost.avg)} sub={`${fmtCost(f.cost.total)} total`} />
        <KPI label="Approval Rate" value={f.approval.rate !== null ? `${f.approval.rate}%` : "—"}
          sub={f.approval.rate !== null ? `${f.approval.approved ?? 0} approved · ${f.approval.overridden ?? 0} overridden` : "no decisions"} />
      </div>

      {/* View tabs */}
      <div className="flex gap-0 mb-6 border-b-2 border-zinc-200">
        {views.map((v) => (
          <button key={v.key} onClick={() => setView(v.key)}
            className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-0.5 transition-colors ${
              view === v.key ? "text-blue-600 border-blue-600" : "text-zinc-400 border-transparent hover:text-zinc-600"
            }`}>
            {v.label}
          </button>
        ))}
      </div>

      {/* ═══════ TRUST & SAFETY ═══════ */}
      {view === "trust" && (<>
        <div className="overflow-x-auto rounded-lg border mb-6">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Agent</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">L-Level</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Auto %</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Approval</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Overrides</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Escalation</th>
            </tr></thead>
            <tbody>
              {agentList.map(([id, a]) => {
                const appTotal = a.approval.approved + a.approval.overridden + a.approval.declined;
                const overridePct = appTotal > 0 ? Math.round(a.approval.overridden / appTotal * 100) : 0;
                const escalationPct = a.tasks.total > 0 ? Math.round((a.tasks.total - a.l_level.l3) / a.tasks.total * 100) : 0;
                return (<tr key={id} className="border-t hover:bg-zinc-50">
                  <td className="px-3 py-2.5 font-medium">{id}</td>
                  <td className="px-3 py-2.5 text-center"><LLevel data={a.l_level} /></td>
                  <td className="px-3 py-2.5 text-center">{a.l_level.auto_pct}%</td>
                  <td className="px-3 py-2.5 text-center">
                    {a.approval.rate !== null ? <Pill label={`${a.approval.rate}%`} color={a.approval.rate >= 95 ? "green" : a.approval.rate >= 85 ? "yellow" : "red"} /> : <span className="text-zinc-400">—</span>}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    {a.approval.overridden > 0 ? <Pill label={`${overridePct}% (${a.approval.overridden})`} color={overridePct >= 10 ? "red" : "yellow"} /> : <span className="text-zinc-400">0</span>}
                  </td>
                  <td className="px-3 py-2.5 text-center">{escalationPct}%</td>
                </tr>);
              })}
            </tbody>
          </table>
        </div>

        {(worstOverride > 0 || closestToL3) && (
        <div className="mb-6">
          <h3 className="text-sm font-semibold text-zinc-600 mb-2">What to act on</h3>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full text-sm"><tbody>
              {worstOverride > 0 && (<tr className="border-t">
                <td className="px-3 py-2.5"><Pill label={`Override ${worstOverride}%`} color="red" /></td>
                <td className="px-3 py-2.5 font-medium">{worstAgent}</td>
                <td className="px-3 py-2.5 text-zinc-500">Review overridden sessions</td>
              </tr>)}
              {closestToL3 && (<tr className="border-t">
                <td className="px-3 py-2.5"><Pill label="L3 candidate" color="green" /></td>
                <td className="px-3 py-2.5 font-medium">{closestToL3}</td>
                <td className="px-3 py-2.5 text-zinc-500">{bestApproval}% approval</td>
              </tr>)}
            </tbody></table>
          </div>
        </div>)}

        <h3 className="text-sm font-semibold text-zinc-600 mb-2">Safety Gates</h3>
        <div className="overflow-x-auto rounded-lg border mb-6">
          <table className="w-full text-sm"><tbody>
            <tr className="border-t"><td className="px-3 py-2.5">Unsafe actions executed</td><td className="px-3 py-2.5"><Pill label={String(f.safety.unsafe_executed)} color={f.safety.unsafe_executed === 0 ? "green" : "red"} /></td><td className="px-3 py-2.5 text-zinc-500">Must be 0 — each is a Sev1</td></tr>
            <tr className="border-t"><td className="px-3 py-2.5">Blast-radius blocks</td><td className="px-3 py-2.5"><Pill label={String(f.safety.blast_radius_blocked)} color={f.safety.blast_radius_blocked === 0 ? "green" : "yellow"} /></td><td className="px-3 py-2.5 text-zinc-500">Agent tried &gt;20 nodes — blocked</td></tr>
            <tr className="border-t"><td className="px-3 py-2.5">Blast-radius (avg / max nodes)</td><td className="px-3 py-2.5">{f.safety.blast_radius_avg ?? "—"} / {f.safety.blast_radius_max ?? "—"}</td><td className="px-3 py-2.5 text-zinc-500">Nodes per repair action</td></tr>
            <tr className="border-t"><td className="px-3 py-2.5">Circuit breaker trips</td><td className="px-3 py-2.5"><Pill label={String(f.safety.circuit_breaker_tripped)} color={f.safety.circuit_breaker_tripped === 0 ? "green" : "red"} /></td><td className="px-3 py-2.5 text-zinc-500">5 failures/1h — auto-disabled</td></tr>
            <tr className="border-t"><td className="px-3 py-2.5">Repair recurrence</td><td className="px-3 py-2.5"><Pill label={String(f.safety.repair_recurrence)} color={f.safety.repair_recurrence === 0 ? "green" : "yellow"} /></td><td className="px-3 py-2.5 text-zinc-500">Node came back broken after repair</td></tr>
            <tr className="border-t"><td className="px-3 py-2.5">False-positive RMAs</td><td className="px-3 py-2.5"><Pill label={String(f.safety.false_positive_rma)} color={f.safety.false_positive_rma === 0 ? "green" : "red"} /></td><td className="px-3 py-2.5 text-zinc-500">Sent to vendor but no fault found</td></tr>
          </tbody></table>
        </div>

        <h3 className="text-sm font-semibold text-zinc-600 mb-2">Monthly Trend</h3>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Metric</th>
              {data.monthly.map((m) => <th key={m.month} className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">{m.month}</th>)}
            </tr></thead>
            <tbody>
              <tr className="border-t"><td className="px-3 py-2.5">Nodes cordoned</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.nodes_cordoned ?? "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Resolved by agent</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center text-emerald-600">{m.resolved_by_agent}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Handled by human</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.nodes_cordoned != null ? Math.max(0, m.nodes_cordoned - m.resolved_by_agent) : "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Auto-resolve rate</td>{data.monthly.map((m) => {
                if (!m.nodes_cordoned) return <td key={m.month} className="px-3 py-2.5 text-center text-zinc-400">—</td>;
                const r = Math.round(m.resolved_by_agent / m.nodes_cordoned * 100);
                return <td key={m.month} className="px-3 py-2.5 text-center"><Pill label={`${r}%`} color={r >= 80 ? "green" : r >= 50 ? "yellow" : "red"} /></td>;
              })}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Escalation rate</td>{data.monthly.map((m) => {
                const r = m.total > 0 ? Math.round((m.total - m.completed) / m.total * 100) : 0;
                return <td key={m.month} className="px-3 py-2.5 text-center">{r}%</td>;
              })}</tr>
            </tbody>
          </table>
        </div>
      </>)}

      {/* ═══════ QUALITY ═══════ */}
      {view === "quality" && (<>
        <div className="overflow-x-auto rounded-lg border mb-6">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Agent</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Accuracy</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Tasks</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Success %</th>
            </tr></thead>
            <tbody>
              {agentList.map(([id, a]) => {
                const accTotal = Object.values(a.accuracy).reduce((s, v) => s + v, 0);
                const accCorrect = a.accuracy["correct"] ?? 0;
                const accPct = accTotal > 0 ? Math.round(accCorrect / accTotal * 100) : null;
                const successPct = a.tasks.total > 0 ? Math.round(a.tasks.completed / a.tasks.total * 100) : 0;
                return (<tr key={id} className="border-t hover:bg-zinc-50">
                  <td className="px-3 py-2.5 font-medium">{id}</td>
                  <td className="px-3 py-2.5 text-center">{accPct !== null ? <Pill label={`${accPct}%`} color={accPct >= 90 ? "green" : accPct >= 70 ? "yellow" : "red"} /> : <span className="text-zinc-400">—</span>}</td>
                  <td className="px-3 py-2.5 text-center">{a.tasks.total}</td>
                  <td className="px-3 py-2.5 text-center"><Pill label={`${successPct}%`} color={successPct >= 95 ? "green" : successPct >= 80 ? "yellow" : "red"} /></td>
                </tr>);
              })}
            </tbody>
          </table>
        </div>

        <h3 className="text-sm font-semibold text-zinc-600 mb-2">Monthly Trend</h3>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Metric</th>
              {data.monthly.map((m) => <th key={m.month} className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">{m.month}</th>)}
            </tr></thead>
            <tbody>
              <tr className="border-t"><td className="px-3 py-2.5">Incidents (hardware)</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.incidents_hw ?? "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Alert precision</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.alert_precision_pct != null ? <Pill label={`${m.alert_precision_pct}%`} color={m.alert_precision_pct >= 80 ? "green" : m.alert_precision_pct >= 60 ? "yellow" : "red"} /> : "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Rollback/recurrence</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.rollback_rate_pct != null ? <Pill label={`${m.rollback_rate_pct}%`} color={m.rollback_rate_pct <= 5 ? "green" : m.rollback_rate_pct <= 15 ? "yellow" : "red"} /> : "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">MTTD (detect → diagnose)</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{fmtDuration(m.avg_duration_s)}</td>)}</tr>
            </tbody>
          </table>
        </div>
      </>)}

      {/* ═══════ PERFORMANCE ═══════ */}
      {view === "performance" && (<>
        <div className="overflow-x-auto rounded-lg border mb-6">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Agent</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Tasks</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Success</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">p50</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">p90</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">p95</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">p99</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Error %</th>
            </tr></thead>
            <tbody>
              {agentList.map(([id, a]) => {
                const rate = a.tasks.total > 0 ? Math.round(a.tasks.completed / a.tasks.total * 100) : 0;
                const errPct = a.tasks.total > 0 ? Math.round(a.errors.total / a.tasks.total * 100) : 0;
                return (<tr key={id} className="border-t hover:bg-zinc-50">
                  <td className="px-3 py-2.5 font-medium">{id}</td>
                  <td className="px-3 py-2.5 text-center">{a.tasks.total} <span className="text-zinc-400 text-[10px]">✓{a.tasks.completed} ✗{a.tasks.failed}</span></td>
                  <td className="px-3 py-2.5 text-center"><Pill label={`${rate}%`} color={rate >= 95 ? "green" : rate >= 80 ? "yellow" : "red"} /></td>
                  <td className="px-3 py-2.5 text-right">{fmtDuration(a.duration_s.p50)}</td>
                  <td className="px-3 py-2.5 text-right">{fmtDuration(a.duration_s.p90)}</td>
                  <td className="px-3 py-2.5 text-right">{fmtDuration(a.duration_s.p95)}</td>
                  <td className="px-3 py-2.5 text-right">{fmtDuration(a.duration_s.p99)}</td>
                  <td className="px-3 py-2.5 text-center">
                    {errPct > 0 ? <span className="text-xs">{errPct}% <span className="text-zinc-400 text-[9px]">(tool:{a.errors.tool} llm:{a.errors.llm})</span></span> : <span className="text-zinc-400">0</span>}
                  </td>
                </tr>);
              })}
            </tbody>
          </table>
        </div>

        <h3 className="text-sm font-semibold text-zinc-600 mb-2">Monthly Trend</h3>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Metric</th>
              {data.monthly.map((m) => <th key={m.month} className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">{m.month}</th>)}
            </tr></thead>
            <tbody>
              <tr className="border-t"><td className="px-3 py-2.5">Tasks completed</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.completed}<span className="text-zinc-400 text-[10px]">/{m.total}</span></td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">MTTD (detect → diagnose)</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{fmtDuration(m.avg_duration_s)}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">MTTR p50 (detect → resolved)</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.mttr_p50_hours != null ? `${m.mttr_p50_hours}h` : "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">MTTR p95</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.mttr_p95_hours != null ? `${m.mttr_p95_hours}h` : "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Toil hours saved</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center text-emerald-600">{m.toil_hours_saved != null ? `${m.toil_hours_saved}h` : "—"}</td>)}</tr>
            </tbody>
          </table>
        </div>
      </>)}

      {/* ═══════ COST ═══════ */}
      {view === "cost" && (<>
        <div className="overflow-x-auto rounded-lg border mb-6">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Agent</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Tasks</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">$/Task</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">Total Cost</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">Input Tokens</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-zinc-500">Output Tokens</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">Trend</th>
            </tr></thead>
            <tbody>
              {agentList.map(([id, a]) => (
                <tr key={id} className="border-t hover:bg-zinc-50">
                  <td className="px-3 py-2.5 font-medium">{id}</td>
                  <td className="px-3 py-2.5 text-center">{a.tasks.total}</td>
                  <td className="px-3 py-2.5 text-right">{fmtCost2(a.cost.avg)}</td>
                  <td className="px-3 py-2.5 text-right">{fmtCost(a.cost.total)}</td>
                  <td className="px-3 py-2.5 text-right text-zinc-500">{a.cost.tokens_in > 0 ? `${(a.cost.tokens_in / 1e6).toFixed(1)}M` : "—"}</td>
                  <td className="px-3 py-2.5 text-right text-zinc-500">{a.cost.tokens_out > 0 ? `${(a.cost.tokens_out / 1e6).toFixed(1)}M` : "—"}</td>
                  <td className="px-3 py-2.5 text-center"><Trend pct={a.cost.trend_pct} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3 className="text-sm font-semibold text-zinc-600 mb-2">Monthly Trend</h3>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b"><tr>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-zinc-500">Metric</th>
              {data.monthly.map((m) => <th key={m.month} className="px-3 py-2.5 text-center text-xs font-semibold text-zinc-500">{m.month}</th>)}
            </tr></thead>
            <tbody>
              <tr className="border-t"><td className="px-3 py-2.5">Agent ops cost</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.agent_cost > 0 ? fmtCost(m.agent_cost) : "—"}</td>)}</tr>
              <tr className="border-t"><td className="px-3 py-2.5">Cost / incident</td>{data.monthly.map((m) => <td key={m.month} className="px-3 py-2.5 text-center">{m.incidents_hw && m.agent_cost > 0 ? fmtCost2(m.agent_cost / m.incidents_hw) : "—"}</td>)}</tr>
            </tbody>
          </table>
        </div>
      </>)}
    </div>
  );
}
