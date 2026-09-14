import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  fetchAgentUsage,
  backfillUsage,
  type AgentUsageResponse,
  type AgentUsageTask,
  type AgentModelSummary,
  type DailySpend,
} from "../api/usage";
import { modelBadgeClass, chartModelColor, legendDotClass } from "../lib/modelColors";

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

function parseChartDate(dateStr: string): Date {
  // Handle both "2026-05-15" and "2026-05-15T00:00:00.000Z" formats
  if (dateStr.includes("T")) return new Date(dateStr);
  return new Date(dateStr + "T00:00:00");
}

const STATUS_BADGE: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-700",
  error: "bg-red-100 text-red-700",
  running: "bg-blue-100 text-blue-700",
  waiting_input: "bg-amber-100 text-amber-700",
  interrupted: "bg-orange-100 text-orange-700",
  busy: "bg-blue-100 text-blue-700",
};

/** Stacked bar chart for daily spend split by model, with click-to-filter */
function DailySpendChart({ data, onDayClick, selectedDate }: { data: DailySpend[]; onDayClick: (date: string) => void; selectedDate?: string }) {
  if (data.length === 0) return null;

  const maxCost = Math.max(...data.map((d) => d.cost_usd), 0.01);
  const maxBarHeight = 120;

  // Collect all models across all days for the legend
  const allModels = Array.from(
    new Set(data.flatMap((d) => (d.by_model || []).map((m) => m.model)))
  );

  return (
    <div className="bg-white rounded-lg border border-zinc-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-zinc-500">Daily Spend</h3>
        {selectedDate && (
          <button
            onClick={() => onDayClick("")}
            className="text-[10px] px-2 py-0.5 rounded bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
          >
            ✕ Clear filter ({selectedDate})
          </button>
        )}
      </div>

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

      <div className="flex items-end gap-[3px] overflow-x-auto" style={{ minHeight: maxBarHeight + 30 }}>
        {data.map((d) => {
          const totalH = Math.max(2, (d.cost_usd / maxCost) * maxBarHeight);
          const isSelected = selectedDate === d.date;
          const modelParts = (d.by_model || []).filter((m) => m.cost_usd > 0);
          // If no model breakdown, use single bar
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
              {/* Tooltip */}
              <div className="absolute bottom-full mb-1 hidden group-hover:block z-10 bg-zinc-800 text-white text-[10px] rounded px-2 py-1 whitespace-nowrap pointer-events-none">
                <div className="font-medium">{parseChartDate(d.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</div>
                <div className="font-medium">{formatCost(d.cost_usd)}</div>
                {(d.by_model || []).map((m) => (
                  <div key={m.model} className="text-zinc-400">
                    {m.model.replace("claude-", "")}: {formatCost(m.cost_usd)}
                  </div>
                ))}
                <div className="text-zinc-500 mt-0.5">Click to filter</div>
              </div>
              {/* Stacked bar */}
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
                <div className={`text-[9px] mt-1 whitespace-nowrap ${isSelected ? "text-blue-600 font-medium" : "text-zinc-400"}`}>
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

/** Model breakdown table with full token split */
function ModelBreakdownTable({ models, totalCost }: { models: AgentModelSummary[]; totalCost: number }) {
  if (models.length === 0) return null;

  return (
    <div className="bg-white rounded-lg border border-zinc-200 p-4">
      <h3 className="text-xs font-semibold text-zinc-500 mb-2">Model Breakdown</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-400 border-b border-zinc-100">
              <th className="text-left font-medium py-1.5 pr-3">Model</th>
              <th className="text-right font-medium py-1.5 px-2">Input</th>
              <th className="text-right font-medium py-1.5 px-2">Output</th>
              <th className="text-right font-medium py-1.5 px-2">Cache Write</th>
              <th className="text-right font-medium py-1.5 px-2">Cache Read</th>
              <th className="text-right font-medium py-1.5 px-2">Cost</th>
              <th className="text-right font-medium py-1.5 px-1">%</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.model} className="border-b border-zinc-50">
                <td className="py-1.5 pr-3">
                  <span className={`${modelBadgeClass(m.model)} px-1.5 py-0.5 rounded font-medium`}>
                    {m.model.replace("claude-", "")}
                  </span>
                </td>
                <td className="text-right text-zinc-600 px-2">{formatTokens(m.input_tokens)}</td>
                <td className="text-right text-zinc-600 px-2">{formatTokens(m.output_tokens)}</td>
                <td className="text-right text-zinc-600 px-2">{formatTokens(m.cache_creation_input_tokens)}</td>
                <td className="text-right text-zinc-600 px-2">{formatTokens(m.cache_read_input_tokens)}</td>
                <td className="text-right font-medium px-2">{formatCost(m.cost_usd)}</td>
                <td className="text-right text-zinc-400 px-1">
                  {totalCost > 0 ? ((m.cost_usd / totalCost) * 100).toFixed(0) : 0}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface Props {
  agentId: string;
}

type TimeRange = "7d" | "30d" | "all" | "custom";

export function AgentUsageTab({ agentId }: Props) {
  const [timeRange, setTimeRange] = useState<TimeRange>("7d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [selectedDay, setSelectedDay] = useState(""); // "2026-05-18" when a bar is clicked
  const [data, setData] = useState<AgentUsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("total_cost_usd");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [backfilling, setBackfilling] = useState(false);

  const handleDayClick = useCallback((date: string) => {
    setSelectedDay(date);
    setPage(1);
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

    if (timeRange === "7d") {
      startDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString();
    } else if (timeRange === "30d") {
      startDate = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString();
    } else if (timeRange === "custom") {
      startDate = customStart ? new Date(customStart).toISOString() : undefined;
      endDate = customEnd ? new Date(customEnd + "T23:59:59").toISOString() : undefined;
    }
    return { startDate, endDate };
  }, [timeRange, customStart, customEnd, selectedDay]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { startDate, endDate } = getDateRange();
      const res = await fetchAgentUsage(agentId, {
        startDate,
        endDate,
        page,
        pageSize: 20,
        search: search || undefined,
        sortBy,
        sortDir,
      });
      setData(res);
    } catch (err: any) {
      setError(err.message || "Failed to load usage");
    } finally {
      setLoading(false);
    }
  }, [agentId, page, search, sortBy, sortDir, getDateRange]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    setPage(1);
    setSelectedDay("");
  }, [timeRange, search, sortBy, sortDir]);

  const handleBackfill = async () => {
    setBackfilling(true);
    try {
      const result = await backfillUsage({ agentId, force: true });
      const r = result.results?.[0];
      if (r) {
        alert(`Backfilled ${r.sessions} sessions, ${r.errors} errors`);
      }
      loadData();
    } catch (err: any) {
      alert("Backfill failed: " + err.message);
    } finally {
      setBackfilling(false);
    }
  };

  const summary = data?.summary;
  const tasks = data?.tasks || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / 20));
  const avgPerTask = summary && summary.total_tasks > 0 ? summary.total_cost_usd / summary.total_tasks : 0;

  return (
    <div className="space-y-4">
      {/* Time range + backfill */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {(["7d", "30d", "all", "custom"] as TimeRange[]).map((tr) => (
            <button
              key={tr}
              onClick={() => setTimeRange(tr)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
                timeRange === tr
                  ? "bg-zinc-900 text-white border-zinc-900"
                  : "bg-white text-zinc-600 border-zinc-200 hover:bg-zinc-50"
              }`}
            >
              {tr === "7d" ? "Last 7 days" : tr === "30d" ? "Last 30 days" : tr === "all" ? "All time" : "Custom"}
            </button>
          ))}
          {timeRange === "custom" && (
            <div className="flex items-center gap-2 ml-2">
              <input
                type="date"
                value={customStart}
                onChange={(e) => setCustomStart(e.target.value)}
                className="text-xs px-2 py-1 border border-zinc-200 rounded-md"
              />
              <span className="text-xs text-zinc-400">to</span>
              <input
                type="date"
                value={customEnd}
                onChange={(e) => setCustomEnd(e.target.value)}
                className="text-xs px-2 py-1 border border-zinc-200 rounded-md"
              />
            </div>
          )}
        </div>
        <button
          onClick={handleBackfill}
          disabled={backfilling}
          className="px-3 py-1.5 text-xs font-medium rounded-md border border-zinc-200 bg-white hover:bg-zinc-50 text-zinc-600 disabled:opacity-50"
        >
          {backfilling ? "Backfilling…" : "↻ Backfill"}
        </button>
      </div>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <div className="bg-white rounded-lg border border-zinc-200 p-3">
            <div className="text-xs text-zinc-400">Total Cost</div>
            <div className="text-lg font-bold text-zinc-900">{formatCost(summary.total_cost_usd)}</div>
          </div>
          <div className="bg-white rounded-lg border border-zinc-200 p-3">
            <div className="text-xs text-zinc-400">Avg / Task</div>
            <div className="text-lg font-bold text-zinc-900">{formatCost(avgPerTask)}</div>
          </div>
          <div className="bg-white rounded-lg border border-zinc-200 p-3">
            <div className="text-xs text-zinc-400">Total Tokens</div>
            <div className="text-lg font-bold text-zinc-900">{formatTokens(summary.total_tokens)}</div>
            <div className="text-[10px] text-zinc-400 mt-0.5">
              in:{formatTokens(summary.total_input_tokens)} out:{formatTokens(summary.total_output_tokens)} cw:{formatTokens(summary.total_cache_creation_input_tokens)} cr:{formatTokens(summary.total_cache_read_input_tokens)}
            </div>
          </div>
          <div className="bg-white rounded-lg border border-zinc-200 p-3">
            <div className="text-xs text-zinc-400">Total Turns</div>
            <div className="text-lg font-bold text-zinc-900">{summary.total_turns.toLocaleString()}</div>
          </div>
          <div className="bg-white rounded-lg border border-zinc-200 p-3">
            <div className="text-xs text-zinc-400">Tasks</div>
            <div className="text-lg font-bold text-zinc-900">{summary.total_tasks.toLocaleString()}</div>
          </div>
        </div>
      )}

      {/* Daily spend chart */}
      {summary && summary.daily_spend && summary.daily_spend.length > 0 && (
        <DailySpendChart data={summary.daily_spend} onDayClick={handleDayClick} selectedDate={selectedDay} />
      )}

      {/* Model breakdown with token split */}
      {summary && summary.model_usage && summary.model_usage.length > 0 && (
        <ModelBreakdownTable models={summary.model_usage} totalCost={summary.total_cost_usd} />
      )}

      {/* Search + sort */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Search tasks…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="text-xs px-2.5 py-1.5 border border-zinc-200 rounded-md w-48 focus:outline-none focus:ring-1 focus:ring-zinc-400"
        />
        <select
          value={`${sortBy}-${sortDir}`}
          onChange={(e) => {
            const [by, dir] = e.target.value.split("-");
            setSortBy(by);
            setSortDir(dir as "asc" | "desc");
          }}
          className="text-xs px-2 py-1.5 border border-zinc-200 rounded-md text-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-400"
        >
          <option value="total_cost_usd-desc">Cost ↓</option>
          <option value="total_cost_usd-asc">Cost ↑</option>
          <option value="total_tokens-desc">Tokens ↓</option>
          <option value="created_at-desc">Newest</option>
          <option value="created_at-asc">Oldest</option>
        </select>
      </div>

      {/* Task table */}
      <div className="bg-white rounded-lg border border-zinc-200 overflow-hidden">
        {loading ? (
          <div className="py-12 text-center text-sm text-zinc-400">Loading…</div>
        ) : error ? (
          <div className="py-8 text-center text-sm text-red-500">{error}</div>
        ) : tasks.length === 0 ? (
          <div className="py-12 text-center text-sm text-zinc-400">
            No usage data. Click "Backfill" to load from gateway.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-100 text-xs text-zinc-500">
                  <th className="text-left px-4 py-2 font-medium">Task</th>
                  <th className="text-left px-3 py-2 font-medium">Status</th>
                  <th className="text-left px-3 py-2 font-medium">Models</th>
                  <th className="text-right px-3 py-2 font-medium">Input</th>
                  <th className="text-right px-3 py-2 font-medium">Output</th>
                  <th className="text-right px-3 py-2 font-medium">Cache W</th>
                  <th className="text-right px-3 py-2 font-medium">Cache R</th>
                  <th className="text-right px-3 py-2 font-medium">Cost</th>
                  <th className="text-left px-3 py-2 font-medium">Date</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t: AgentUsageTask) => (
                  <tr key={t.task_id} className="border-b border-zinc-50 hover:bg-zinc-50">
                    <td className="px-4 py-2.5">
                      <Link
                        to={`/sessions/${t.session_id}#task-${t.task_id}`}
                        className="text-blue-600 hover:underline text-xs"
                      >
                        #{t.task_id}
                      </Link>
                      <p className="text-xs text-zinc-500 truncate max-w-[200px]">
                        {t.title || t.prompt}
                      </p>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${STATUS_BADGE[t.status] || "bg-zinc-100 text-zinc-600"}`}>
                        {t.status}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex gap-1 flex-wrap">
                        {t.models.map((m) => (
                          <span key={m} className={`text-[10px] px-1 py-0.5 rounded font-medium ${modelBadgeClass(m)}`}>
                            {m.replace("claude-", "")}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="text-right px-3 py-2.5 text-zinc-600 text-xs">{formatTokens(t.total_input_tokens)}</td>
                    <td className="text-right px-3 py-2.5 text-zinc-600 text-xs">{formatTokens(t.total_output_tokens)}</td>
                    <td className="text-right px-3 py-2.5 text-zinc-600 text-xs">{formatTokens(t.total_cache_creation_input_tokens)}</td>
                    <td className="text-right px-3 py-2.5 text-zinc-600 text-xs">{formatTokens(t.total_cache_read_input_tokens)}</td>
                    <td className="text-right px-3 py-2.5 font-semibold text-xs">{formatCost(t.total_cost_usd)}</td>
                    <td className="px-3 py-2.5 text-zinc-500 text-xs">
                      {new Date(t.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-4 py-3 border-t border-zinc-100 flex items-center justify-between">
            <span className="text-xs text-zinc-500">
              Showing {((page - 1) * 20) + 1}–{Math.min(page * 20, total)} of {total} tasks
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="w-7 h-7 flex items-center justify-center rounded text-xs text-zinc-600 hover:bg-zinc-100 disabled:opacity-30"
              >
                ‹
              </button>
              <span className="text-xs text-zinc-600">Page {page} of {totalPages}</span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="w-7 h-7 flex items-center justify-center rounded text-xs text-zinc-600 hover:bg-zinc-100 disabled:opacity-30"
              >
                ›
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
