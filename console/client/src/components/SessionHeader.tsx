import { useState, useRef, type KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { Session, SessionState } from "../api/sessions.ts";
import { patchSession, deleteSession, interrupt, resume } from "../api/sessions.ts";
import { TaskStatusBadge } from "./TaskStatusBadge.tsx";
import type { Task, TaskStatus } from "../api/tasks.ts";
import { completeTask } from "../api/tasks.ts";
import { ConfirmModal } from "./ConfirmModal.tsx";
import { ShareSessionModal } from "./ShareSessionModal.tsx";
import { ApiError } from "../lib/api.ts";

interface SessionHeaderProps {
  session: Session;
  state: SessionState | null;
  isOwner: boolean;
  currentTask?: Task;
  onSessionUpdated: (updated: Session) => void;
  onTaskUpdated?: () => void;
  showInspector?: boolean;
  onToggleInspector?: () => void;
}

function statusToTaskStatus(status: string): TaskStatus {
  const valid: TaskStatus[] = [
    "pending", "running", "busy", "waiting_input",
    "completed", "error", "interrupted", "cancelled",
  ];
  return (valid.includes(status as TaskStatus) ? status : "running") as TaskStatus;
}

export function SessionHeader({
  session,
  state,
  isOwner,
  currentTask,
  onSessionUpdated,
  onTaskUpdated,
  showInspector,
  onToggleInspector,
}: SessionHeaderProps) {
  const navigate = useNavigate();
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(session.title ?? "");
  const [deleting, setDeleting] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [markCompleting, setMarkCompleting] = useState(false);
  const titleInputRef = useRef<HTMLInputElement>(null);

  async function saveTitle() {
    const trimmed = titleDraft.trim();
    if (!trimmed) {
      setTitleDraft(session.title ?? "");
      setEditingTitle(false);
      return;
    }
    try {
      const updated = await patchSession(session.id, { title: trimmed });
      onSessionUpdated(updated.session);
    } catch {
      // revert
      setTitleDraft(session.title ?? "");
    }
    setEditingTitle(false);
  }

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      void saveTitle();
    } else if (e.key === "Escape") {
      setTitleDraft(session.title ?? "");
      setEditingTitle(false);
    }
  }

  async function handleConfirmDelete() {
    setConfirmDeleteOpen(false);
    setDeleting(true);
    try {
      await deleteSession(session.id);
      navigate(`/agents/${encodeURIComponent(session.agent_id)}`);
    } catch {
      setDeleting(false);
    }
  }

  async function handleInterrupt() {
    setActionPending(true);
    try {
      await interrupt(session.id);
    } catch {
      // ignore
    } finally {
      setActionPending(false);
    }
  }

  async function handleResume() {
    setActionPending(true);
    try {
      await resume(session.id);
    } catch {
      // ignore
    } finally {
      setActionPending(false);
    }
  }

  async function handleMarkComplete() {
    if (!currentTask || markCompleting) return;
    setMarkCompleting(true);
    try {
      await completeTask(currentTask.id);
      onTaskUpdated?.();
    } catch (err) {
      if (!(err instanceof ApiError)) {
        console.error("Failed to complete task:", err);
      }
    } finally {
      setMarkCompleting(false);
    }
  }

  const gatewayStatus = state?.status ?? "unknown";
  const taskStatus = currentTask?.status;
  // Show interrupt when gateway is actively processing OR when a manual task is still running
  const isGatewayActive = ["running", "busy", "waiting_input"].includes(gatewayStatus);
  const isManualTaskRunning = taskStatus === "running" && currentTask?.completion_mode === "manual";
  const isActive = isGatewayActive || isManualTaskRunning;
  const isInterrupted = gatewayStatus === "interrupted" || gatewayStatus === "error";

  // Manual completion mode button: show when task is manual and still running
  const isManualRunning =
    isOwner &&
    currentTask?.completion_mode === "manual" &&
    (currentTask.status === "running" || currentTask.status === "busy" || currentTask.status === "waiting_input");

  const displayTitle = session.title ?? `Session ${session.id}`;

  return (
    <>
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-200 bg-white flex-wrap">
        {/* Back */}
        <Link
          to={`/agents/${encodeURIComponent(session.agent_id)}`}
          className="text-xs text-zinc-400 hover:text-zinc-600 flex-shrink-0"
        >
          ←
        </Link>

        {/* Title */}
        {isOwner && editingTitle ? (
          <input
            ref={titleInputRef}
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={() => { void saveTitle(); }}
            onKeyDown={handleTitleKeyDown}
            autoFocus
            className="text-sm font-semibold text-zinc-900 bg-white border border-zinc-300 rounded px-2 py-0.5 focus:outline-none focus:ring-2 focus:ring-zinc-400 min-w-0 flex-1 max-w-xs"
          />
        ) : (
          <h1
            className={`text-sm font-semibold text-zinc-900 truncate flex-1 min-w-0 max-w-xs ${
              isOwner ? "cursor-pointer hover:text-zinc-600" : ""
            }`}
            title={isOwner ? "Click to edit title" : undefined}
            onClick={() => {
              if (isOwner) {
                setTitleDraft(session.title ?? "");
                setEditingTitle(true);
              }
            }}
          >
            {displayTitle}
          </h1>
        )}

        {/* Agent link */}
        <Link
          to={`/agents/${encodeURIComponent(session.agent_id)}`}
          className="text-xs text-blue-600 hover:text-blue-800 flex-shrink-0"
        >
          {session.agent_id}
        </Link>

        {/* Status badge */}
        {state && (
          <TaskStatusBadge status={statusToTaskStatus(gatewayStatus)} />
        )}

        <div className="flex-1" />

        {/* Action buttons */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Inspector toggle */}
          {onToggleInspector && (
            <button
              onClick={onToggleInspector}
              className={`text-xs px-2.5 py-1 border rounded-md transition-colors ${
                showInspector
                  ? "border-zinc-500 bg-zinc-100 text-zinc-700"
                  : "border-zinc-300 text-zinc-500 hover:bg-zinc-50"
              }`}
              title="Toggle inspector"
            >
              &#9432;
            </button>
          )}

          {/* Share button */}
          <button
            onClick={() => setShareOpen(true)}
            className="text-xs px-2.5 py-1 border border-zinc-300 rounded-md text-zinc-600 hover:bg-zinc-50"
            title="Share session"
          >
            Share
          </button>

          {/* Manual complete button */}
          {isManualRunning && (
            <button
              onClick={() => { void handleMarkComplete(); }}
              disabled={markCompleting}
              className="text-xs px-2.5 py-1 border border-emerald-400 rounded-md text-emerald-700 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50 transition-colors"
              title="Mark this task as complete"
            >
              {markCompleting ? "Completing…" : "✓ Mark complete"}
            </button>
          )}

          {/* Owner-only actions */}
          {isOwner && (
            <>
              {isActive && (
                <button
                  onClick={() => { void handleInterrupt(); }}
                  disabled={actionPending}
                  className="text-xs px-2.5 py-1 border border-zinc-300 rounded-md text-zinc-600 hover:bg-zinc-50 disabled:opacity-50"
                >
                  Interrupt
                </button>
              )}
              {isInterrupted && (
                <button
                  onClick={() => { void handleResume(); }}
                  disabled={actionPending}
                  className="text-xs px-2.5 py-1 border border-zinc-300 rounded-md text-zinc-600 hover:bg-zinc-50 disabled:opacity-50"
                >
                  Resume
                </button>
              )}
              <button
                onClick={() => setConfirmDeleteOpen(true)}
                disabled={deleting}
                className="text-xs px-2.5 py-1 rounded-md disabled:opacity-50 border border-zinc-300 text-zinc-600 hover:bg-red-50 hover:border-red-300 hover:text-red-600 transition-colors"
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Delete confirmation modal */}
      <ConfirmModal
        open={confirmDeleteOpen}
        title="Delete session?"
        message="This will permanently delete the session and all its tasks and events. This cannot be undone."
        confirmLabel="Delete session"
        destructive
        onConfirm={() => { void handleConfirmDelete(); }}
        onClose={() => setConfirmDeleteOpen(false)}
      />

      {/* Share modal */}
      <ShareSessionModal
        open={shareOpen}
        session={session}
        isOwner={isOwner}
        onClose={() => setShareOpen(false)}
      />
    </>
  );
}
