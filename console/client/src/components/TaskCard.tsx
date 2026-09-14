import { useState, useRef, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import type { Task } from "../api/tasks.ts";
import type { GatewayEvent } from "../api/sessions.ts";
import { approvePermission, denyPermission, sendGuidance, getSubagentTranscript, resume } from "../api/sessions.ts";
import { TaskUsageSection } from "./TaskUsageSection.tsx";
import type { SubagentTranscript } from "../api/sessions.ts";
import { TaskStatusBadge } from "./TaskStatusBadge.tsx";
import { MessageBubble } from "./MessageBubble.tsx";
import { ToolCard } from "./ToolCard.tsx";
import { cancelPendingTask, completeTask } from "../api/tasks.ts";
import { useSchedule } from "../hooks/useSchedules.ts";

interface TaskCardProps {
  task: Task;
  taskIndex: number;
  events: GatewayEvent[];
  isLatest: boolean;
  sessionId: number;
  canSend: boolean;
  onCancel?: () => void;
  highlight?: boolean;
  onAutoFillComposer?: (text: string) => void;
}

interface ToolState {
  toolName: string;
  toolUseId: string;
  stdout: string;
  status: "running" | "completed" | "error";
  result?: string;
  mcpProgress?: { progress: number; total: number; message: string };
}

const TERMINAL_STATUSES = new Set(["completed", "error", "interrupted", "cancelled"]);

export function TaskCard({ task, taskIndex, events, isLatest, sessionId, canSend, onCancel, highlight, onAutoFillComposer }: TaskCardProps) {
  const navigate = useNavigate();
  const isTerminal = TERMINAL_STATUSES.has(task.status);
  const isPending = task.status === "pending";
  const [collapsed, setCollapsed] = useState(isTerminal && !isLatest);
  const [cancelling, setCancelling] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [resuming, setResuming] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { schedule } = useSchedule(task.from_schedule_id ?? null);

  useEffect(() => {
    if (highlight && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    } else if (highlight && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [highlight]);

  async function handleCancel() {
    setCancelling(true);
    try {
      await cancelPendingTask(task.id);
      onCancel?.();
    } catch {
      // ignore
    } finally {
      setCancelling(false);
    }
  }

  async function handleMarkComplete() {
    setCompleting(true);
    try {
      await completeTask(task.id);
    } catch {
      // ignore
    } finally {
      setCompleting(false);
    }
  }

  async function handleResume() {
    setResuming(true);
    try {
      await resume(sessionId);
    } catch {
      // ignore
    } finally {
      setResuming(false);
    }
  }

  // Render the event trace
  function renderEvents() {
    const visibleEvents = events;
    const toolMap = new Map<string, ToolState>();
    const rendered: React.ReactNode[] = [];
    const subagentGroups = new Map<string, GatewayEvent[]>(); // parent tool_use_id → sub-agent events
    let streamingText = "";
    let streamingThinking = "";
    let streamingMessageId: string | null = null;

    // Helper: flush accumulated thinking into a collapsible block
    function flushThinking(key: string) {
      if (streamingThinking) {
        rendered.push(<ThinkingBlock key={key} text={streamingThinking} />);
        streamingThinking = "";
      }
    }

    for (let i = 0; i < visibleEvents.length; i++) {
      const evt = visibleEvents[i];
      const payload = evt.payload as Record<string, unknown>;

      if (evt.event_type === "message.user") {
        flushThinking(`think-before-user-${evt.seq}`);
        const content = String(payload.content ?? "");
        const userName = payload.user_name ? String(payload.user_name) : undefined;
        rendered.push(
          <MessageBubble key={`user-${evt.seq}`} role="user" text={content} timestamp={evt.timestamp} userName={userName} />
        );
      } else if (evt.event_type === "tool.started") {
        flushThinking(`think-before-tool-${evt.seq}`);
        const toolUseId = String(payload.tool_use_id ?? evt.seq);
        const toolName = String(payload.tool_name ?? "unknown");
        const tool: ToolState = { toolName, toolUseId, stdout: "", status: "running" };
        toolMap.set(toolUseId, tool);
        rendered.push(
          <ToolCardLive key={`tool-${toolUseId}`} toolUseId={toolUseId} toolMap={toolMap} events={events} startSeq={evt.seq} />
        );
        // If this is an Agent tool, inject sub-agent group panel after it
        if (toolName === "Agent") {
          rendered.push(
            <SubagentGroup key={`subgroup-${toolUseId}`} parentToolUseId={toolUseId} events={events} sessionId={sessionId} />
          );
        }
      } else if (evt.event_type === "tool.stdout") {
        continue;
      } else if (evt.event_type === "tool.finished") {
        // Orphaned tool.finished (from sub-agent internal tools that have no tool.started)
        const evtToolId = String(payload.tool_use_id ?? "");
        if (evtToolId && !toolMap.has(evtToolId)) {
          // This tool.finished has no matching tool.started — render as a compact result
          const subToolName = String(payload.tool_name ?? "");
          const subResult = String(payload.result ?? payload.output ?? "");
          const isErr = payload.error === true;
          if (subResult) {
            rendered.push(
              <ToolCard
                key={`orphan-tool-${evtToolId}`}
                toolName={subToolName || "sub-agent tool"}
                toolUseId={evtToolId}
                toolInput=""
                stdout=""
                status={isErr ? "error" : "completed"}
                result={subResult}
              />
            );
          }
          toolMap.set(evtToolId, { toolName: subToolName, toolUseId: evtToolId, stdout: "", status: isErr ? "error" : "completed" });
        }
        continue;
      } else if (evt.event_type === "message.delta") {
        const text = String(payload.text ?? "");
        const kind = payload.kind as string | undefined;
        if (text) {
          if (kind === "thinking") {
            streamingThinking += text;
          } else {
            if (streamingMessageId === null) {
              streamingMessageId = `delta-${evt.seq}`;
            }
            streamingText += text;
          }
        }
      } else if (evt.event_type === "message.agent") {
        flushThinking(`think-before-agent-${evt.seq}`);
        const content = String(payload.content ?? streamingText);
        const text = content || streamingText;
        if (text) {
          rendered.push(
            <MessageBubble key={`agent-${evt.seq}`} role="assistant" text={text} timestamp={evt.timestamp} />
          );
        }
        streamingText = "";
        streamingMessageId = null;
      } else if (evt.event_type === "permission.requested") {
        const permPayload = payload as any;
        const toolName = String(permPayload.tool_name ?? "tool");
        if (permPayload.awaiting_approval) {
          // Check if this permission has been resolved already
          const resolved = visibleEvents.some(
            (e) =>
              e.event_type === "permission.resolved" &&
              (e.payload as any).permission_id === permPayload.permission_id,
          );
          // If this is AskUserQuestion, show a special question card
          if (toolName === "AskUserQuestion") {
            const input = permPayload.input;
            let questions: Array<any> | undefined;
            if (input) {
              try {
                const parsed = typeof input === "string" ? JSON.parse(input) : input;
                questions = parsed.questions;
              } catch {}
            }
            if (questions && questions.length > 0) {
              rendered.push(
                <QuestionCard
                  key={`ask-${evt.seq}`}
                  questions={questions}
                  sessionId={sessionId}
                  permissionId={String(permPayload.permission_id)}
                  resolved={resolved}
                  onSelectAnswer={(answer: string) => onAutoFillComposer?.(answer)}
                />,
              );
            } else {
              rendered.push(
                <PermissionApprovalCard
                  key={`perm-${evt.seq}`}
                  sessionId={sessionId}
                  permissionId={String(permPayload.permission_id)}
                  toolName={toolName}
                  toolInput={permPayload.input}
                  resolved={resolved}
                  canSend={canSend}
                  onDecision={onCancel}
                />,
              );
            }
          } else {
            rendered.push(
              <PermissionApprovalCard
                key={`perm-${evt.seq}`}
                sessionId={sessionId}
                permissionId={String(permPayload.permission_id)}
                toolName={toolName}
                toolInput={permPayload.input}
                resolved={resolved}
                canSend={canSend}
                onDecision={onCancel}
              />,
            );
          }
        } else {
          // Auto-decided permission — just show the badge
          const toolName = String(permPayload.tool_name ?? "tool");
          const autoDecision = permPayload.auto_decision ?? "allow";
          rendered.push(
            <div key={`perm-${evt.seq}`} className="text-xs text-zinc-500 bg-zinc-50 border border-zinc-200 rounded px-2 py-1 mb-2">
              {autoDecision === "allow" ? "✓" : "✗"} Auto-{autoDecision}: {toolName}
            </div>,
          );
        }
      } else if (evt.event_type === "permission.resolved") {
        // Only show resolved events that don't have a corresponding awaiting_approval request
        // (i.e., don't double-render for events handled by PermissionApprovalCard)
        const hasAwaitingRequest = visibleEvents.some(
          (e) =>
            e.event_type === "permission.requested" &&
            (e.payload as any).awaiting_approval === true &&
            (e.payload as any).permission_id === payload.permission_id,
        );
        if (!hasAwaitingRequest) {
          rendered.push(
            <div key={`permr-${evt.seq}`} className="text-xs text-zinc-500 bg-zinc-50 border border-zinc-200 rounded px-2 py-1 mb-2">
              Permission granted: {String(payload.decision ?? "allow")}
            </div>,
          );
        }
      } else if (evt.event_type === "question.requested") {
        // AskUserQuestion was detected — render QuestionCard
        // The questions data comes from the tool.started event's accumulated input
        const qPayload = payload as any;
        const toolUseId = String(qPayload.tool_use_id ?? "");

        // Find the matching tool.started event to get the questions input
        const toolStarted = visibleEvents.find(
          (e) =>
            e.event_type === "tool.started" &&
            (e.payload as any).tool_use_id === toolUseId &&
            (e.payload as any).tool_name === "AskUserQuestion",
        );
        const toolInput = (toolStarted?.payload as any)?.input;
        let questions: Array<any> | undefined = qPayload.questions;
        if ((!questions || questions.length === 0) && toolInput) {
          try {
            const parsed = typeof toolInput === "string" ? JSON.parse(toolInput) : toolInput;
            questions = parsed.questions;
          } catch {}
        }

        // Also try to reconstruct from tool.stdout partials if input not yet merged
        if (!questions && toolUseId) {
          let partialJson = "";
          for (const e of visibleEvents) {
            if (e.event_type === "tool.stdout" && e.seq >= (evt.seq - 50)) {
              const p = e.payload as any;
              if (p.partial) partialJson += String(p.partial);
            }
          }
          if (partialJson) {
            try {
              const parsed = JSON.parse(partialJson);
              questions = parsed.questions;
            } catch {}
          }
        }

        if (questions && questions.length > 0) {
          // Legacy path — just auto-fill composer, don't render QuestionCard without full props
          if (onAutoFillComposer && questions[0]?.options?.[0]?.label) {
            // No-op: let the permission.requested path handle rendering
          }
        }
      } else if (evt.event_type === "question.answered") {
        // Don't render answered events separately
      } else if (evt.event_type.startsWith("subagent.")) {
        // Collect sub-agent events — they're rendered grouped after the loop
        const parentToolUseId = String(payload.parent_tool_use_id ?? payload.tool_use_id ?? "");
        if (!subagentGroups.has(parentToolUseId)) {
          subagentGroups.set(parentToolUseId, []);
        }
        subagentGroups.get(parentToolUseId)!.push(evt);
      }
    }

    // Flush any remaining thinking
    flushThinking("think-trailing");

    // Streaming assistant message in progress
    if (streamingText) {
      rendered.push(
        <MessageBubble
          key={`streaming-${streamingMessageId}`}
          role="assistant"
          text={streamingText}
          streaming
        />
      );
    }

    return rendered;
  }

  const firstUserEvent = events.find((e) => e.event_type === "message.user");
  const firstUserPayload = firstUserEvent?.payload as Record<string, unknown> | undefined;
  const promptText =
    firstUserPayload?.content as string | undefined ?? task.prompt;
  const promptTimestamp = firstUserEvent?.timestamp ?? task.created_at;
  const promptUserName = firstUserPayload?.user_name as string | undefined;

  const isWaitingInput = task.status === "waiting_input" || task.status === "busy";

  const terminalEvent = events.find(
    (e) =>
      e.event_type === "session.completed" ||
      e.event_type === "session.error" ||
      e.event_type === "session.interrupted"
  );

  return (
    <div
      ref={cardRef}
      id={`task-${task.id}`}
      className={`rounded-xl border mb-4 overflow-hidden transition-all ${
        highlight ? "border-blue-400 ring-2 ring-blue-200" : "border-zinc-200"
      } bg-white shadow-sm`}
    >
      {/* Card header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-100 bg-zinc-50">
        {task.title ? (
          <span className="text-xs font-semibold text-zinc-700">{task.title}</span>
        ) : (
          <span className="text-xs font-semibold text-zinc-500">Task {taskIndex + 1}</span>
        )}
        <TaskStatusBadge status={task.status} />
        {task.completion_mode === "manual" && (
          <span className="text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 border border-blue-200 font-medium">
            Manual
          </span>
        )}
        {task.from_schedule_id && (
          <button
            onClick={() => navigate("/schedules")}
            className="text-xs bg-violet-50 text-violet-600 border border-violet-200 px-1.5 py-0.5 rounded hover:bg-violet-100 transition-colors"
            title={`From schedule: ${schedule?.name ?? `#${task.from_schedule_id}`}`}
          >
            &#9201; {schedule?.name ?? `schedule #${task.from_schedule_id}`}
          </button>
        )}
        <div className="flex-1" />
        {isPending && (
          <button
            onClick={() => { void handleCancel(); }}
            disabled={cancelling}
            className="text-xs text-red-500 hover:text-red-700 disabled:opacity-50 px-2 py-0.5 rounded border border-red-200 hover:border-red-300"
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
        {isTerminal && (
          <button
            onClick={() => setCollapsed((v) => !v)}
            className="text-xs text-zinc-400 hover:text-zinc-600"
          >
            {collapsed ? "Expand" : "Collapse"}
          </button>
        )}
      </div>

      {/* Pending compact view */}
      {isPending ? (
        <div className="px-4 py-3">
          <p className="text-sm text-zinc-700 mb-1 line-clamp-2">{task.prompt}</p>
          <p className="text-xs text-zinc-400">Queued · will start when previous task completes</p>
        </div>
      ) : collapsed ? (
        /* Collapsed terminal task */
        <div className="px-4 py-3">
          <p className="text-sm text-zinc-600 line-clamp-1 mb-1">{task.prompt}</p>
          {task.output_preview && (
            <p className="text-xs text-zinc-400 line-clamp-2">{task.output_preview}</p>
          )}
        </div>
      ) : (
        /* Full card */
        <div className="px-4 py-3">
          {/* Prompt bubble */}
          <MessageBubble role="user" text={promptText} timestamp={promptTimestamp} userName={promptUserName} />

          {/* Events */}
          {renderEvents()}

          {/* Waiting input banner */}
          {isWaitingInput && (
            <div className="mt-2 flex items-center gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700">
              <span>⚠</span>
              <span>Agent is waiting for your input</span>
              {task.completion_mode === "manual" && (
                <button
                  onClick={() => { void handleMarkComplete(); }}
                  disabled={completing}
                  className="ml-auto text-xs px-2.5 py-1 border border-emerald-400 rounded-md text-emerald-700 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50 transition-colors whitespace-nowrap"
                  title="Mark this task as complete"
                >
                  {completing ? "Completing…" : "✓ Mark complete"}
                </button>
              )}
            </div>
          )}

          {/* Interrupted banner */}
          {task.status === "interrupted" && (
            <div className="mt-2 flex items-center gap-2 px-3 py-2 bg-zinc-50 border border-zinc-200 rounded-lg text-sm text-zinc-600">
              <span>⏸</span>
              <span>Task was interrupted</span>
              <button
                onClick={() => { void handleResume(); }}
                disabled={resuming}
                className="text-xs px-2.5 py-1 border border-amber-400 rounded-md text-amber-700 bg-amber-50 hover:bg-amber-100 disabled:opacity-50 transition-colors whitespace-nowrap"
                title="Resume this interrupted task"
              >
                {resuming ? "Resuming…" : "↻ Resume"}
              </button>
              {task.completion_mode === "manual" && (
                <button
                  onClick={() => { void handleMarkComplete(); }}
                  disabled={completing}
                  className="text-xs px-2.5 py-1 border border-emerald-400 rounded-md text-emerald-700 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50 transition-colors whitespace-nowrap"
                  title="Mark this task as complete"
                >
                  {completing ? "Completing…" : "✓ Mark complete"}
                </button>
              )}
            </div>
          )}

          {/* Terminal footer */}
          {terminalEvent && (
            <div
              className={`mt-3 text-xs px-3 py-1.5 rounded border ${
                task.status === "completed"
                  ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                  : task.status === "interrupted"
                    ? "bg-zinc-50 border-zinc-200 text-zinc-500"
                    : "bg-red-50 border-red-200 text-red-600"
              }`}
            >
              {task.status === "completed" && "✓ Task completed"}
              {task.status === "error" && "✗ Task ended with error"}
              {task.status === "interrupted" && "⏸ Task interrupted"}
            </div>
          )}
        </div>
      )}
      {/* Task usage section - lazy loaded on expand */}
      {task.status !== "pending" && (
        <TaskUsageSection taskId={task.id} />
      )}
      <div ref={bottomRef} />
    </div>
  );
}

// Collapsible thinking block
function ThinkingBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="mb-2 border border-violet-200 rounded-lg bg-violet-50 text-xs overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 w-full px-3 py-1.5 text-left text-violet-600 hover:bg-violet-100 select-none"
      >
        <span>💭</span>
        <span className="font-medium">Thinking</span>
        <span className="text-violet-400 ml-1">{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded && (
        <div className="px-3 py-2 border-t border-violet-200 text-violet-700 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto">
          {text}
        </div>
      )}
    </div>
  );
}

// SubagentGroup: shows all sub-agents spawned by a parent Agent tool
// Compact summary by default, click ▶ to expand full details (tools, thinking, text)
function SubagentGroup({
  parentToolUseId,
  events,
  sessionId,
}: {
  parentToolUseId: string;
  events: GatewayEvent[];
  sessionId: number;
}) {
  // Per-task expanded state and transcript data
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set());
  const [transcripts, setTranscripts] = useState<Map<string, SubagentTranscript>>(new Map());
  const [loadingTasks, setLoadingTasks] = useState<Set<string>>(new Set());
  const pollingRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      for (const tid of pollingRef.current.values()) clearInterval(tid);
    };
  }, []);

  // Stop polling when a running sub-agent finishes
  useEffect(() => {
    for (const taskId of expandedTasks) {
      if (pollingRef.current.has(taskId)) {
        // Find task status — if not running, stop polling
        const task = taskMap.get(taskId);
        if (task && task.status !== "running") {
          stopPolling(taskId);
          // One final load to get completed transcript
          doLoad(taskId);
        }
      }
    }
  });

  const doLoad = useCallback((taskId: string) => {
    setLoadingTasks(l => { const n = new Set(l); n.add(taskId); return n; });
    getSubagentTranscript(sessionId, taskId)
      .then(data => {
        setTranscripts(t => { const n = new Map(t); n.set(taskId, data); return n; });
      })
      .catch(() => {})
      .finally(() => {
        setLoadingTasks(l => { const n = new Set(l); n.delete(taskId); return n; });
      });
  }, [sessionId]);

  const stopPolling = useCallback((taskId: string) => {
    const id = pollingRef.current.get(taskId);
    if (id) {
      clearInterval(id);
      pollingRef.current.delete(taskId);
    }
  }, []);

  const toggleExpand = useCallback((taskId: string, isRunning: boolean) => {
    setExpandedTasks(prev => {
      const next = new Set(prev);
      if (next.has(taskId)) {
        next.delete(taskId);
        stopPolling(taskId);
      } else {
        next.add(taskId);
        // Load transcript on expand
        doLoad(taskId);
        // If running, keep refreshing every 5s until collapsed or finished
        if (isRunning && !pollingRef.current.has(taskId)) {
          const id = setInterval(() => doLoad(taskId), 5000);
          pollingRef.current.set(taskId, id);
        }
      }
      return next;
    });
  }, [doLoad, stopPolling]);

  // Collect all sub-agent events for this parent tool, grouped by task_id
  const taskMap = new Map<string, {
    description: string;
    taskId: string;
    status: string;
    progress: string[];
    toolCalls: Array<{
      name: string;
      toolUseId: string;
      input: string;
      result: string;
      error: boolean;
      status: "running" | "completed" | "error";
    }>;
    deltas: Array<{ kind: string; text: string }>;
    summary: string;
    isBackground: boolean;
  }>();

  for (const evt of events) {
    const payload = evt.payload as Record<string, unknown>;
    const taskId = String(payload.task_id ?? "");

    if (evt.event_type === "subagent.started") {
      // Only include sub-agents spawned by THIS Agent tool
      if (String(payload.tool_use_id ?? "") !== parentToolUseId) continue;
      taskMap.set(taskId, {
        description: String(payload.description ?? payload.prompt ?? "Sub-agent"),
        taskId,
        status: "running",
        progress: [],
        toolCalls: [],
        deltas: [],
        summary: "",
        isBackground: false,
      });
    } else if (taskId && taskMap.has(taskId)) {
      // Match by task_id (preferred — works for progress/updated/finished/delta events)
      const task = taskMap.get(taskId)!;
      if (evt.event_type === "subagent.progress") {
        const summary = payload.summary ? String(payload.summary) : "";
        const lastTool = payload.last_tool_name ? String(payload.last_tool_name) : "";
        const usage = payload.usage as Record<string, number> | undefined;
        const duration = usage?.duration_ms ? `${Math.round(usage.duration_ms / 1000)}s` : "";
        if (summary || lastTool) {
          task.progress.push(summary || `Using ${lastTool}${duration ? ` (${duration})` : ""}`);
          if (task.progress.length > 3) task.progress = task.progress.slice(-3);
        }
      } else if (evt.event_type === "subagent.updated") {
        const patch = payload.patch as Record<string, unknown> | undefined;
        if (patch?.status) task.status = String(patch.status);
        if (patch?.is_backgrounded) task.isBackground = true;
      } else if (evt.event_type === "subagent.tool.started") {
        const input = payload.input
          ? (typeof payload.input === "string" ? payload.input : JSON.stringify(payload.input, null, 2))
          : "";
        task.toolCalls.push({
          name: String(payload.tool_name ?? "tool"),
          toolUseId: String(payload.tool_use_id ?? ""),
          input: input.slice(0, 500),
          result: "",
          error: false,
          status: "running",
        });
      } else if (evt.event_type === "subagent.tool.finished") {
        const tc = task.toolCalls.find(tc => tc.status === "running");
        if (tc) {
          tc.status = payload.error === true ? "error" : "completed";
          tc.result = String(payload.result ?? "").slice(0, 1000);
          tc.error = payload.error === true;
        }
      } else if (evt.event_type === "subagent.delta") {
        const kind = String(payload.kind ?? "text");
        const text = String(payload.text ?? "");
        if (text.trim()) {
          const last = task.deltas[task.deltas.length - 1];
          if (last && last.kind === kind) {
            last.text += text;
          } else {
            task.deltas.push({ kind, text });
          }
        }
      } else if (evt.event_type === "subagent.finished") {
        task.status = String(payload.status ?? "completed");
        task.summary = String(payload.summary ?? "");
      }
    } else if (!taskId && evt.event_type.startsWith("subagent.tool.")) {
      // Fallback: tool events with null task_id (older adapter versions)
      const evtParentId = String(payload.parent_tool_use_id ?? "");
      if (evtParentId !== parentToolUseId) continue;
      for (const task of taskMap.values()) {
        if (evt.event_type === "subagent.tool.started") {
          const input = payload.input
            ? (typeof payload.input === "string" ? payload.input : JSON.stringify(payload.input, null, 2))
            : "";
          task.toolCalls.push({
            name: String(payload.tool_name ?? "tool"),
            toolUseId: String(payload.tool_use_id ?? ""),
            input: input.slice(0, 500),
            result: "",
            error: false,
            status: "running",
          });
          break;
        } else if (evt.event_type === "subagent.tool.finished") {
          const tc = task.toolCalls.find(tc => tc.status === "running");
          if (tc) {
            tc.status = payload.error === true ? "error" : "completed";
            tc.result = String(payload.result ?? "").slice(0, 1000);
            tc.error = payload.error === true;
          }
          break;
        }
      }
    }
  }

  if (taskMap.size === 0) return null;
  const tasks = Array.from(taskMap.values());

  return (
    <div className="ml-6 my-1 border-l-2 border-blue-200 pl-3 space-y-1.5">
      {tasks.map((task) => {
        const isRunning = task.status === "running";
        const isCompleted = task.status === "completed";
        const isFailed = task.status === "failed";
        const statusIcon = isCompleted ? "✓" : isFailed ? "✗" : isRunning ? "●" : "■";
        const statusColor = isCompleted ? "text-green-600" : isFailed ? "text-red-500" : isRunning ? "text-blue-500" : "text-zinc-400";
        const bgClass = isRunning ? "bg-blue-50/50" : isCompleted ? "bg-green-50/30" : isFailed ? "bg-red-50/30" : "";
        const hasStreamedTools = task.toolCalls.length > 0;
        const hasDetails = hasStreamedTools || task.deltas.length > 0 || task.progress.length > 0;
        const expanded = expandedTasks.has(task.taskId);
        const transcript = transcripts.get(task.taskId);
        const isLoading = loadingTasks.has(task.taskId);

        // Merge transcript tool calls with streamed ones (transcript is more complete)
        const displayToolCalls = transcript
          ? transcript.toolCalls.map(tc => ({
              name: tc.name,
              toolUseId: "",
              input: typeof tc.input === "string" ? tc.input : JSON.stringify(tc.input, null, 2),
              result: tc.result.slice(0, 1000),
              error: tc.isError,
              status: tc.isError ? "error" as const : "completed" as const,
            }))
          : task.toolCalls;

        const displayDeltas = task.deltas.length > 0 ? task.deltas
          : (transcript?.thinkingSnippets || []).map(t => ({ kind: "thinking", text: t }))
            .concat((transcript?.textSnippets || []).map(t => ({ kind: "text", text: t })));

        const displaySummary = task.summary || transcript?.finalResult || "";

        return (
          <div key={task.taskId} className={`rounded-md px-2.5 py-1.5 ${bgClass}`}>
            {/* Header row: toggle + status + description */}
            <div
              className={`flex items-start gap-1.5 ${hasDetails ? "cursor-pointer hover:bg-white/50 rounded" : ""}`}
              onClick={() => hasDetails && toggleExpand(task.taskId, isRunning)}
            >
              {hasDetails ? (
                <span className="text-[10px] text-zinc-400 mt-0.5 select-none">{expanded ? "▼" : "▶"}</span>
              ) : (
                <span className="w-[10px]" />
              )}
              <span className={`text-xs mt-0.5 ${statusColor}`}>{statusIcon}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-medium text-zinc-700 truncate">{task.description}</span>
                  {task.isBackground && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-zinc-100 text-zinc-500">background</span>
                  )}
                  {transcript && !hasStreamedTools && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-blue-50 text-blue-500">{transcript.toolCalls.length} tools</span>
                  )}
                </div>
                {/* Latest progress (compact) */}
                {task.progress.length > 0 && !expanded && (
                  <div className="text-[11px] text-zinc-400 mt-0.5 truncate">
                    {task.progress[task.progress.length - 1]}
                  </div>
                )}
                {/* Tool badges (compact) */}
                {displayToolCalls.length > 0 && !expanded && (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {displayToolCalls.slice(0, 10).map((tc, idx) => (
                      <span key={idx} className={`text-[10px] font-mono px-1 py-0.5 rounded ${
                        tc.status === "running" ? "bg-amber-50 text-amber-600" :
                        tc.status === "completed" ? "bg-green-50 text-green-600" :
                        tc.status === "error" ? "bg-red-50 text-red-500" :
                        "bg-zinc-50 text-zinc-400"
                      }`}>
                        {tc.status === "running" ? "● " : tc.status === "completed" ? "✓ " : "✗ "}
                        {tc.name}
                      </span>
                    ))}
                    {displayToolCalls.length > 10 && (
                      <span className="text-[10px] text-zinc-400">+{displayToolCalls.length - 10} more</span>
                    )}
                  </div>
                )}
                {/* Result summary (compact) */}
                {displaySummary && !expanded && (
                  <div className={`text-[11px] mt-0.5 line-clamp-1 ${isCompleted ? "text-green-700" : isFailed ? "text-red-600" : "text-zinc-500"}`}>
                    {displaySummary.slice(0, 120)}
                  </div>
                )}
              </div>
            </div>

            {/* EXPANDED: full details */}
            {expanded && (
              <div className="mt-1.5 ml-5 space-y-1.5">
                {isLoading && (
                  <div className="text-[11px] text-blue-400 animate-pulse">Loading transcript…</div>
                )}

                {/* Progress lines */}
                {task.progress.length > 0 && (
                  <div className="space-y-0.5">
                    {task.progress.map((p, i) => (
                      <div key={i} className="text-[11px] text-zinc-400">📊 {p}</div>
                    ))}
                  </div>
                )}

                {/* Thinking / text deltas */}
                {displayDeltas.map((d, i) => (
                  <div key={`delta-${i}`}>
                    {d.kind === "thinking" ? (
                      <div className="text-[11px] text-zinc-400 italic">
                        💭 <span className="line-clamp-3">{d.text}</span>
                      </div>
                    ) : (
                      <div className="text-[11px] text-zinc-600 line-clamp-4">
                        {d.text}
                      </div>
                    )}
                  </div>
                ))}

                {/* Tool calls with inputs and results */}
                {displayToolCalls.map((tc, idx) => (
                  <div key={`tc-${idx}`} className="rounded border border-zinc-100 bg-white px-2 py-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`text-[10px] ${
                        tc.status === "running" ? "text-amber-500" :
                        tc.status === "completed" ? "text-green-500" :
                        "text-red-500"
                      }`}>
                        {tc.status === "running" ? "●" : tc.status === "completed" ? "✓" : "✗"}
                      </span>
                      <span className="text-[11px] font-mono font-medium text-zinc-700">{tc.name}</span>
                    </div>
                    {tc.input && (
                      <pre className="text-[10px] text-zinc-500 mt-0.5 whitespace-pre-wrap break-all line-clamp-4">{tc.input}</pre>
                    )}
                    {tc.result && (
                      <pre className={`text-[10px] mt-0.5 whitespace-pre-wrap break-all line-clamp-6 ${tc.error ? "text-red-500" : "text-green-700"}`}>
                        {tc.result}
                      </pre>
                    )}
                  </div>
                ))}

                {/* Full result / final text */}
                {displaySummary && (
                  <div className={`text-[11px] mt-1 ${isCompleted ? "text-green-700" : isFailed ? "text-red-600" : "text-zinc-500"}`}>
                    {isCompleted ? "✓" : isFailed ? "✗" : "→"} {displaySummary.slice(0, 500)}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ToolCardLive: reads from the events array for this tool's accumulated state
function ToolCardLive({
  toolUseId,
  events,
  startSeq,
}: {
  toolUseId: string;
  toolMap: Map<string, ToolState>;
  events: GatewayEvent[];
  startSeq: number;
}) {
  let toolName = "unknown";
  let toolInput = "";
  let stdout = "";
  let status: "running" | "completed" | "error" = "running";
  let result: string | undefined;
  let foundStart = false;
  let nestedDepth = 0; // Track nested tool calls (e.g. sub-agent tools)
  let mcpProgress: { progress?: number; total?: number; message?: string; elapsed_time_ms?: number } | undefined;

  for (const evt of events) {
    if (evt.seq < startSeq) continue;
    const payload = evt.payload as Record<string, unknown>;
    const evtToolId = String(payload.tool_use_id ?? "");

    if (evt.event_type === "tool.started" && evtToolId === toolUseId) {
      toolName = String(payload.tool_name ?? "unknown");
      foundStart = true;
      if (payload.input) {
        toolInput = typeof payload.input === "string"
          ? payload.input
          : JSON.stringify(payload.input, null, 2);
      }
    } else if (evt.event_type === "tool.started" && foundStart && evtToolId !== toolUseId) {
      // A different tool started (e.g. sub-agent internal tool) — skip its events
      nestedDepth++;
    } else if (evt.event_type === "tool.finished" && nestedDepth > 0 && evtToolId !== toolUseId) {
      // Close a nested tool call
      nestedDepth--;
    } else if (evt.event_type === "tool.stdout" && foundStart && nestedDepth === 0) {
      // Only collect stdout for THIS tool — use tool_use_id when available
      const stdoutToolId = String(payload.tool_use_id ?? "");
      if (stdoutToolId && stdoutToolId !== toolUseId) {
        // This stdout belongs to a different tool — skip it
        continue;
      }
      const partial = payload.partial ?? payload.partial_json ?? payload.output ?? payload.stdout;
      if (partial !== undefined) stdout += String(partial);
    } else if (evt.event_type === "mcp.progress" && foundStart && nestedDepth === 0 && evtToolId === toolUseId) {
      // Keep the latest progress update for this tool
      mcpProgress = {
        progress: payload.progress as number | undefined,
        total: payload.total as number | undefined,
        message: payload.message as string | undefined,
        elapsed_time_ms: payload.elapsed_time_ms as number | undefined,
      };
    } else if (evt.event_type === "tool.finished" && evtToolId === toolUseId) {
      // This is OUR tool's finished event
      const isError = payload.error === true;
      status = isError ? "error" : "completed";
      result = String(payload.result ?? payload.output ?? "");
      break;
    }
  }

  // tool.stdout accumulates the input JSON (input_json_delta from SDK)
  if (!toolInput && stdout) {
    // Try to parse and pretty-print the raw stdout JSON
    try {
      const parsed = JSON.parse(stdout);
      toolInput = JSON.stringify(parsed, null, 2);
    } catch {
      toolInput = stdout;
    }
  }

  return (
    <ToolCard
      toolName={toolName}
      toolUseId={toolUseId}
      toolInput={toolInput}
      stdout={stdout}
      status={status}
      result={result}
      mcpProgress={status === "running" ? mcpProgress : undefined}
    />
  );
}

// PermissionApprovalCard: rendered when awaiting_approval is true
function PermissionApprovalCard({
  sessionId,
  permissionId,
  toolName,
  toolInput,
  resolved,
  onDecision,
  canSend,
}: {
  sessionId: number;
  permissionId: string;
  toolName: string;
  toolInput: unknown;
  resolved: boolean;
  onDecision?: () => void;
  canSend: boolean;
}) {
  const [deciding, setDeciding] = useState(false);

  async function handleDecision(decision: "approve" | "deny") {
    setDeciding(true);
    try {
      if (decision === "approve") {
        await approvePermission(sessionId, permissionId);
      } else {
        await denyPermission(sessionId, permissionId);
      }
      onDecision?.();
    } catch {
      // ignore
    } finally {
      setDeciding(false);
    }
  }

  if (resolved) {
    return (
      <div className="text-xs text-zinc-400 bg-zinc-50 border border-zinc-200 rounded px-2 py-1 mb-2">
        Permission resolved
      </div>
    );
  }

  const inputPreview =
    typeof toolInput === "string" ? toolInput : JSON.stringify(toolInput, null, 2);

  return (
    <div className="bg-amber-50 border-2 border-amber-300 rounded-lg p-4 mb-2">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-amber-600 font-medium text-sm">Permission required</span>
      </div>
      <div className="text-sm text-zinc-700 mb-1">
        Agent wants to use <span className="font-mono font-semibold">{toolName}</span>:
      </div>
      <pre className="text-xs bg-zinc-900 text-zinc-100 rounded p-2 mb-3 max-h-40 overflow-auto whitespace-pre-wrap">
        {inputPreview}
      </pre>
      <div className="flex gap-2">
        <button
          onClick={() => { void handleDecision("approve"); }}
          disabled={deciding || !canSend}
          title={!canSend ? "You don't have permission to approve" : undefined}
          className="text-xs px-3 py-1.5 bg-emerald-500 text-white rounded hover:bg-emerald-600 disabled:opacity-50"
        >
          Approve
        </button>
        <button
          onClick={() => { void handleDecision("deny"); }}
          disabled={deciding || !canSend}
          title={!canSend ? "You don't have permission to deny" : undefined}
          className="text-xs px-3 py-1.5 bg-red-500 text-white rounded hover:bg-red-600 disabled:opacity-50"
        >
          Deny
        </button>
      </div>
    </div>
  );
}

// QuestionCard: rendered when AskUserQuestion tool is awaiting answer
// Shows clickable options. When user clicks an option:
// 1. Auto-approves the permission (so the SDK proceeds)
// 2. Sends the answer as a guidance message to the agent
function QuestionCard({
  questions,
  sessionId,
  permissionId,
  resolved,
}: {
  questions: Array<{
    question: string;
    header: string;
    options: Array<{ label: string; description: string; preview?: string }>;
    multiSelect: boolean;
  }>;
  sessionId: number;
  permissionId: string;
  resolved: boolean;
  onSelectAnswer?: (answer: string) => void;
}) {
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string>>({});
  const [approved, setApproved] = useState(false);
  const [sent, setSent] = useState(false);

  async function handleSelect(questionText: string, label: string, multiSelect: boolean) {
    let next: Record<string, string>;
    if (multiSelect) {
      const current = selectedAnswers[questionText] ?? "";
      const parts = current ? current.split(", ") : [];
      if (parts.includes(label)) {
        next = { ...selectedAnswers, [questionText]: parts.filter((p) => p !== label).join(", ") };
      } else {
        next = { ...selectedAnswers, [questionText]: [...parts, label].join(", ") };
      }
      setSelectedAnswers(next);
    } else {
      // Single select — approve + send immediately
      next = { ...selectedAnswers, [questionText]: label };
      setSelectedAnswers(next);

      // Auto-approve permission and send answer
      if (!approved && !resolved) {
        setApproved(true);
        try { await approvePermission(sessionId, permissionId); } catch {}

        // Send the answer as a guidance message
        const answerParts = Object.entries(next)
          .filter(([, v]) => v)
          .map(([q, a]) => `${q}: ${a}`);
        if (answerParts.length > 0) {
          try {
            await sendGuidance(sessionId, answerParts.join("; "));
            setSent(true);
          } catch {}
        }
      }
    }
  }

  async function handleMultiSubmit() {
    if (approved || resolved || sent) return;
    setApproved(true);
    try { await approvePermission(sessionId, permissionId); } catch {}

    const answerParts = Object.entries(selectedAnswers)
      .filter(([, v]) => v)
      .map(([q, a]) => `${q}: ${a}`);
    if (answerParts.length > 0) {
      try {
        await sendGuidance(sessionId, answerParts.join("; "));
        setSent(true);
      } catch {}
    }
  }

  if (sent) {
    return (
      <div className="text-xs text-green-600 bg-green-50 border border-green-200 rounded px-2 py-1 mb-2">
        ✓ Answer sent
      </div>
    );
  }

  return (
    <div className="bg-blue-50 border-2 border-blue-300 rounded-lg p-4 mb-2">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-blue-600 font-medium text-sm">❓ Agent is asking a question</span>
      </div>

      {questions.map((q, qi) => (
        <div key={qi} className="mb-3 last:mb-0">
          <div className="flex items-center gap-2 mb-2">
            {q.header && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">
                {q.header}
              </span>
            )}
            {q.multiSelect && (
              <span className="text-xs text-blue-500">Select multiple, then press Enter ↵</span>
            )}
          </div>
          <div className="text-sm text-zinc-800 font-medium mb-2">{q.question}</div>
          <div className="space-y-1.5">
            {q.options.map((opt, oi) => {
              const isSelected = q.multiSelect
                ? (selectedAnswers[q.question] ?? "").split(", ").includes(opt.label)
                : selectedAnswers[q.question] === opt.label;
              return (
                <button
                  key={oi}
                  onClick={() => handleSelect(q.question, opt.label, q.multiSelect)}
                  className={`w-full text-left px-3 py-2 rounded-lg border transition-colors ${
                    isSelected
                      ? "bg-blue-100 border-blue-400 text-blue-800"
                      : "bg-white border-zinc-200 hover:border-blue-300 hover:bg-blue-50 text-zinc-700"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`w-4 h-4 ${q.multiSelect ? "rounded-md" : "rounded-full"} border flex items-center justify-center text-xs ${
                      isSelected
                        ? "bg-blue-500 border-blue-500 text-white"
                        : "border-zinc-300"
                    }`}>
                      {isSelected && (q.multiSelect ? "✓" : "●")}
                    </span>
                    <span className="font-medium text-sm">{opt.label}</span>
                  </div>
                  {opt.description && (
                    <div className="text-xs text-zinc-500 ml-6 mt-0.5">{opt.description}</div>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      ))}

      {questions.some(q => q.multiSelect) && (
        <div className="flex gap-2 mt-3">
          <button
            onClick={() => { void handleMultiSubmit(); }}
            disabled={!Object.values(selectedAnswers).some(v => v)}
            className="text-xs px-4 py-1.5 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Submit answer
          </button>
        </div>
      )}

      <div className="text-xs text-blue-500 mt-2">
        Click an option to send your answer
      </div>
    </div>
  );
}

