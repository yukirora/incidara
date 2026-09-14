import type { Schedule } from "../api/schedules.ts";
import { usePatchSchedule, useDeleteSchedule, useRunNow } from "../hooks/useSchedules.ts";

interface ScheduleRowProps {
  schedule: Schedule;
  onEdit: (schedule: Schedule) => void;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function timeUntil(dateStr: string): string {
  const diff = new Date(dateStr).getTime() - Date.now();
  if (diff <= 0) return "due now";
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function triggerSummary(schedule: Schedule): string {
  switch (schedule.trigger_type) {
    case "once": {
      if (!schedule.run_at) return "Once";
      return `Once at ${new Date(schedule.run_at).toLocaleString()} ${schedule.timezone}`;
    }
    case "interval": {
      const sec = schedule.interval_seconds ?? 0;
      if (sec < 60) return `Every ${sec}s`;
      const min = Math.floor(sec / 60);
      if (sec % 60 === 0 && min < 60) return `Every ${min}m`;
      const hr = Math.floor(min / 60);
      if (sec % 3600 === 0) return `Every ${hr}h`;
      return `Every ${hr}h ${min % 60}m`;
    }
    case "cron":
      return `Cron: ${schedule.cron_expr ?? ""} (${schedule.timezone})`;
  }
}

export function ScheduleRow({ schedule, onEdit }: ScheduleRowProps) {
  const patchMutation = usePatchSchedule();
  const deleteMutation = useDeleteSchedule();
  const runNowMutation = useRunNow();

  function handleToggle() {
    patchMutation.mutate({ id: schedule.id, input: { enabled: !schedule.enabled } });
  }

  function handleDelete() {
    if (!confirm(`Delete schedule "${schedule.name ?? schedule.prompt.slice(0, 40)}"?`)) return;
    deleteMutation.mutate(schedule.id);
  }

  function handleRunNow() {
    runNowMutation.mutate(schedule.id);
  }

  return (
    <div className="flex items-center gap-4 px-4 py-3 border-b border-zinc-100 hover:bg-zinc-50">
      {/* Toggle */}
      <button
        onClick={handleToggle}
        disabled={patchMutation.isPending}
        title={schedule.enabled ? "Disable" : "Enable"}
        className={`w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
          schedule.enabled ? "bg-emerald-500" : "bg-zinc-300"
        } disabled:opacity-50`}
      >
        <span
          className={`block w-4 h-4 bg-white rounded-full shadow transition-transform mx-0.5 ${
            schedule.enabled ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </button>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-sm font-medium text-zinc-900 truncate">
            {schedule.name ?? schedule.prompt.split("\n")[0]?.slice(0, 60) ?? "Unnamed"}
          </span>
          {!schedule.enabled && (
            <span className="text-xs bg-zinc-100 text-zinc-500 px-1.5 py-0.5 rounded">
              disabled
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-zinc-500">
          <span>{triggerSummary(schedule)}</span>
          <span className="text-zinc-300">·</span>
          <span>
            {schedule.session_mode === "reuse"
              ? `Reuse session #${schedule.reuse_session_id}`
              : "New session each firing"}
          </span>
          <span className="text-zinc-300">·</span>
          <span className={schedule.completion_mode === "manual" ? "text-amber-600 font-medium" : ""}>
            {schedule.completion_mode === "manual" ? "✋ Manual" : "Auto-complete"}
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs text-zinc-400 mt-0.5">
          {schedule.last_fired_at ? (
            <span>Last fired: {timeAgo(schedule.last_fired_at)}</span>
          ) : (
            <span>Last fired: never</span>
          )}
          {schedule.next_fire_at && schedule.enabled && (
            <>
              <span className="text-zinc-300">·</span>
              <span>Next: in {timeUntil(schedule.next_fire_at)}</span>
            </>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          onClick={handleRunNow}
          disabled={runNowMutation.isPending}
          className="text-xs px-2 py-1 rounded border border-zinc-200 text-zinc-600 hover:bg-zinc-100 disabled:opacity-50"
        >
          {runNowMutation.isPending ? "Running…" : "Run now"}
        </button>
        <button
          onClick={() => onEdit(schedule)}
          className="text-xs px-2 py-1 rounded border border-zinc-200 text-zinc-600 hover:bg-zinc-100"
        >
          Edit
        </button>
        <button
          onClick={handleDelete}
          disabled={deleteMutation.isPending}
          className="text-xs px-2 py-1 rounded border border-red-200 text-red-500 hover:bg-red-50 disabled:opacity-50"
        >
          {deleteMutation.isPending ? "…" : "Delete"}
        </button>
      </div>
    </div>
  );
}
