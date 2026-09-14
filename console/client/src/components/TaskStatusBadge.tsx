import type { TaskStatus } from "../api/tasks.ts";

interface TaskStatusBadgeProps {
  status: TaskStatus;
}

const STATUS_CONFIG: Record<
  TaskStatus,
  { label: string; className: string }
> = {
  pending: {
    label: "Queued",
    className: "bg-zinc-100 text-zinc-600",
  },
  running: {
    label: "Running",
    className: "bg-blue-100 text-blue-700",
  },
  busy: {
    label: "Running",
    className: "bg-blue-100 text-blue-700",
  },
  waiting_input: {
    label: "Waiting input",
    className: "bg-amber-100 text-amber-700",
  },
  completed: {
    label: "Completed",
    className: "bg-emerald-100 text-emerald-700",
  },
  error: {
    label: "Error",
    className: "bg-red-100 text-red-700",
  },
  interrupted: {
    label: "Interrupted",
    className: "bg-zinc-100 text-zinc-500",
  },
  cancelled: {
    label: "Cancelled",
    className: "bg-zinc-100 text-zinc-400 line-through",
  },
};

const STATUS_ICON: Record<TaskStatus, string> = {
  pending: "⏳",
  running: "●",
  busy: "●",
  waiting_input: "⚠",
  completed: "✓",
  error: "✗",
  interrupted: "⏸",
  cancelled: "",
};

export function TaskStatusBadge({ status }: TaskStatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.pending;
  const icon = STATUS_ICON[status] ?? "";

  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full whitespace-nowrap ${config.className}`}
    >
      {icon && <span>{icon}</span>}
      {config.label}
    </span>
  );
}
