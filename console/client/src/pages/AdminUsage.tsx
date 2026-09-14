import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  fetchUsageOverview,
  backfillUsage,
  type OverviewResponse,
  type OverviewAgentSummary,
  type OverviewModelSummary,
} from "../api/usage";
import { modelBadgeClass, chartModelColor, legendDotClass } from "../lib/modelColors";
import { useMe } from "../hooks/useMe";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function formatCost(usd: number): string {
  if (usd >= 1) return "$" + usd.toFixed(2);
  if (usd >= 0.01) return "$" + usd.toFixed(3);
  if (usd > 0) return "$" + usd.toFixed(4);
  return "$0.00";
}

const AGENT_COLORS: Record<string, string> = {
  "feedback": "bg-emerald-500",
  "triage-unknown": "bg-blue-500",
  "repair": "bg-amber-500",
  "recycler": "bg-violet-500",
  "repair-draft": "bg-rose-400",
  "test-agent": "bg-zinc-400",
};

function parseChartDate(dateStr: string): Date {
  if (dateStr.includes("T")) return new Date(dateStr);
  return new Date(dateStr + "T00:00:00");
}

function DailyChart({ data, onDayClick, selectedDate }: { data: { date: string; cost_usd: number; by_model: { model: string; cost_usd: number }[] }[]; onDayClick: (date: string) => void; selectedDate?: string }) {
  if (data.length === 0) return null;
  const maxCost = Math.max(...data.map((d) => d.cost_usd), 0.01);
  const maxH = 100;

  const allModels = Array.from(
    new Set(data.flatMap((d) => (d.by_model || []).map((m) => m.model)))
  );

  return (
    <div>
      {/* Legend */}
      {allModels.length > 1 && (
        <div className="flex gap-3 mb-2 flex-wrap">
          {allModels.map((model) => (
            <div key={model} className="flex items-center gap-1">
              <span className={legendDotClass(model)} />
              <span className="text-[10px] text-zinc-500">{model.replace("claude-", "")}</span>
            </div>
          ))}
        </div>
      )}
      <div className="flex items-end gap-[3px] overflow-x-auto" style={{ minHeight: maxH + 28 }}>
        {data.map((d) => {
          const totalH = Math.max(2, (d.cost_usd / maxCost) * maxH);
          const isSelected = selectedDate === d.date;
          const modelParts = (d.by_model || []).filter((m) => m.cost_usd > 0);
          const parts = modelParts.length > 0
            ? modelParts
            : [{ model: "_total", cost_usd: d.cost_usd }];

          return (
            <div
              key={d.date}
              className="flex flex-col items-center flex-shrink-0 group relative cursor-pointer"
              style={{ minWidth: data.length > 30 ? 8 : 20 }}
              onClick={() => onDayClick(isSelected ? "" : d.date)}
            >
              <div className="absolute bottom-full mb-1 hidden group-hover:block z-10 bg-zinc-800 text-white text-[10px] rounded px-2 py-1 whitespace-nowrap pointer-events-none">
                <div className="font-medium">{parseChartDate(d.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}: {formatCost(d.cost_usd)}</div>
                {(d.by_model || []).map((m) => (
                  <div key={m.model} className="text-zinc-400">
                    {m.model.replace("claude-", "")}: {formatCost(m.cost_usd)}
                  </div>
                ))}
                <div className="text-zinc-500 mt-0.5">Click to filter</div>
              </div>
              <div
                className={`w-full flex flex-col-reverse rounded-t overflow-hidden transition-all ${isSelected ? "ring-2 ring-blue-400 ring-offset-1" : ""}`}
                style={{ height: totalH }}
              >
                {parts.map((p, i) => {
                  const segH = d.cost_usd > 0 ? Math.max(1, (p.cost_usd / d.cost_usd) * totalH) : 0;
                  return (
                    <div
                      key={p.model}
                      className={`${chartModelColor(p.model)} ${i === parts.length - 1 ? "rounded-t-sm" : ""}`}
                      style={{ height: segH }}
                    />
                  );
                })}
              </div>
              {data.length <= 31 && (
                <div className={`text-[9px] mt-1 ${isSelected ? "text-blue-600 font-medium" : "text-zinc-400"}`}>
                  {parseChartDate(d.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

type TimeRange = "7d" | "30d" | "all" | "custom";

interface AdminUsageProps {
  embedded?: boolean;
}

export function AdminUsage({ embedded }: AdminUsageProps) {
  const { is_admin } = useMe();
  const [timeRange, setTimeRange] = useState<TimeRange>("7d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [selectedDay, setSelectedDay] = useState("");
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [backfilling, setBackfilling] = useState(false);

  const handleDayClick = useCallback((date: string) => {
    setSelectedDay(date);
  }, []);

  const getDateRange = useCallback(() => {
    // If a specific day is selected, filter to that day
    if (selectedDay) {
      return {
        startDate: new Date(selectedDay + "T00:00:00Z").toISOString(),
        endDate: new Date(selectedDay + "T23:59:59Z").toISOString(),
      };
    }

    const now = new Date();
    let startDate: string | undefined;
    let endDate: string | undefined;
    if (timeRange === "7d") startDate = new Date(now.getTime() - 7 * 86400000).toISOString();
    else if (timeRange === "30d") startDate = new Date(now.getTime() - 30 * 86400000).toISOString();
    else if (timeRange === "custom") {
      startDate = customStart ? new Date(customStart).toISOString() : undefined;
      endDate = customEnd ? new Date(customEnd + "T23:59:59").toISOString() : undefined;
    }
    return { startDate, endDate };
  }, [timeRange, customStart, customEnd, selectedDay]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const { startDate, endDate } = getDateRange();
      setData(await fetchUsageOverview({ startDate, endDate }));
    } catch (err: any) {
      console.error("Failed to load usage overview:", err);
    } finally {
      setLoading(false);
    }
  }, [getDateRange]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleBackfill = async () => {
    setBackfilling(true);
    try {
      const result = await backfillUsage({ force: true });
      const total = result.results?.reduce((s, r) => s + r.sessions, 0) ?? 0;
      alert(`Backfilled ${total} sessions across all agents`);
      loadData();
    } catch (err: any) {
      alert("Backfill failed: " + err.message);
    } finally {
      setBackfilling(false);
    }
  };

  const agents = data?.agents || [];
  const models = data?.models || [];
  const daily = data?.daily_spend || [];
  const totalCost = data?.total_cost_usd ?? 0;

  if (!is_admin) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-zinc-500">Admin access required</p>
      </div>
    );
  }

  return (
    <div className={embedded ? "space-y-6" : "p-6 space-y-6"} style={embedded ? {} : { maxWidth: 1400 }}>
      {!embedded && <h1 className="text-xl font-semibold text-zinc-900">Admin — Usage Overview</h1>}

      {/* Time range + backfill */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {(["7d", "30d", "all", "custom"] as TimeRange[]).map((tr) => (
            <button
              key={tr}
              onClick={() => { setTimeRange(tr); setSelectedDay(""); }}
              className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
                timeRange === tr ? "bg-zinc-900 text-white border-zinc-900" : "bg-white text-zinc-600 border-zinc-200 hover:bg-zinc-50"
              }`}
            >
              {tr === "7d" ? "Last 7 days" : tr === "30d" ? "Last 30 days" : tr === "all" ? "All time" : "Custom"}
            </button>
          ))}
          {timeRange === "custom" && (
            <div className="flex items-center gap-2 ml-2">
              <input type="date" value={customStart} onChange={(e) => setCustomStart(e.target.value)} className="text-xs px-2 py-1 border border-zinc-200 rounded-md" />
              <span className="text-xs text-zinc-400">→</span>
              <input type="date" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} className="text-xs px-2 py-1 border border-zinc-200 rounded-md" />
            </div>
          )}
        </div>
        <button onClick={handleBackfill} disabled={backfilling} className="px-3 py-1.5 text-xs font-medium rounded-md bg-zinc-900 text-white hover:bg-zinc-700 disabled:opacity-50">
          {backfilling ? "Backfilling…" : "↻ Backfill All"}
        </button>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-zinc-400">Loading…</div>
      ) : !data ? (
        <div className="py-12 text-center text-sm text-red-500">Failed to load</div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-white rounded-lg border border-zinc-200 p-4">
              <p className="text-xs text-zinc-500 mb-1">Total Spend</p>
              <p className="text-2xl font-bold text-zinc-900">{formatCost(data.total_cost_usd)}</p>
            </div>
            <div className="bg-white rounded-lg border border-zinc-200 p-4">
              <p className="text-xs text-zinc-500 mb-1">Total Tokens</p>
              <p className="text-2xl font-bold text-zinc-900">{formatTokens(data.total_tokens)}</p>
            </div>
            <div className="bg-white rounded-lg border border-zinc-200 p-4">
              <p className="text-xs text-zinc-500 mb-1">Total Tasks</p>
              <p className="text-2xl font-bold text-zinc-900">{data.total_tasks.toLocaleString()}</p>
            </div>
            <div className="bg-white rounded-lg border border-zinc-200 p-4">
              <p className="text-xs text-zinc-500 mb-1">Total Turns</p>
              <p className="text-2xl font-bold text-zinc-900">{data.total_turns.toLocaleString()}</p>
            </div>
          </div>

          {/* Daily chart */}
          {daily.length > 0 && (
            <div className="bg-white rounded-lg border border-zinc-200 p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-zinc-700">Daily Spend</h3>
                {selectedDay && (
                  <button
                    onClick={() => handleDayClick("")}
                    className="text-[10px] px-2 py-0.5 rounded bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
                  >
                    ✕ Clear filter ({selectedDay})
                  </button>
                )}
              </div>
              <DailyChart data={daily} onDayClick={handleDayClick} selectedDate={selectedDay} />
            </div>
          )}

          {/* Per-agent table */}
          <div className="bg-white rounded-lg border border-zinc-200 overflow-hidden">
            <div className="px-4 py-3 border-b border-zinc-100">
              <h3 className="text-sm font-semibold text-zinc-700">Per-Agent Breakdown</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-100 text-xs text-zinc-500">
                    <th className="text-left px-4 py-2 font-medium">Agent</th>
                    <th className="text-right px-4 py-2 font-medium">Tasks</th>
                    <th className="text-right px-4 py-2 font-medium">Turns</th>
                    <th className="text-right px-4 py-2 font-medium">Tokens</th>
                    <th className="text-right px-4 py-2 font-medium">Avg / Task</th>
                    <th className="text-right px-4 py-2 font-medium">Cost</th>
                    <th className="text-right px-4 py-2 font-medium">% of Total</th>
                  </tr>
                </thead>
                <tbody>
                  {agents.map((a: OverviewAgentSummary) => {
                    const avgPerTask = a.total_tasks > 0 ? a.total_cost_usd / a.total_tasks : 0;
                    const pct = totalCost > 0 ? (a.total_cost_usd / totalCost) * 100 : 0;
                    return (
                      <tr key={a.agent_id} className="border-b border-zinc-50 hover:bg-zinc-50">
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className={`w-2 h-2 rounded-full ${AGENT_COLORS[a.agent_id] || "bg-zinc-300"}`} />
                            <Link to={`/agents/${a.agent_id}?tab=usage`} className="font-medium text-blue-600 hover:underline">
                              {a.agent_id}
                            </Link>
                          </div>
                        </td>
                        <td className="text-right px-4 py-2.5 text-zinc-600">{a.total_tasks.toLocaleString()}</td>
                        <td className="text-right px-4 py-2.5 text-zinc-600">{a.total_turns.toLocaleString()}</td>
                        <td className="text-right px-4 py-2.5 font-medium">{formatTokens(a.total_tokens)}</td>
                        <td className="text-right px-4 py-2.5 text-zinc-600">{formatCost(avgPerTask)}</td>
                        <td className="text-right px-4 py-2.5 font-semibold text-zinc-900">{formatCost(a.total_cost_usd)}</td>
                        <td className="text-right px-4 py-2.5">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-16 h-1.5 bg-zinc-100 rounded-full overflow-hidden">
                              <div className="h-full bg-blue-500 rounded-full" style={{ width: `${Math.min(pct, 100)}%` }} />
                            </div>
                            <span className="text-xs text-zinc-500">{pct.toFixed(0)}%</span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {/* Total row */}
                  <tr className="bg-zinc-50 font-medium">
                    <td className="px-4 py-2.5 text-zinc-900">Total</td>
                    <td className="text-right px-4 py-2.5 text-zinc-700">{data.total_tasks.toLocaleString()}</td>
                    <td className="text-right px-4 py-2.5 text-zinc-700">{data.total_turns.toLocaleString()}</td>
                    <td className="text-right px-4 py-2.5 text-zinc-900 font-bold">{formatTokens(data.total_tokens)}</td>
                    <td className="text-right px-4 py-2.5 text-zinc-700">{formatCost(data.total_tasks > 0 ? data.total_cost_usd / data.total_tasks : 0)}</td>
                    <td className="text-right px-4 py-2.5 font-bold text-zinc-900">{formatCost(data.total_cost_usd)}</td>
                    <td className="text-right px-4 py-2.5 text-zinc-500">100%</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Cross-agent model breakdown */}
          {models.length > 0 && (
            <div className="bg-white rounded-lg border border-zinc-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-zinc-100">
                <h3 className="text-sm font-semibold text-zinc-700">By Model (All Agents)</h3>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-zinc-100 text-xs text-zinc-500">
                      <th className="text-left px-4 py-2 font-medium">Model</th>
                      <th className="text-right px-4 py-2 font-medium">Input</th>
                      <th className="text-right px-4 py-2 font-medium">Output</th>
                      <th className="text-right px-4 py-2 font-medium">Cache Write</th>
                      <th className="text-right px-4 py-2 font-medium">Cache Read</th>
                      <th className="text-right px-4 py-2 font-medium">Total Tokens</th>
                      <th className="text-right px-4 py-2 font-medium">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.map((m: OverviewModelSummary) => (
                      <tr key={m.model} className="border-b border-zinc-50">
                        <td className="px-4 py-2.5">
                          <span className={`${modelBadgeClass(m.model)} px-1.5 py-0.5 rounded font-medium text-xs`}>
                            {m.model.replace("claude-", "")}
                          </span>
                        </td>
                        <td className="text-right px-4 py-2.5 text-zinc-500 text-xs">{formatTokens(m.input_tokens)}</td>
                        <td className="text-right px-4 py-2.5 text-zinc-500 text-xs">{formatTokens(m.output_tokens)}</td>
                        <td className="text-right px-4 py-2.5 text-zinc-500 text-xs">{formatTokens(m.cache_creation_input_tokens)}</td>
                        <td className="text-right px-4 py-2.5 text-zinc-500 text-xs">{formatTokens(m.cache_read_input_tokens)}</td>
                        <td className="text-right px-4 py-2.5 font-medium text-xs">{formatTokens(m.tokens)}</td>
                        <td className="text-right px-4 py-2.5 font-semibold text-xs">{formatCost(m.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
