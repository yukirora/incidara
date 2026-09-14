import { useState, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useAgents } from "../hooks/useAgents.ts";
import { useTasks } from "../hooks/useTasks.ts";
import { TaskRow } from "../components/TaskRow.tsx";
import { TasksFilterBar, type TaskFilters } from "../components/TasksFilterBar.tsx";
import type { TaskStatus } from "../api/tasks.ts";

const PAGE_SIZE = 20;

function getInitialFilters(searchParams: URLSearchParams): TaskFilters {
  const statusParam = searchParams.get("statuses") ?? "";
  return {
    statuses: statusParam ? (statusParam.split(",") as TaskStatus[]) : [],
    agent_id: searchParams.get("agent_id") ?? "",
    scope: (searchParams.get("scope") ?? "all") as TaskFilters["scope"],
    time: (searchParams.get("time") ?? "all") as TaskFilters["time"],
    title_search: searchParams.get("title_search") ?? "",
  };
}

export function Tasks() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] = useState<TaskFilters>(() => getInitialFilters(searchParams));
  const [page, setPage] = useState(1);
  const { agents } = useAgents();
  const queryClient = useQueryClient();

  // Build query params from filters
  const queryParams = {
    statuses: filters.statuses.length > 0 ? filters.statuses : undefined,
    agent_id: filters.agent_id || undefined,
    scope: filters.scope !== "all" ? filters.scope : undefined,
    title_search: filters.title_search || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };

  const { tasks, total, isLoading, isFetching } = useTasks(queryParams);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function handleFiltersChange(newFilters: TaskFilters) {
    setFilters(newFilters);
    setPage(1);
    // Sync filters to URL
    const params = new URLSearchParams();
    if (newFilters.statuses.length > 0) params.set("statuses", newFilters.statuses.join(","));
    if (newFilters.agent_id) params.set("agent_id", newFilters.agent_id);
    if (newFilters.scope && newFilters.scope !== "all") params.set("scope", newFilters.scope);
    if (newFilters.time && newFilters.time !== "all") params.set("time", newFilters.time);
    if (newFilters.title_search) params.set("title_search", newFilters.title_search);
    setSearchParams(params, { replace: true });
  }

  // Build page numbers to display: always show first, last, current ± 1, and gaps
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

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-zinc-900">Tasks</h1>
        {isFetching && !isLoading && (
          <span className="text-xs text-zinc-400">Refreshing…</span>
        )}
      </div>

      <div className="mb-4">
        <TasksFilterBar
          filters={filters}
          onChange={handleFiltersChange}
          agents={agents}
        />
      </div>

      <div className="bg-white rounded-lg shadow-sm border border-zinc-200 overflow-hidden">
        {isLoading ? (
          <div className="py-12 text-center text-sm text-zinc-400">
            Loading tasks…
          </div>
        ) : tasks.length === 0 ? (
          <div className="py-12 text-center text-sm text-zinc-400">
            No tasks found. Try a different filter.
          </div>
        ) : (
          <>
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} agents={agents} onTaskUpdated={() => { void queryClient.invalidateQueries({ queryKey: ["tasks"] }); }} />
            ))}
          </>
        )}
      </div>

      {!isLoading && total > 0 && (
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
  );
}
