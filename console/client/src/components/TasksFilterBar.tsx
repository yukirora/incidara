import { useState, useRef, useEffect } from "react";
import type { TaskStatus } from "../api/tasks.ts";
import type { AgentSummary } from "../api/agents.ts";

export interface TaskFilters {
  statuses: TaskStatus[];
  agent_id: string;
  scope: "all" | "mine" | "shared";
  time: "all" | "24h" | "7d" | "30d";
  title_search: string;
}

interface TasksFilterBarProps {
  filters: TaskFilters;
  onChange: (filters: TaskFilters) => void;
  agents: AgentSummary[];
  hideAgent?: boolean;
}

const STATUS_OPTIONS: [TaskStatus, string][] = [
  ["pending", "Queued"],
  ["running", "Running"],
  ["waiting_input", "Waiting input"],
  ["completed", "Completed"],
  ["error", "Error"],
  ["interrupted", "Interrupted"],
  ["cancelled", "Cancelled"],
];

const selectClass =
  "text-sm border border-zinc-200 rounded-md px-2 py-1.5 bg-white text-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-400";

export function TasksFilterBar({
  filters,
  onChange,
  agents,
  hideAgent = false,
}: TasksFilterBarProps) {
  const [statusOpen, setStatusOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!statusOpen) return;
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setStatusOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [statusOpen]);

  function update(patch: Partial<TaskFilters>) {
    onChange({ ...filters, ...patch });
  }

  function toggleStatus(val: TaskStatus) {
    const next = filters.statuses.includes(val)
      ? filters.statuses.filter((s) => s !== val)
      : [...filters.statuses, val];
    update({ statuses: next });
  }

  const statusLabel = filters.statuses.length === 0
    ? "All statuses"
    : filters.statuses.length === 1
      ? STATUS_OPTIONS.find(([v]) => v === filters.statuses[0])?.[1] ?? filters.statuses[0]
      : `${filters.statuses.length} statuses`;

  return (
    <div className="flex flex-wrap gap-2">
      {/* Title search */}
      <input
        type="text"
        placeholder="Search title or prompt…"
        value={filters.title_search}
        onChange={(e) => update({ title_search: e.target.value })}
        className="text-sm border border-zinc-200 rounded-md px-2.5 py-1.5 bg-white text-zinc-700 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-400 min-w-[180px]"
      />

      {/* Status filter — dropdown with multi-select checkboxes */}
      <div className="relative" ref={dropdownRef}>
        <button
          type="button"
          onClick={() => setStatusOpen((v) => !v)}
          className={selectClass + " text-left min-w-[110px]"}
        >
          {statusLabel}
        </button>
        {statusOpen && (
          <div className="absolute top-full left-0 mt-1 bg-white border border-zinc-200 rounded-md shadow-lg z-50 py-1 min-w-[160px]">
            {STATUS_OPTIONS.map(([val, label]) => (
              <label
                key={val}
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-zinc-50 cursor-pointer text-sm text-zinc-700"
              >
                <input
                  type="checkbox"
                  checked={filters.statuses.includes(val)}
                  onChange={() => toggleStatus(val)}
                  className="h-3.5 w-3.5 accent-zinc-600"
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* Agent filter */}
      {!hideAgent && (
        <select
          value={filters.agent_id}
          onChange={(e) => update({ agent_id: e.target.value })}
          className={selectClass}
        >
          <option value="">All agents</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      )}

      {/* Scope filter */}
      <select
        value={filters.scope}
        onChange={(e) => update({ scope: e.target.value as TaskFilters["scope"] })}
        className={selectClass}
      >
        <option value="all">All</option>
        <option value="mine">Mine</option>
        <option value="shared">Shared</option>
      </select>

      {/* Time filter (stub — no server param yet) */}
      <select
        value={filters.time}
        onChange={(e) => update({ time: e.target.value as TaskFilters["time"] })}
        className={selectClass}
      >
        <option value="all">All time</option>
        <option value="24h">Last 24h</option>
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
      </select>
    </div>
  );
}
