import { useState, useRef, useEffect, type KeyboardEvent } from "react";
import { sendGuidance, startNewTask, resume } from "../api/sessions.ts";
import { completeTask } from "../api/tasks.ts";
import { ApiError } from "../lib/api.ts";

type CurrentTaskStatus = "none" | "pending" | "running" | "busy" | "waiting_input" | "completed" | "error" | "interrupted" | "cancelled";

interface ComposerProps {
  sessionId: number;
  currentTaskStatus: CurrentTaskStatus;
  currentTaskId?: number;
  currentTaskCompletionMode?: 'auto' | 'manual';
  pendingCount: number;
  isOwner: boolean;
  onSent: () => void;
  prefillText?: string | null;
  onPrefillConsumed?: () => void;
}

const TERMINAL_STATUSES = new Set(["completed", "error", "interrupted", "cancelled"]);

export function Composer({ sessionId, currentTaskStatus, currentTaskId, currentTaskCompletionMode, pendingCount, isOwner, onSent, prefillText, onPrefillConsumed }: ComposerProps) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // When prefillText changes, set the textarea value, focus, and auto-submit
  useEffect(() => {
    if (prefillText != null) {
      setText(prefillText);
      onPrefillConsumed?.();
      textareaRef.current?.focus();
      // Auto-submit the pre-filled answer after a brief delay to allow state to settle
      if (prefillText.trim()) {
        const timer = setTimeout(() => {
          void (async () => {
            try {
              if (isWaiting) {
                await sendGuidance(sessionId, prefillText.trim());
              } else {
                await sendGuidance(sessionId, prefillText.trim());
              }
              setText("");
              onSent();
            } catch {}
          })();
        }, 300);
        return () => clearTimeout(timer);
      }
    }
  }, [prefillText, onPrefillConsumed]);

  // Manual-mode task that's still "running" after agent responded = treat like waiting for input
  const isManualRunning =
    currentTaskCompletionMode === "manual" &&
    (currentTaskStatus === "running" || currentTaskStatus === "busy" || currentTaskStatus === "waiting_input");

  const isWaiting = currentTaskStatus === "waiting_input" || isManualRunning;
  const isInterrupted = currentTaskStatus === "interrupted";
  const isRunning = !isManualRunning && (currentTaskStatus === "running" || currentTaskStatus === "busy");
  const isTerminal = (TERMINAL_STATUSES.has(currentTaskStatus) && !isInterrupted) || currentTaskStatus === "none";
  const hasPending = currentTaskStatus === "pending" && pendingCount > 0;

  async function handleMarkComplete() {
    if (!currentTaskId || completing) return;
    setCompleting(true);
    setError(null);
    try {
      await completeTask(currentTaskId);
      onSent(); // refetch
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      setCompleting(false);
    }
  }

  let label: string;
  let chip: React.ReactNode;
  let chipColor: string;

  if (!isOwner) {
    label = "View only";
    chip = null;
    chipColor = "";
  } else if (isWaiting) {
    label = isManualRunning ? "Send guidance" : "Reply to agent";
    chip = (
      <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">
        {isManualRunning ? "✋ Manual task — guide or mark complete" : "⚠ Agent is waiting"}
      </span>
    );
    chipColor = "border-amber-300 focus-within:ring-amber-200";
  } else if (isInterrupted) {
    label = "Send to resume";
    chip = (
      <span className="text-xs px-2 py-0.5 rounded-full bg-zinc-100 text-zinc-600 font-medium">
        ⏸ Task interrupted — send a message to resume
      </span>
    );
    chipColor = "border-zinc-300 focus-within:ring-zinc-200";
  } else if (isRunning) {
    label = "Send guidance";
    chip = (
      <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">
        ⏳ Agent working — message appends to current task
      </span>
    );
    chipColor = "";
  } else if (hasPending) {
    label = "Queue next task";
    chip = (
      <span className="text-xs px-2 py-0.5 rounded-full bg-zinc-100 text-zinc-600 font-medium">
        ⏳ {pendingCount} task{pendingCount > 1 ? "s" : ""} queued
      </span>
    );
    chipColor = "";
  } else if (isTerminal) {
    label = "Continue task";
    chip = (
      <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium">
        ✓ Task done — send to continue
      </span>
    );
    chipColor = "";
  } else {
    label = "Queue next task";
    chip = null;
    chipColor = "";
  }

  async function handleSubmit() {
    if (!text.trim() || submitting || !isOwner) return;

    setError(null);
    setSubmitting(true);

    try {
      if (isWaiting) {
        await sendGuidance(sessionId, text.trim());
      } else if (isInterrupted) {
        await resume(sessionId, text.trim());
      } else if (currentTaskStatus === "none") {
        // No task at all — this shouldn't normally happen in an existing session
        await startNewTask(sessionId, text.trim());
      } else {
        // Task is completed/error/cancelled — send guidance to reopen it
        await sendGuidance(sessionId, text.trim());
      }
      setText("");
      onSent();
      textareaRef.current?.focus();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("State changed — please try again.");
        onSent(); // refetch to refresh state
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      void handleSubmit();
    }
  }

  if (!isOwner) {
    return (
      <div className="border-t border-zinc-200 bg-white px-4 py-3">
        <div className="text-sm text-zinc-400 text-center py-2">
          View only — contact owner to interact
        </div>
      </div>
    );
  }

  return (
    <div className="border-t border-zinc-200 bg-white px-4 py-3">
      {/* Status chip */}
      {chip && (
        <div className="mb-2 flex items-center gap-2">
          {isWaiting && (
            <div className="w-full flex items-center gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700 mb-1">
              <span>⚠</span>
              <span>Agent is waiting for your input</span>
            </div>
          )}
          {!isWaiting && chip}
        </div>
      )}

      {/* Textarea */}
      <div
        className={`flex gap-2 border rounded-xl overflow-hidden bg-white focus-within:ring-2 focus-within:ring-zinc-300 ${chipColor}`}
      >
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={label + "…"}
          rows={3}
          disabled={submitting}
          className="flex-1 px-3 py-2.5 text-sm resize-none focus:outline-none disabled:opacity-50 bg-transparent"
        />
        <div className="flex flex-col justify-end p-2 gap-1">
          {isManualRunning && (
            <button
              onClick={() => { void handleMarkComplete(); }}
              disabled={completing}
              className="px-3 py-2 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              title="Mark this task as complete"
            >
              {completing ? "…" : "Mark complete"}
            </button>
          )}
          <button
            onClick={() => { void handleSubmit(); }}
            disabled={submitting || !text.trim()}
            className="px-3 py-2 bg-zinc-900 text-white text-xs font-medium rounded-lg hover:bg-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
          >
            {submitting ? "…" : (isWaiting ? "Reply" : "Send")}
          </button>
          <span className="text-[10px] text-zinc-400 text-center mt-1">⌘↵</span>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mt-2 px-3 py-2 bg-red-50 border border-red-200 rounded text-xs text-red-600">
          {error}
        </div>
      )}
    </div>
  );
}
