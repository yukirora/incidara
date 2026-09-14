import { useState, useEffect, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listSessions, createSession, createTask, type Session } from "../api/sessions.ts";
import { ApiError } from "../lib/api.ts";
import { ScheduleFormModal } from "./ScheduleFormModal.tsx";

interface NewTaskFormProps {
  agentId: string;
}

export function NewTaskForm({ agentId }: NewTaskFormProps) {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [completionMode, setCompletionMode] = useState<"auto" | "manual">("auto");
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [selectedSession, setSelectedSession] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [scheduleSaved, setScheduleSaved] = useState(false);

  const { data: sessionsData, isLoading: sessionsLoading } = useQuery<Session[]>({
    queryKey: ["sessions", agentId, "mine"],
    queryFn: () => listSessions({ agent_id: agentId, scope: "mine" }),
    staleTime: 15_000,
    enabled: mode === "existing",
  });

  const sessions = sessionsData ?? [];

  // Auto-select first session when switching to existing mode
  useEffect(() => {
    if (mode === "existing" && sessions.length > 0 && !selectedSession) {
      setSelectedSession(String(sessions[0].id));
    }
  }, [mode, sessions, selectedSession]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;

    setError(null);
    setSubmitting(true);

    try {
      if (mode === "new") {
        const result = await createSession(agentId, {
          prompt: prompt.trim(),
          title: taskTitle.trim() || undefined,
          completion_mode: completionMode,
        });
        navigate(`/sessions/${result.session.id}`);
      } else {
        if (!selectedSession) {
          setError("Please select a session.");
          return;
        }
        const result = await createTask(parseInt(selectedSession, 10), {
          prompt: prompt.trim(),
          title: taskTitle.trim() || undefined,
          completion_mode: completionMode,
        });
        navigate(`/sessions/${selectedSession}#task-${result.task.id}`);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4">
      {/* Title (optional) */}
      <div>
        <label
          htmlFor="task-title"
          className="block text-sm font-medium text-zinc-700 mb-1"
        >
          Title <span className="text-zinc-400 font-normal">(optional)</span>
        </label>
        <input
          id="task-title"
          type="text"
          value={taskTitle}
          onChange={(e) => setTaskTitle(e.target.value)}
          className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500 focus:border-transparent"
          placeholder="e.g. Fix login bug"
        />
      </div>

      {/* Prompt */}
      <div>
        <label
          htmlFor="prompt"
          className="block text-sm font-medium text-zinc-700 mb-1"
        >
          Prompt
        </label>
        <textarea
          id="prompt"
          required
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500 focus:border-transparent resize-vertical"
          placeholder="Describe the task for the agent…"
        />
      </div>

      {/* Completion mode */}
      <div>
        <p className="text-sm font-medium text-zinc-700 mb-2">Completion</p>
        <div className="flex flex-col gap-2">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="completion-mode"
              value="auto"
              checked={completionMode === "auto"}
              onChange={() => setCompletionMode("auto")}
              className="text-zinc-900"
            />
            <span className="text-sm text-zinc-700">Auto-complete (agent decides)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="completion-mode"
              value="manual"
              checked={completionMode === "manual"}
              onChange={() => setCompletionMode("manual")}
              className="text-zinc-900"
            />
            <span className="text-sm text-zinc-700">Manual (I'll decide when done)</span>
          </label>
        </div>
      </div>

      {/* Session mode */}
      <div>
        <p className="text-sm font-medium text-zinc-700 mb-2">Session</p>
        <div className="flex gap-4">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="session-mode"
              value="new"
              checked={mode === "new"}
              onChange={() => setMode("new")}
              className="text-zinc-900"
            />
            <span className="text-sm text-zinc-700">New session</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="session-mode"
              value="existing"
              checked={mode === "existing"}
              onChange={() => setMode("existing")}
              className="text-zinc-900"
            />
            <span className="text-sm text-zinc-700">Existing session</span>
          </label>
        </div>
      </div>

      {/* Existing session picker */}
      {mode === "existing" && (
        <div>
          <label
            htmlFor="session-select"
            className="block text-sm font-medium text-zinc-700 mb-1"
          >
            Select session
          </label>
          {sessionsLoading ? (
            <p className="text-sm text-zinc-400">Loading sessions…</p>
          ) : sessions.length === 0 ? (
            <p className="text-sm text-zinc-400">No sessions yet. Use "New session" instead.</p>
          ) : (
            <select
              id="session-select"
              value={selectedSession}
              onChange={(e) => setSelectedSession(e.target.value)}
              className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
            >
              {sessions.map((s) => (
                <option key={s.id} value={String(s.id)}>
                  {s.title ?? `Session ${s.id}`} (
                  {new Date(s.created_at).toLocaleDateString()})
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Submit row */}
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={submitting || !prompt.trim() || (mode === "existing" && sessions.length === 0)}
          className="flex-1 py-2 px-4 bg-zinc-900 text-white text-sm font-medium rounded-md hover:bg-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {submitting ? "Starting…" : "Start task"}
        </button>
        <button
          type="button"
          disabled={!prompt.trim()}
          onClick={() => { setScheduleSaved(false); setScheduleModalOpen(true); }}
          className="py-2 px-4 border border-zinc-300 text-zinc-700 text-sm font-medium rounded-md hover:bg-zinc-50 focus:outline-none focus:ring-2 focus:ring-zinc-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          title="Schedule this task to run on a recurring or future trigger"
        >
          &#9201; Schedule
        </button>
      </div>

      {scheduleSaved && (
        <div className="p-3 bg-emerald-50 border border-emerald-200 rounded text-sm text-emerald-700">
          Schedule saved! You can manage it on the <a href="/schedules" className="underline">Schedules</a> page.
        </div>
      )}

      <ScheduleFormModal
        open={scheduleModalOpen}
        onClose={() => {
          setScheduleModalOpen(false);
          setScheduleSaved(true);
        }}
        initialAgentId={agentId}
        initialPrompt={prompt.trim()}
      />
    </form>
  );
}
