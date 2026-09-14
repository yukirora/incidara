import { useState } from "react";
import { Link } from "react-router-dom";
import type { Task } from "../api/tasks.ts";
import type { AgentSummary } from "../api/agents.ts";
import { TaskStatusBadge } from "./TaskStatusBadge.tsx";
import { TaskUsageSection } from "./TaskUsageSection.tsx";
import { useSchedule } from "../hooks/useSchedules.ts";
import { completeTask } from "../api/tasks.ts";
import { resume } from "../api/sessions.ts";

interface TaskRowProps {
  task: Task;
  agents: AgentSummary[];
  onTaskUpdated?: () => void;
  showUsage?: boolean;
}

function formatTaskTime(dateStr: string): string {
  const d = new Date(dateStr);
  const diff = Date.now() - d.getTime();
  const minutes = Math.floor(diff / 60_000);
  let ago: string;
  if (minutes < 1) ago = "just now";
  else if (minutes < 60) ago = `${minutes}m ago`;
  else if (minutes < 1440) ago = `${Math.floor(minutes / 60)}h ago`;
  else ago = `${Math.floor(minutes / 1440)}d ago`;
  const date = d.toLocaleDateString([], { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${date} ${time} · ${ago}`;
}

function truncate(str: string, max: number): string {
  if (str.length <= max) return str;
  return str.slice(0, max) + "…";
}

export function TaskRow({ task, agents, onTaskUpdated, showUsage = false }: TaskRowProps) {
  const { schedule } = useSchedule(task.from_schedule_id ?? null);
  const [completing, setCompleting] = useState(false);

  const agentName =
    agents.find((a) => a.id === task.agent_id)?.name ??
    (task.agent_id ? task.agent_id : `session ${task.session_id}`);

  const promptFirstLine = task.prompt.split("\n")[0] ?? task.prompt;
  const promptDisplay = truncate(promptFirstLine, 80);

  let outcomeText = "";
  if (task.status === "completed" && task.output_preview) {
    outcomeText = truncate(task.output_preview, 100);
  } else if (task.status === "error" && task.output_preview) {
    outcomeText = truncate(task.output_preview, 100);
  } else if (task.status === "pending") {
    outcomeText = "Queued — waiting for previous task";
  } else if (task.status === "running" || task.status === "busy") {
    outcomeText = "Running…";
  } else if (task.status === "waiting_input") {
    outcomeText = "Waiting for your input";
  } else if (task.status === "interrupted") {
    outcomeText = task.output_preview ? truncate(task.output_preview, 100) : "Interrupted";
  } else if (task.status === "cancelled") {
    outcomeText = "Cancelled";
  }

  async function handleMarkComplete(e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    setCompleting(true);
    try {
      await completeTask(task.id);
      onTaskUpdated?.();
    } catch { /* ignore */ } finally { setCompleting(false); }
  }

  async function handleResume(e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    setCompleting(true);
    try {
      await resume(task.session_id);
      onTaskUpdated?.();
    } catch {
      // ignore
    } finally {
      setCompleting(false);
    }
  }

  const isWaitingManual = task.status === "waiting_input" && task.completion_mode === "manual";

  const linkTo = `/sessions/${task.session_id}#task-${task.id}`;

  return (
    <div className="border-b border-zinc-100 hover:bg-zinc-50 transition-colors">
      <Link
        to={linkTo}
        className="flex items-start gap-3 px-4 py-3 block"
      >
        {/* Status badge */}
        <div className="pt-0.5 flex-shrink-0">
          <TaskStatusBadge status={task.status} />
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-medium text-zinc-500 truncate">{agentName}</span>
            {task.from_schedule_id && (
              <Link
                to="/schedules"
                className="flex-shrink-0 text-xs bg-violet-50 text-violet-600 border border-violet-200 px-1.5 py-0.5 rounded hover:bg-violet-100 transition-colors"
                title={`From schedule: ${schedule?.name ?? `#${task.from_schedule_id}`}`}
              >
                &#9201; via {schedule?.name ?? `#${task.from_schedule_id}`}
              </Link>
            )}
          </div>
          {task.title && (
            <p className="text-sm text-zinc-900 truncate font-medium">{task.title}</p>
          )}
          <p className={`text-sm truncate ${task.title ? "text-zinc-500 font-normal" : "text-zinc-900 font-medium"}`}>{promptDisplay}</p>
          {outcomeText && (
            <p className="text-xs text-zinc-400 truncate mt-0.5">{outcomeText}</p>
          )}
        </div>

        {/* Meta */}
        <div className="flex-shrink-0 text-right">
          {isWaitingManual && (
            <button
              onClick={handleMarkComplete}
              disabled={completing}
              className="text-xs px-2 py-0.5 border border-emerald-400 rounded-md text-emerald-700 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50 transition-colors whitespace-nowrap mb-1"
              title="Mark this task as complete"
            >
              {completing ? "…" : "✓ Complete"}
            </button>
          )}
          {task.status === "interrupted" && (
            <>
              <button
                onClick={handleResume}
                disabled={completing}
                className="text-xs px-2 py-0.5 border border-amber-400 rounded-md text-amber-700 bg-amber-50 hover:bg-amber-100 disabled:opacity-50 transition-colors whitespace-nowrap mb-1"
                title="Resume this interrupted task"
              >
                {completing ? "…" : "↻ Resume"}
              </button>
              {task.completion_mode === "manual" && (
                <button
                  onClick={handleMarkComplete}
                  disabled={completing}
                  className="text-xs px-2 py-0.5 border border-emerald-400 rounded-md text-emerald-700 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50 transition-colors whitespace-nowrap mb-1"
                  title="Mark this task as complete"
                >
                  {completing ? "…" : "✓ Complete"}
                </button>
              )}
            </>
          )}
          <p className="text-xs text-zinc-400">{formatTaskTime(task.created_at)}</p>
          <p className="text-xs text-zinc-300">{task.submitter_email}</p>
        </div>
      </Link>

      {/* Usage section — outside the Link so clicks don't navigate */}
      {task.status !== "pending" && showUsage && (
        <div onClick={(e) => e.stopPropagation()}>
          <TaskUsageSection taskId={task.id} />
        </div>
      )}
    </div>
  );
}
