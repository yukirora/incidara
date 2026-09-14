import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { AgentSummary } from "../api/agents.ts";
import { listTasks } from "../api/tasks.ts";
import { TaskStatusBadge } from "./TaskStatusBadge.tsx";

interface AgentCardProps {
  agent: AgentSummary;
}

const ACCESS_BADGE: Record<string, { label: string; className: string }> = {
  admin: { label: "ADMIN", className: "bg-red-100 text-red-800" },
  owner: { label: "OWNER", className: "bg-zinc-900 text-white" },
  group: { label: "GROUP", className: "bg-purple-100 text-purple-800" },
};

export function AgentCard({ agent }: AgentCardProps) {
  const navigate = useNavigate();
  const badge = ACCESS_BADGE[agent.access_level] ?? ACCESS_BADGE.group;

  const hasActive = agent.counters.running > 0;
  const hasWaiting = agent.counters.waiting > 0;

  const statusDotClass = hasActive
    ? "bg-emerald-500"
    : hasWaiting
    ? "bg-amber-500"
    : "bg-zinc-300";

  const statusTitle = hasActive
    ? "Running"
    : hasWaiting
    ? "Waiting for input"
    : "Idle";

  // Fetch recent tasks for this agent
  const { data: tasksData } = useQuery({
    queryKey: ["tasks", { agent_id: agent.id, limit: 5 }],
    queryFn: () => listTasks({ agent_id: agent.id, limit: 5 }),
    staleTime: 15_000,
  });

  const recentTasks = tasksData?.tasks ?? [];

  function handleCardClick() {
    navigate(`/agents/${agent.id}`);
  }

  function handleNewTask(e: React.MouseEvent) {
    e.stopPropagation();
    navigate(`/agents/${agent.id}`);
  }

  function handleTaskClick(e: React.MouseEvent, sessionId: number, taskId: number) {
    e.stopPropagation();
    navigate(`/sessions/${sessionId}#task-${taskId}`);
  }

  return (
    <div
      onClick={handleCardClick}
      className="bg-white rounded-lg shadow-sm border border-zinc-200 cursor-pointer hover:border-zinc-300 hover:shadow transition-all"
    >
      {/* Agent header */}
      <div className="p-4">
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusDotClass}`}
              title={statusTitle}
            />
            <h3 className="text-sm font-semibold text-zinc-900 truncate">{agent.name}</h3>
          </div>
          <span
            className={`text-xs font-semibold px-1.5 py-0.5 rounded flex-shrink-0 ${badge.className}`}
          >
            {badge.label}
          </span>
        </div>

        {agent.description && (
          <p className="text-xs text-zinc-500 mb-3 line-clamp-2">{agent.description}</p>
        )}

        <div className="flex items-center justify-between">
          <p className="text-xs text-zinc-400">
            {agent.counters.running > 0 && (
              <span className="text-emerald-600 font-medium">{agent.counters.running} running</span>
            )}
            {agent.counters.running > 0 && agent.counters.waiting > 0 && " · "}
            {agent.counters.waiting > 0 && (
              <span className="text-amber-600 font-medium">{agent.counters.waiting} waiting</span>
            )}
            {(agent.counters.running > 0 || agent.counters.waiting > 0) &&
              agent.counters.completed_today > 0 &&
              " · "}
            {agent.counters.completed_today > 0 && (
              <span>{agent.counters.completed_today} completed today</span>
            )}
            {agent.counters.running === 0 &&
              agent.counters.waiting === 0 &&
              agent.counters.completed_today === 0 && (
                <span>No active tasks</span>
              )}
          </p>
          <button
            onClick={handleNewTask}
            className="text-xs px-2 py-1 bg-zinc-900 text-white rounded hover:bg-zinc-700 transition-colors flex-shrink-0"
          >
            New task
          </button>
        </div>
      </div>

      {/* Recent tasks */}
      {recentTasks.length > 0 && (
        <div className="border-t border-zinc-100">
          {recentTasks.map((task) => (
            <div
              key={task.id}
              onClick={(e) => handleTaskClick(e, task.session_id, task.id)}
              className="flex items-center gap-2 px-4 py-2 hover:bg-zinc-50 transition-colors border-b border-zinc-50 last:border-b-0"
            >
              <TaskStatusBadge status={task.status} />
              <span className="text-xs text-zinc-700 truncate flex-1">
                {task.title || task.prompt.slice(0, 60)}
              </span>
              {task.output_preview && (
                <span className="text-xs text-zinc-400 truncate max-w-[200px] hidden sm:inline">
                  {task.output_preview.slice(0, 40)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
