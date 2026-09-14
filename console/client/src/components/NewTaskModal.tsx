import { useState, useEffect, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAgents } from "../hooks/useAgents.ts";
import { listSessions, createSession, createTask, type Session } from "../api/sessions.ts";
import { ApiError } from "../lib/api.ts";
import { useUIState } from "../lib/store.ts";

export function NewTaskModal() {
  const navigate = useNavigate();
  const { newTaskModalOpen, newTaskPrefilledAgentId, closeNewTaskModal } = useUIState();
  const { agents } = useAgents();

  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [completionMode, setCompletionMode] = useState<"auto" | "manual">("auto");
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [selectedSessionId, setSelectedSessionId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // When modal opens, prefill agent if provided
  useEffect(() => {
    if (newTaskModalOpen) {
      setSelectedAgentId(newTaskPrefilledAgentId ?? (agents[0]?.id ?? ""));
      setPrompt("");
      setTaskTitle("");
      setCompletionMode("auto");
      setMode("new");
      setSelectedSessionId("");
      setError(null);
    }
  }, [newTaskModalOpen, newTaskPrefilledAgentId, agents]);

  // Update agent if agents load after modal opens
  useEffect(() => {
    if (newTaskModalOpen && !selectedAgentId && agents.length > 0) {
      setSelectedAgentId(agents[0].id);
    }
  }, [agents, newTaskModalOpen, selectedAgentId]);

  const { data: sessionsData, isLoading: sessionsLoading } = useQuery<Session[]>({
    queryKey: ["sessions", selectedAgentId, "mine"],
    queryFn: () => listSessions({ agent_id: selectedAgentId, scope: "mine" }),
    staleTime: 15_000,
    enabled: newTaskModalOpen && mode === "existing" && !!selectedAgentId,
  });

  const sessions = sessionsData ?? [];

  useEffect(() => {
    if (mode === "existing" && sessions.length > 0 && !selectedSessionId) {
      setSelectedSessionId(String(sessions[0].id));
    }
  }, [mode, sessions, selectedSessionId]);

  // Reset selected session when agent changes
  useEffect(() => {
    setSelectedSessionId("");
  }, [selectedAgentId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!prompt.trim() || !selectedAgentId) return;

    setError(null);
    setSubmitting(true);

    try {
      if (mode === "new") {
        const result = await createSession(selectedAgentId, {
          prompt: prompt.trim(),
          title: taskTitle.trim() || undefined,
          completion_mode: completionMode,
        });
        closeNewTaskModal();
        navigate(`/sessions/${result.session.id}`);
      } else {
        if (!selectedSessionId) {
          setError("Please select a session.");
          setSubmitting(false);
          return;
        }
        const result = await createTask(parseInt(selectedSessionId, 10), {
          prompt: prompt.trim(),
          title: taskTitle.trim() || undefined,
          completion_mode: completionMode,
        });
        closeNewTaskModal();
        navigate(`/sessions/${selectedSessionId}#task-${result.task.id}`);
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

  if (!newTaskModalOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) closeNewTaskModal(); }}
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-zinc-900">New task</h2>
          <button
            onClick={closeNewTaskModal}
            className="text-zinc-400 hover:text-zinc-600 text-lg leading-none"
          >
            &times;
          </button>
        </div>

        <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4">
          {/* Agent picker */}
          <div>
            <label className="block text-sm font-medium text-zinc-700 mb-1">Agent</label>
            {agents.length === 0 ? (
              <p className="text-sm text-zinc-400">No agents available.</p>
            ) : (
              <select
                value={selectedAgentId}
                onChange={(e) => setSelectedAgentId(e.target.value)}
                className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
              >
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Title (optional) */}
          <div>
            <label className="block text-sm font-medium text-zinc-700 mb-1">
              Title <span className="text-zinc-400 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={taskTitle}
              onChange={(e) => setTaskTitle(e.target.value)}
              className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
              placeholder="e.g. Fix login bug"
            />
          </div>

          {/* Prompt */}
          <div>
            <label className="block text-sm font-medium text-zinc-700 mb-1">Prompt</label>
            <textarea
              required
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500 resize-none"
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
                  name="modal-completion-mode"
                  value="auto"
                  checked={completionMode === "auto"}
                  onChange={() => setCompletionMode("auto")}
                />
                <span className="text-sm text-zinc-700">Auto-complete (agent decides)</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="modal-completion-mode"
                  value="manual"
                  checked={completionMode === "manual"}
                  onChange={() => setCompletionMode("manual")}
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
                  name="modal-session-mode"
                  value="new"
                  checked={mode === "new"}
                  onChange={() => setMode("new")}
                />
                <span className="text-sm text-zinc-700">New session</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="modal-session-mode"
                  value="existing"
                  checked={mode === "existing"}
                  onChange={() => setMode("existing")}
                />
                <span className="text-sm text-zinc-700">Existing session</span>
              </label>
            </div>
          </div>

          {/* Existing session picker */}
          {mode === "existing" && (
            <div>
              <label className="block text-sm font-medium text-zinc-700 mb-1">Select session</label>
              {sessionsLoading ? (
                <p className="text-sm text-zinc-400">Loading sessions…</p>
              ) : sessions.length === 0 ? (
                <p className="text-sm text-zinc-400">No sessions yet. Use "New session" instead.</p>
              ) : (
                <select
                  value={selectedSessionId}
                  onChange={(e) => setSelectedSessionId(e.target.value)}
                  className="w-full px-3 py-2 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-zinc-500"
                >
                  {sessions.map((s) => (
                    <option key={s.id} value={String(s.id)}>
                      {s.title ?? `Session ${s.id}`} ({new Date(s.created_at).toLocaleDateString()})
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
              {error}
            </div>
          )}

          <div className="flex gap-3 justify-end pt-2">
            <button
              type="button"
              onClick={closeNewTaskModal}
              className="px-4 py-2 text-sm text-zinc-600 border border-zinc-300 rounded-md hover:bg-zinc-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !prompt.trim() || !selectedAgentId || (mode === "existing" && sessions.length === 0)}
              className="px-4 py-2 text-sm bg-zinc-900 text-white rounded-md hover:bg-zinc-700 disabled:opacity-50"
            >
              {submitting ? "Starting…" : "Start task"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
