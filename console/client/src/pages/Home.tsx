import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAgents } from "../hooks/useAgents.ts";
import { useMe } from "../hooks/useMe.ts";
import { useUIState } from "../lib/store.ts";
import { listTasks } from "../api/tasks.ts";
import { fetchUsageOverview, fetchAgentUsage, backfillUsage } from "../api/usage.ts";
import type { DailySpend } from "../api/usage.ts";
import { DASHBOARDS } from "../lib/dashboards.ts";

/* ─── Chip (pill) colors matching the proposal ─── */
const chip: Record<string, string> = {
  emerald: "text-emerald-600 bg-emerald-50",
  amber:   "text-amber-600 bg-amber-50",
  blue:    "text-blue-600 bg-blue-50",
  violet:  "text-violet-600 bg-violet-50",
  rose:    "text-rose-600 bg-rose-50",
  zinc:    "text-stone-500 bg-stone-100",
};

/* ─── Auto-assigned color palette ─── */
const PALETTE = [
  { iconBg: "bg-blue-50",    light: "bg-blue-200",    mid: "bg-blue-300",    dark: "bg-blue-400" },
  { iconBg: "bg-violet-50",  light: "bg-violet-200",  mid: "bg-violet-300",  dark: "bg-violet-400" },
  { iconBg: "bg-amber-50",   light: "bg-amber-200",   mid: "bg-amber-300",   dark: "bg-amber-400" },
  { iconBg: "bg-emerald-50", light: "bg-emerald-200", mid: "bg-emerald-300", dark: "bg-emerald-400" },
  { iconBg: "bg-rose-50",    light: "bg-rose-200",    mid: "bg-rose-300",    dark: "bg-rose-400" },
  { iconBg: "bg-sky-50",     light: "bg-sky-200",     mid: "bg-sky-300",     dark: "bg-sky-400" },
  { iconBg: "bg-teal-50",    light: "bg-teal-200",    mid: "bg-teal-300",    dark: "bg-teal-400" },
  { iconBg: "bg-orange-50",  light: "bg-orange-200",  mid: "bg-orange-300",  dark: "bg-orange-400" },
];

const AGENT_EMOJIS = ["🔍", "📊", "🔧", "♻️", "📝", "🧪", "🛡️", "📡", "🧠", "⚙️"];

function agentEmoji(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AGENT_EMOJIS[Math.abs(hash) % AGENT_EMOJIS.length];
}

/* ─── Time helpers ─── */
function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

/** Get last 7 day keys (YYYY-MM-DD) ending today */
function last7Days(): string[] {
  const days: string[] = [];
  const now = new Date();
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

/** Group tasks by day for sparkline */
/* ─── Signal card (clickable) ─── */
function SignalCard({ label, value, chip: chipLabel, chipColor, onClick }: {
  label: string; value: number | string; chip?: string; chipColor?: string; onClick?: () => void;
}) {
  return (
    <div
      className={`bg-white rounded-2xl border border-zinc-200/80 p-4 transition-colors ${onClick ? "cursor-pointer hover:border-zinc-300 hover:shadow-sm" : ""}`}
      onClick={onClick}
    >
      <div className="text-[11px] uppercase tracking-wider font-semibold text-zinc-400">{label}</div>
      <div className="mt-2 text-[28px] font-bold leading-none text-zinc-900">{value}</div>
      {chipLabel && (
        <div className="mt-2">
          <span className={`inline-flex items-center text-[11px] font-semibold px-2 py-0.5 rounded-full ${chip[chipColor ?? "zinc"]}`}>
            {chipLabel}
          </span>
        </div>
      )}
    </div>
  );
}

/* ─── Activity icon ─── */
function ActivityIcon({ status }: { status: string }) {
  if (status === "completed") return <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] ${chip.emerald}`}>✓</span>;
  if (status === "running" || status === "busy") return <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] ${chip.blue}`}>▶</span>;
  if (status === "error") return <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] ${chip.rose}`}>✗</span>;
  if (status === "waiting_input") return <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] ${chip.amber}`}>💬</span>;
  return <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] ${chip.zinc}`}>·</span>;
}

/** Per-agent sparkline: 7 bars showing daily activity (token usage) */
function AgentSparkline({ counts, colorIdx, hasActivity }: {
  counts: number[]; colorIdx: number; hasActivity: boolean;
}) {
  const max = Math.max(...counts, 1);
  const c = PALETTE[colorIdx % PALETTE.length];
  return (
    <div className="flex items-end gap-[2px] h-8 mb-3">
      {counts.map((count, i) => {
        const pct = count > 0 ? Math.max(15, (count / max) * 100) : (hasActivity ? 5 : 2);
        const cls = i < 2 ? c.light : i < 5 ? c.mid : c.dark;
        return <div key={i} className={`flex-1 rounded-sm ${cls}`} style={{ height: `${pct}%` }} />;
      })}
    </div>
  );
}

/** Tokens Today mini chart — 7 emerald bars with Mon–Sun labels */
function TokensTodayCard() {
  // Calculate 7-day date range
  const endDate = new Date().toISOString().slice(0, 10);
  const startDate = new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10);

  const queryClient = useQueryClient();
  const { data, refetch } = useQuery({
    queryKey: ["usage-overview", startDate, endDate],
    queryFn: () => fetchUsageOverview({ startDate, endDate }),
    staleTime: 60_000,
  });

  const [backfilling, setBackfilling] = useState(false);

  const handleBackfill = async () => {
    setBackfilling(true);
    try {
      await backfillUsage({});
      await refetch();
      queryClient.invalidateQueries({ queryKey: ["usage-overview"] });
      queryClient.invalidateQueries({ queryKey: ["agent-sparklines"] });
    } finally {
      setBackfilling(false);
    }
  };

  const rawDaily = data?.daily_spend ?? [];
  if (rawDaily.length === 0) {
    return (
      <div className="bg-white rounded-2xl border border-zinc-200/80 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-zinc-700">Tokens Today</h3>
          <span className="text-xs text-zinc-400">No data</span>
        </div>
        <button
          onClick={handleBackfill}
          disabled={backfilling}
          className="w-full text-xs text-blue-500 hover:text-blue-700 hover:bg-blue-50 py-2 rounded-lg transition-colors disabled:opacity-50"
        >
          {backfilling ? "Syncing…" : "↻ Sync usage data"}
        </button>
      </div>
    );
  }

  // Build a map of date → spend, then fill all 7 days
  const spendMap = new Map(rawDaily.map(d => [d.date, d]));
  const days: string[] = [];
  for (let i = 6; i >= 0; i--) {
    days.push(new Date(Date.now() - i * 86400000).toISOString().slice(0, 10));
  }
  const dailySpend = days.map(d => spendMap.get(d) ?? { date: d, cost_usd: 0 });

  // Today's tokens from the daily entry
  const todaySpend = spendMap.get(endDate);
  const todayTokens = todaySpend
    ? todaySpend.input_tokens + todaySpend.output_tokens + todaySpend.cache_creation_input_tokens + todaySpend.cache_read_input_tokens
    : 0;

  const maxCost = Math.max(...dailySpend.map(d => d.cost_usd), 0.01);
  const today = endDate;

  return (
    <div className="bg-white rounded-2xl border border-zinc-200/80 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-zinc-700">Tokens Today</h3>
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-bold text-zinc-900">{todayTokens > 0 ? formatTokens(todayTokens) : "—"}</span>
          <button
            onClick={handleBackfill}
            disabled={backfilling}
            className="text-zinc-400 hover:text-zinc-600 disabled:opacity-50 transition-colors"
            title="Sync latest usage data"
          >
            {backfilling ? "⏳" : "↻"}
          </button>
        </div>
      </div>
      <div className="flex items-end gap-[2px] h-10">
        {dailySpend.map((d, i) => {
          const pct = d.cost_usd > 0 ? Math.max(4, (d.cost_usd / maxCost) * 100) : 4;
          const cls = i < 2 ? "bg-emerald-200" : i < 4 ? "bg-emerald-300" : i < 6 ? "bg-emerald-400" : "bg-emerald-500";
          return <div key={d.date} className={`flex-1 rounded-sm ${cls}`} style={{ height: `${pct}%` }} />;
        })}
      </div>
      <div className="flex justify-between mt-1.5">
        {dailySpend.map(d => {
          const date = new Date(d.date + "T00:00:00");
          const isToday = d.date === today;
          return (
            <span key={d.date} className={`text-[9px] ${isToday ? "text-zinc-900 font-medium" : "text-zinc-400"}`}>
              {date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
            </span>
          );
        })}
      </div>
      <p className="text-[10px] text-zinc-400 mt-2">
        Last 7 days · <a href="/admin/usage" className="text-blue-500 hover:underline">See usage →</a>
      </p>
    </div>
  );
}

/* ─── Main Home component ─── */
export function Home() {
  const { agents, isLoading } = useAgents();
  const { user, agent_levels, is_admin, dashboard_access } = useMe();

  // If user has no agent access, show dashboard cards
  const hasAnyAgent = Object.keys(agent_levels).length > 0;
  const navigate = useNavigate();
  const { openNewTaskModal } = useUIState();

  // Fetch recent tasks (last 5)
  const { data: recentData } = useQuery({
    queryKey: ["tasks", { recent: true }],
    queryFn: () => listTasks({ limit: 5 }),
    staleTime: 15_000,
  });

  // Fetch waiting_input tasks — oldest first, limit 5
  const { data: waitingData } = useQuery({
    queryKey: ["tasks", { status: "waiting_input", oldest: true }],
    queryFn: async () => {
      const res = await listTasks({ status: "waiting_input", limit: 100 });
      // Sort oldest first (most urgent), show top 5
      const sorted = [...res.tasks].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
      return { tasks: sorted.slice(0, 5), total: res.total };
    },
    staleTime: 15_000,
  });

  // Fetch per-agent daily spend for sparklines (usage API has accurate per-day data)
  const agentSparklines = useQuery({
    queryKey: ["agent-sparklines"],
    queryFn: async () => {
      const result = new Map<string, DailySpend[]>();
      const sevenDaysAgo = new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10);
      for (const agent of agents) {
        try {
          const res = await fetchAgentUsage(agent.id, { pageSize: 1, startDate: sevenDaysAgo });
          result.set(agent.id, res.summary.daily_spend ?? []);
        } catch { /* skip agents with no usage */ }
      }
      return result;
    },
    enabled: agents.length > 0,
    staleTime: 60_000,
  });

  const recentTasks = recentData?.tasks ?? [];
  const waitingTasks = waitingData?.tasks ?? [];
  const waitingTotal = waitingData?.total ?? 0;
  const sparklineMap = agentSparklines.data;
  const days = last7Days();

  // Aggregate counters
  const totalAgents = agents.length;
  const activeRunning = agents.reduce((sum, a) => sum + a.counters.running, 0);
  const activeAgents = agents.filter(a => a.counters.running > 0 || a.counters.waiting > 0).length;
  const waitingInput = agents.reduce((sum, a) => sum + a.counters.waiting, 0);
  const completedToday = agents.reduce((sum, a) => sum + a.counters.completed_today, 0);

  // Compute oldest wait
  let oldestWaitChip: string | undefined;
  if (waitingTasks.length > 0) {
    const oldest = waitingTasks.reduce((min, t) => {
      const created = new Date(t.created_at).getTime();
      return created < min ? created : min;
    }, Date.now());
    const mins = Math.floor((Date.now() - oldest) / 60_000);
    oldestWaitChip = mins < 60 ? `${mins} min oldest wait` : `${Math.floor(mins / 60)}h oldest wait`;
  }

  const displayName = user?.email?.split("@")[0] ?? "User";

  return (
    <div className="max-w-6xl mx-auto px-6 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-zinc-900">Dashboard</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Welcome back, {displayName}</p>
      </div>

      {!isLoading && hasAnyAgent && (
        <>
          {/* Signal cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
            <SignalCard label="Agents" value={totalAgents} chip={`${activeAgents} active now`} chipColor="emerald" />
            <SignalCard label="Running" value={activeRunning} chip={activeRunning > 0 ? `${activeRunning} runs active` : undefined} chipColor="blue" onClick={() => navigate("/tasks?statuses=running")} />
            <SignalCard label="Needs input" value={waitingInput} chip={oldestWaitChip} chipColor="amber" onClick={() => navigate("/tasks?statuses=waiting_input")} />
            <SignalCard label="Done today" value={completedToday} chip={completedToday > 0 ? "Healthy overall" : undefined} chipColor="emerald" onClick={() => navigate("/tasks?statuses=completed")} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Agents grid */}
            <div className="lg:col-span-2">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-zinc-700">Your Agents</h2>
              </div>

              {agents.length === 0 ? (
                <div className="text-center py-16 text-zinc-400 max-w-sm mx-auto">
                  <p className="text-base font-medium text-zinc-500 mb-1">No agents available yet</p>
                  <p className="text-sm">Ask an admin to grant access to an agent for your account.</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {agents.map((agent, idx) => {
                    const hasActive = agent.counters.running > 0;
                    const hasWaiting = agent.counters.waiting > 0;
                    const icon = agentEmoji(agent.name);
                    const chipType = hasActive ? "emerald" : hasWaiting ? "amber" : "zinc";
                    const statusText = hasActive
                      ? `${agent.counters.running} running`
                      : hasWaiting
                      ? `${agent.counters.waiting} waiting`
                      : "Idle";

                    // Build 7-day sparkline from daily spend (tasks count = total_tokens as proxy, or cost_usd)
                    const agentDaily = sparklineMap?.get(agent.id) ?? [];
                    const spendByDay = new Map(agentDaily.map(d => [d.date, d]));
                    const counts = days.map(d => {
                      const entry = spendByDay.get(d);
                      // Use total tokens as bar height proxy (proportional to activity)
                      return entry ? entry.input_tokens + entry.output_tokens + entry.cache_creation_input_tokens + entry.cache_read_input_tokens : 0;
                    });

                    return (
                      <div
                        key={agent.id}
                        onClick={() => navigate(`/agents/${agent.id}`)}
                        className="bg-white rounded-2xl border border-zinc-200/80 cursor-pointer hover:border-zinc-300 hover:shadow-sm transition-all"
                      >
                        <div className="p-4">
                          <div className="flex items-start justify-between mb-3">
                            <div className="flex items-center gap-2.5">
                              <div className={`w-9 h-9 rounded-xl ${PALETTE[idx % PALETTE.length].iconBg} flex items-center justify-center`}>
                                <span className="text-base">{icon}</span>
                              </div>
                              <div>
                                <h3 className="text-sm font-semibold text-zinc-900">{agent.name}</h3>
                                <p className="text-[10px] text-zinc-400">{agent.id}</p>
                              </div>
                            </div>
                            <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${chip[chipType]}`}>
                              {(hasActive || hasWaiting) && (
                                <span className={`w-1.5 h-1.5 rounded-full ${hasActive ? "bg-emerald-500 animate-pulse" : "bg-amber-500 animate-pulse"}`} />
                              )}
                              {statusText}
                            </span>
                          </div>

                          {agent.description && (
                            <p className="text-xs text-zinc-500 mb-3 line-clamp-2">{agent.description}</p>
                          )}

                          {/* 7-day task count sparkline */}
                          <AgentSparkline counts={counts} colorIdx={idx} hasActivity={hasActive || hasWaiting} />
                          <p className="text-[10px] text-zinc-400 -mt-1 mb-2">Daily activity (tokens) · last 7 days</p>

                          <div className="flex items-center justify-between">
                            <div className="flex gap-3 text-[10px] text-zinc-400">
                              <span>{agent.counters.running + agent.counters.waiting + agent.counters.completed_today} tasks today</span>
                            </div>
                            <button
                              onClick={(e) => { e.stopPropagation(); openNewTaskModal(agent.id); }}
                              className="text-xs font-medium text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 px-2 py-1 rounded-lg transition-colors"
                            >
                              New task
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Right sidebar */}
            <div className="space-y-4">
              {/* Needs attention */}
              {waitingTasks.length > 0 && (
                <div className="bg-white rounded-2xl border border-zinc-200/80 overflow-hidden">
                  <div className="px-4 py-3 border-b border-zinc-100 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-zinc-700">Needs Attention</h3>
                    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${chip.amber}`}>
                      {waitingTotal}
                    </span>
                  </div>
                  <div>
                    {waitingTasks.map(task => (
                      <a
                        key={task.id}
                        href={`/sessions/${task.session_id}#task-${task.id}`}
                        onClick={e => { e.preventDefault(); navigate(`/sessions/${task.session_id}#task-${task.id}`); }}
                        className="flex items-start gap-3 px-4 py-3 hover:bg-zinc-50 transition-colors border-b border-zinc-50 last:border-b-0"
                      >
                        <span className="w-2 h-2 rounded-full bg-amber-500 mt-1.5 flex-shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium text-zinc-800 truncate">{task.title || task.prompt.slice(0, 60)}</p>
                          <p className="text-[10px] text-zinc-400 mt-0.5">{task.agent_id ?? "Agent"} · {timeAgo(task.created_at)}</p>
                        </div>
                      </a>
                    ))}
                  </div>
                  <div className="px-4 py-2 border-t border-zinc-100">
                    <a href="/tasks?statuses=waiting_input" onClick={e => { e.preventDefault(); navigate("/tasks?statuses=waiting_input"); }} className="text-[10px] text-zinc-400 hover:text-zinc-600 transition-colors">
                      View all waiting tasks →
                    </a>
                  </div>
                </div>
              )}

              {/* Recent activity */}
              <div className="bg-white rounded-2xl border border-zinc-200/80 overflow-hidden">
                <div className="px-4 py-3 border-b border-zinc-100">
                  <h3 className="text-sm font-semibold text-zinc-700">Recent Activity</h3>
                </div>
                <div className="divide-y divide-zinc-50">
                  {recentTasks.length === 0 ? (
                    <div className="px-4 py-6 text-xs text-zinc-400 text-center">No recent tasks</div>
                  ) : (
                    recentTasks.map(task => (
                      <a
                        key={task.id}
                        href={`/sessions/${task.session_id}#task-${task.id}`}
                        onClick={e => { e.preventDefault(); navigate(`/sessions/${task.session_id}#task-${task.id}`); }}
                        className="flex items-center gap-2.5 px-4 py-2.5 hover:bg-zinc-50 transition-colors"
                      >
                        <ActivityIcon status={task.status} />
                        <div className="min-w-0 flex-1">
                          <p className="text-[11px] text-zinc-700 truncate">{task.title || task.prompt.slice(0, 50)}</p>
                          <p className="text-[10px] text-zinc-400">{task.agent_id ?? "Agent"} · {timeAgo(task.created_at)}</p>
                        </div>
                      </a>
                    ))
                  )}
                </div>
                <div className="px-4 py-2 border-t border-zinc-100">
                  <a href="/tasks" onClick={e => { e.preventDefault(); navigate("/tasks"); }} className="text-[10px] text-zinc-400 hover:text-zinc-600 transition-colors">
                    View all tasks →
                  </a>
                </div>
              </div>

              {/* Tokens Today */}
              <TokensTodayCard />
            </div>
          </div>
        </>
      )}

      {!isLoading && !hasAnyAgent && (
        <div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {DASHBOARDS.filter(d => is_admin || dashboard_access[d.id]).map(d => (
              <a key={d.id} href={d.path} onClick={e => { e.preventDefault(); navigate(d.path); }}
                className="block p-5 bg-white rounded-xl border border-zinc-200 hover:border-zinc-300 hover:shadow-sm transition-all">
                <div className="text-2xl mb-2">{d.icon}</div>
                <h3 className="text-sm font-semibold text-zinc-900">{d.label}</h3>
                <p className="text-xs text-zinc-500 mt-1">{d.desc}</p>
              </a>
            ))}
          </div>
          {DASHBOARDS.filter(d => is_admin || dashboard_access[d.id]).length === 0 && (
            <p className="text-sm text-zinc-400 mt-8 text-center">No dashboard access yet. Ask an admin to grant access.</p>
          )}
        </div>
      )}
    </div>
  );
}
