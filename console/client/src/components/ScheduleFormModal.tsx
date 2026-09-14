import { useState, useEffect, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { CronExpressionParser } from "cron-parser";
import { useAgents } from "../hooks/useAgents.ts";
import { useCreateSchedule, usePatchSchedule } from "../hooks/useSchedules.ts";
import { listSessions, type Session } from "../api/sessions.ts";
import type { Schedule, TriggerType, SessionMode, CompletionMode } from "../api/schedules.ts";

interface ScheduleFormModalProps {
  open: boolean;
  onClose: () => void;
  /** Pre-fill fields (e.g. from "Schedule this task" entry) */
  initialAgentId?: string;
  initialPrompt?: string;
  /** If provided, we're editing an existing schedule */
  editSchedule?: Schedule;
}

const TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Paris",
  "Asia/Tokyo",
  "Asia/Shanghai",
  "Australia/Sydney",
];

function validateCron(expr: string, tz: string): string | null {
  if (!expr.trim()) return "Cron expression is required";
  try {
    CronExpressionParser.parse(expr, { tz });
    return null;
  } catch (err) {
    return err instanceof Error ? err.message : "Invalid cron expression";
  }
}

export function ScheduleFormModal({
  open,
  onClose,
  initialAgentId,
  initialPrompt,
  editSchedule,
}: ScheduleFormModalProps) {
  const { agents } = useAgents();
  const createMutation = useCreateSchedule();
  const patchMutation = usePatchSchedule();

  const isEdit = !!editSchedule;

  // Form state
  const [agentId, setAgentId] = useState(initialAgentId ?? editSchedule?.agent_id ?? "");
  const [name, setName] = useState(editSchedule?.name ?? "");
  const [prompt, setPrompt] = useState(initialPrompt ?? editSchedule?.prompt ?? "");
  const [triggerType, setTriggerType] = useState<TriggerType>(
    editSchedule?.trigger_type ?? "interval"
  );
  const [runAt, setRunAt] = useState(
    editSchedule?.run_at
      ? new Date(editSchedule.run_at).toISOString().slice(0, 16)
      : ""
  );
  const [intervalSeconds, setIntervalSeconds] = useState(
    String(editSchedule?.interval_seconds ?? 3600)
  );
  const [cronExpr, setCronExpr] = useState(editSchedule?.cron_expr ?? "0 * * * *");
  const [timezone, setTimezone] = useState(editSchedule?.timezone ?? "UTC");
  const [sessionMode, setSessionMode] = useState<SessionMode>(
    editSchedule?.session_mode ?? "new"
  );
  const [reuseSessionId, setReuseSessionId] = useState<string>(
    String(editSchedule?.reuse_session_id ?? "")
  );
  const [completionMode, setCompletionMode] = useState<CompletionMode>(
    editSchedule?.completion_mode ?? "auto"
  );
  const [cronError, setCronError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Sync all fields when modal opens or editSchedule changes
  useEffect(() => {
    if (!open) return;
    if (editSchedule) {
      setAgentId(editSchedule.agent_id ?? "");
      setName(editSchedule.name ?? "");
      setPrompt(editSchedule.prompt ?? "");
      setTriggerType(editSchedule.trigger_type ?? "interval");
      setRunAt(
        editSchedule.run_at
          ? new Date(editSchedule.run_at).toISOString().slice(0, 16)
          : ""
      );
      setIntervalSeconds(String(editSchedule.interval_seconds ?? 3600));
      setCronExpr(editSchedule.cron_expr ?? "0 * * * *");
      setTimezone(editSchedule.timezone ?? "UTC");
      setSessionMode(editSchedule.session_mode ?? "new");
      setReuseSessionId(String(editSchedule.reuse_session_id ?? ""));
      setCompletionMode(editSchedule.completion_mode ?? "auto");
    } else {
      // Reset to defaults for new schedule
      setAgentId(initialAgentId ?? "");
      setName("");
      setPrompt(initialPrompt ?? "");
      setTriggerType("interval");
      setRunAt("");
      setIntervalSeconds("3600");
      setCronExpr("0 * * * *");
      setTimezone("UTC");
      setSessionMode("new");
      setReuseSessionId("");
      setCompletionMode("auto");
    }
    setCronError(null);
    setSubmitError(null);
  }, [open, editSchedule, initialAgentId, initialPrompt]);

  // Load sessions for reuse picker
  const { data: sessionsData } = useQuery<Session[]>({
    queryKey: ["sessions", agentId, "mine"],
    queryFn: () => listSessions({ agent_id: agentId, scope: "mine" }),
    enabled: sessionMode === "reuse" && !!agentId,
    staleTime: 15_000,
  });
  const sessions = sessionsData ?? [];

  function handleCronBlur() {
    setCronError(validateCron(cronExpr, timezone));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitError(null);

    if (triggerType === "cron") {
      const err = validateCron(cronExpr, timezone);
      if (err) {
        setCronError(err);
        return;
      }
    }

    const base = {
      name: name.trim() || undefined,
      prompt: prompt.trim(),
      trigger_type: triggerType,
      run_at: triggerType === "once" ? new Date(runAt).toISOString() : undefined,
      interval_seconds: triggerType === "interval" ? parseInt(intervalSeconds, 10) : undefined,
      cron_expr: triggerType === "cron" ? cronExpr.trim() : undefined,
      timezone,
      session_mode: sessionMode,
      reuse_session_id:
        sessionMode === "reuse" && reuseSessionId
          ? parseInt(reuseSessionId, 10)
          : undefined,
      completion_mode: completionMode,
    };

    try {
      if (isEdit && editSchedule) {
        await patchMutation.mutateAsync({ id: editSchedule.id, input: base });
      } else {
        await createMutation.mutateAsync({ agent_id: agentId, ...base });
      }
      onClose();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Failed to save schedule");
    }
  }

  if (!open) return null;

  const isPending = createMutation.isPending || patchMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 overflow-y-auto max-h-[90vh]">
        <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
          <h2 className="text-base font-semibold text-zinc-900">
            {isEdit ? "Edit schedule" : "New schedule"}
          </h2>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-700 text-xl leading-none"
          >
            &times;
          </button>
        </div>

        <form
          onSubmit={(e) => { void handleSubmit(e); }}
          className="px-6 py-4 space-y-4"
        >
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-zinc-700 mb-1">
              Name <span className="text-zinc-400 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
              placeholder="My daily report"
            />
          </div>

          {/* Agent */}
          {!isEdit && (
            <div>
              <label className="block text-sm font-medium text-zinc-700 mb-1">
                Agent <span className="text-red-500">*</span>
              </label>
              <select
                required
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
              >
                <option value="">Select agent…</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Prompt */}
          <div>
            <label className="block text-sm font-medium text-zinc-700 mb-1">
              Prompt <span className="text-red-500">*</span>
            </label>
            <textarea
              required
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500 resize-vertical"
              placeholder="What should the agent do?"
            />
          </div>

          {/* Trigger type */}
          <div>
            <p className="text-sm font-medium text-zinc-700 mb-2">Trigger</p>
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="trigger"
                  value="once"
                  checked={triggerType === "once"}
                  onChange={() => setTriggerType("once")}
                />
                <span className="text-sm text-zinc-700">Once at</span>
                {triggerType === "once" && (
                  <input
                    type="datetime-local"
                    required
                    value={runAt}
                    onChange={(e) => setRunAt(e.target.value)}
                    className="ml-2 px-2 py-1 border border-zinc-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
                  />
                )}
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="trigger"
                  value="interval"
                  checked={triggerType === "interval"}
                  onChange={() => setTriggerType("interval")}
                />
                <span className="text-sm text-zinc-700">Every</span>
                {triggerType === "interval" && (
                  <div className="flex items-center gap-1 ml-2">
                    <input
                      type="number"
                      required
                      min={30}
                      value={intervalSeconds}
                      onChange={(e) => setIntervalSeconds(e.target.value)}
                      className="w-24 px-2 py-1 border border-zinc-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
                    />
                    <span className="text-sm text-zinc-500">seconds</span>
                  </div>
                )}
              </label>

              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="trigger"
                  value="cron"
                  checked={triggerType === "cron"}
                  onChange={() => setTriggerType("cron")}
                  className="mt-1"
                />
                <div className="flex-1">
                  <span className="text-sm text-zinc-700">Cron</span>
                  {triggerType === "cron" && (
                    <div className="mt-1 space-y-2">
                      <input
                        type="text"
                        required
                        value={cronExpr}
                        onChange={(e) => { setCronExpr(e.target.value); setCronError(null); }}
                        onBlur={handleCronBlur}
                        className={`w-full px-2 py-1 border rounded text-sm font-mono focus:outline-none focus:ring-2 focus:ring-zinc-500 ${cronError ? "border-red-400" : "border-zinc-300"}`}
                        placeholder="0 * * * *"
                      />
                      {cronError && (
                        <p className="text-xs text-red-500">{cronError}</p>
                      )}
                      <select
                        value={timezone}
                        onChange={(e) => setTimezone(e.target.value)}
                        className="w-full px-2 py-1 border border-zinc-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
                      >
                        {TIMEZONES.map((tz) => (
                          <option key={tz} value={tz}>{tz}</option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>
              </label>
            </div>
          </div>

          {/* Timezone (for once / interval) */}
          {triggerType !== "cron" && (
            <div>
              <label className="block text-sm font-medium text-zinc-700 mb-1">
                Timezone
              </label>
              <select
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
              >
                {TIMEZONES.map((tz) => (
                  <option key={tz} value={tz}>{tz}</option>
                ))}
              </select>
            </div>
          )}

          {/* Session mode */}
          <div>
            <p className="text-sm font-medium text-zinc-700 mb-2">Session</p>
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="session-mode"
                  value="new"
                  checked={sessionMode === "new"}
                  onChange={() => setSessionMode("new")}
                />
                <span className="text-sm text-zinc-700">New session each firing</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="session-mode"
                  value="reuse"
                  checked={sessionMode === "reuse"}
                  onChange={() => setSessionMode("reuse")}
                />
                <span className="text-sm text-zinc-700">Reuse session</span>
              </label>
            </div>

            {sessionMode === "reuse" && (
              <div className="mt-2">
                {!agentId ? (
                  <p className="text-sm text-zinc-400">Select an agent first</p>
                ) : sessions.length === 0 ? (
                  <p className="text-sm text-zinc-400">No sessions on this agent yet</p>
                ) : (
                  <select
                    required
                    value={reuseSessionId}
                    onChange={(e) => setReuseSessionId(e.target.value)}
                    className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
                  >
                    <option value="">Select session…</option>
                    {sessions.map((s) => (
                      <option key={s.id} value={String(s.id)}>
                        {s.title ?? `Session ${s.id}`}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}
          </div>

          {/* Completion mode */}
          <div>
            <p className="text-sm font-medium text-zinc-700 mb-2">Completion</p>
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="completion-mode"
                  value="auto"
                  checked={completionMode === "auto"}
                  onChange={() => setCompletionMode("auto")}
                />
                <span className="text-sm text-zinc-700">Auto-complete</span>
                <span className="text-xs text-zinc-400">— task completes when agent finishes</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="completion-mode"
                  value="manual"
                  checked={completionMode === "manual"}
                  onChange={() => setCompletionMode("manual")}
                />
                <span className="text-sm text-zinc-700">Manual</span>
                <span className="text-xs text-zinc-400">— requires user to mark complete</span>
              </label>
            </div>
          </div>

          {/* Submit error */}
          {submitError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
              {submitError}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-zinc-600 hover:text-zinc-900"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending}
              className="px-4 py-2 bg-zinc-900 text-white text-sm font-medium rounded-md hover:bg-zinc-700 disabled:opacity-50"
            >
              {isPending ? "Saving…" : isEdit ? "Save changes" : "Create schedule"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
