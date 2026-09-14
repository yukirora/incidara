import { useState, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useAgent } from "../hooks/useAgents.ts";
import { useAgents } from "../hooks/useAgents.ts";
import { useTasks } from "../hooks/useTasks.ts";
import { NewTaskForm } from "../components/NewTaskForm.tsx";
import { TaskRow } from "../components/TaskRow.tsx";
import { TasksFilterBar, type TaskFilters } from "../components/TasksFilterBar.tsx";
import { AgentUsageTab } from "../components/AgentUsageTab.tsx";
import type { TaskStatus } from "../api/tasks.ts";

const PAGE_SIZE = 20;

const DEFAULT_FILTERS: TaskFilters = {
  statuses: ["waiting_input", "running", "interrupted"] as TaskStatus[],
  agent_id: "",
  scope: "all",
  time: "all",
  title_search: "",
};

const ACCESS_BADGE: Record<string, { label: string; className: string }> = {
  admin: { label: "ADMIN", className: "bg-red-100 text-red-800" },
  owner: { label: "OWNER", className: "bg-zinc-900 text-white" },
  group: { label: "GROUP", className: "bg-purple-100 text-purple-800" },
};

const BACKEND_LABEL: Record<string, string> = {
  claude_code: "Claude Code",
  pi_agent: "Pi Agent",
};

export function AgentDetail() {
  const { id } = useParams<{ id: string }>();
  const { agent, isLoading, error } = useAgent(id ?? "");
  const { agents } = useAgents();
  const queryClient = useQueryClient();

  const [filters, setFilters] = useState<TaskFilters>({...DEFAULT_FILTERS, title_search: ""});
  const [page, setPage] = useState(1);
  const [activeTab, setActiveTab] = useState<"tasks" | "usage">("tasks");

  const queryParams = {
    statuses: filters.statuses.length > 0 ? filters.statuses : undefined,
    agent_id: id,
    scope: filters.scope !== "all" ? filters.scope : undefined,
    title_search: filters.title_search || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };

  const { tasks, total, isLoading: tasksLoading, isFetching } = useTasks(queryParams);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function handleFiltersChange(newFilters: TaskFilters) {
    setFilters(newFilters);
    setPage(1);
  }

  // Build page numbers
  const pageNumbers = useMemo(() => {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const pages: (number | "gap")[] = [];
    pages.push(1);
    const start = Math.max(2, page - 1);
    const end = Math.min(totalPages - 1, page + 1);
    if (start > 2) pages.push("gap");
    for (let i = start; i <= end; i++) pages.push(i);
    if (end < totalPages - 1) pages.push("gap");
    pages.push(totalPages);
    return pages;
  }, [page, totalPages]);

  if (isLoading) {
    return (
      <div className="p-6 text-sm text-zinc-400">Loading agent…</div>
    );
  }

  if (error || !agent) {
    return (
      <div className="p-6">
        <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error?.message ?? "Agent not found"}
        </div>
        <Link to="/" className="mt-4 inline-block text-sm text-zinc-500 hover:text-zinc-700">
          ← Back to overview
        </Link>
      </div>
    );
  }

  const badge = ACCESS_BADGE[agent.access_level] ?? ACCESS_BADGE.group;

  return (
    <div className="p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <Link to="/" className="text-xs text-zinc-400 hover:text-zinc-600 mb-3 inline-block">
          ← Overview
        </Link>
        <div className="bg-white rounded-lg shadow-sm border border-zinc-200 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <h1 className="text-lg font-semibold text-zinc-900">{agent.name}</h1>
                <span
                  className={`text-xs font-semibold px-1.5 py-0.5 rounded flex-shrink-0 ${badge.className}`}
                >
                  {badge.label}
                </span>
              </div>
              {agent.description && (
                <p className="text-sm text-zinc-500 mb-2">{agent.description}</p>
              )}
              <p className="text-xs text-zinc-400">
                Backend: {BACKEND_LABEL[agent.backend] ?? agent.backend}
              </p>
            </div>
            <div className="text-right flex-shrink-0">
              <div className="flex gap-3 text-xs">
                {agent.counters.running > 0 && (
                  <span className="text-emerald-600 font-medium">
                    {agent.counters.running} running
                  </span>
                )}
                {agent.counters.waiting > 0 && (
                  <span className="text-amber-600 font-medium">
                    {agent.counters.waiting} waiting
                  </span>
                )}
                {agent.counters.completed_today > 0 && (
                  <span className="text-zinc-400">
                    {agent.counters.completed_today} done today
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-4 border-b border-zinc-200">
        <button
          onClick={() => setActiveTab("tasks")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === "tasks"
              ? "border-zinc-900 text-zinc-900"
              : "border-transparent text-zinc-400 hover:text-zinc-600"
          }`}
        >
          Tasks
        </button>
        <button
          onClick={() => setActiveTab("usage")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === "usage"
              ? "border-zinc-900 text-zinc-900"
              : "border-transparent text-zinc-400 hover:text-zinc-600"
          }`}
        >
          Usage
        </button>
      </div>

      {/* Tasks tab */}
      {activeTab === "tasks" && (
      <div>
      {/* New Task Form */}
      <div className="bg-white rounded-lg shadow-sm border border-zinc-200 p-4 mb-6">
        <h2 className="text-sm font-semibold text-zinc-700 mb-3">New task</h2>
        <NewTaskForm agentId={agent.id} />
      </div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-zinc-700">Tasks</h2>
          {isFetching && !tasksLoading && (
            <span className="text-xs text-zinc-400">Refreshing…</span>
          )}
        </div>

        <div className="mb-3">
          <TasksFilterBar
            filters={filters}
            onChange={handleFiltersChange}
            agents={agents}
            hideAgent={true}
          />
        </div>

        <div className="bg-white rounded-lg shadow-sm border border-zinc-200 overflow-hidden">
          {tasksLoading ? (
            <div className="py-12 text-center text-sm text-zinc-400">
              Loading tasks…
            </div>
          ) : tasks.length === 0 ? (
            <div className="py-12 text-center text-sm text-zinc-400">
              No sessions yet on this agent. Start your first task above ↑
            </div>
          ) : (
            tasks.map((task) => (
              <TaskRow key={task.id} task={task} agents={agents} showUsage={true} onTaskUpdated={() => { void queryClient.invalidateQueries({ queryKey: ["tasks"] }); }} />
            ))
          )}
        </div>

        {!tasksLoading && tasks.length > 0 && (
          <div className="mt-3 flex items-center justify-between">
            <span className="text-xs text-zinc-400">
              Showing {((page - 1) * PAGE_SIZE) + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
            </span>
            {totalPages > 1 && (
              <nav className="flex items-center gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1 || isFetching}
                  className="px-2 py-1 text-sm border border-zinc-200 rounded-md bg-white hover:bg-zinc-50 text-zinc-700 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ‹
                </button>
                {pageNumbers.map((p, i) =>
                  p === "gap" ? (
                    <span key={`gap-${i}`} className="px-1 text-zinc-400">…</span>
                  ) : (
                    <button
                      key={p}
                      onClick={() => setPage(p)}
                      disabled={isFetching}
                      className={`min-w-[32px] px-2 py-1 text-sm rounded-md border disabled:cursor-not-allowed ${
                        p === page
                          ? "bg-zinc-900 text-white border-zinc-900"
                          : "bg-white text-zinc-700 border-zinc-200 hover:bg-zinc-50"
                      }`}
                    >
                      {p}
                    </button>
                  )
                )}
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages || isFetching}
                  className="px-2 py-1 text-sm border border-zinc-200 rounded-md bg-white hover:bg-zinc-50 text-zinc-700 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  ›
                </button>
              </nav>
            )}
          </div>
        )}
      </div>
      )} {/* end tasks tab */}

      {/* Usage tab */}
      {activeTab === "usage" && (
        <AgentUsageTab agentId={agent.id} />
      )}
    </div>
  );
}
