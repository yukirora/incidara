import type { TaskStore } from "./task-store.js";
import type { SessionStore } from "./session-store.js";
import type { GatewayClient } from "./gateway-client.js";
import type { Agent } from "./agent-registry.js";

export interface GatewayEvent {
  seq: number;
  session_id: string;
  event_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface SseTaskUpdaterDeps {
  taskStore: TaskStore;
  sessionStore: SessionStore;
  makeClient: (gatewayUrl: string) => GatewayClient;
  getAgents: () => Agent[];
}

/**
 * Parses complete SSE events from a buffer.
 * Returns parsed GatewayEvent objects and the unparsed remainder.
 */
export function extractCompleteSseEvents(buffer: string): {
  parsed: GatewayEvent[];
  remainder: string;
} {
  const parsed: GatewayEvent[] = [];
  let remainder = buffer;

  while (true) {
    const eventEnd = remainder.indexOf("\n\n");
    if (eventEnd === -1) break;

    const eventBlock = remainder.slice(0, eventEnd);
    remainder = remainder.slice(eventEnd + 2);

    // Collect data: lines
    const dataLines: string[] = [];
    for (const line of eventBlock.split("\n")) {
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (dataLines.length > 0) {
      const jsonStr = dataLines.join("");
      try {
        const event = JSON.parse(jsonStr) as GatewayEvent;
        parsed.push(event);
      } catch {
        // Skip malformed events
      }
    }
  }

  return { parsed, remainder };
}

/**
 * Promotes the next pending task in the queue for a session.
 * If promoted, sends the task prompt to the gateway.
 * On gateway error, marks the task as error.
 */
export async function promoteNext(
  deps: SseTaskUpdaterDeps,
  sessionId: number
): Promise<void> {
  const { taskStore, sessionStore, makeClient, getAgents } = deps;

  const promoted = await taskStore.promoteNextPending(sessionId);
  if (!promoted) return;

  // Look up the session's gateway_session_id + agent's gateway_url
  const session = await sessionStore.getById(sessionId);
  if (!session) {
    await taskStore.updateStatus(promoted.id, "error", new Date());
    await taskStore.updateOutputPreview(promoted.id, "Error: session not found");
    return;
  }

  const agents = getAgents();
  const agent = agents.find((a) => a.id === session.agent_id);
  if (!agent) {
    await taskStore.updateStatus(promoted.id, "error", new Date());
    await taskStore.updateOutputPreview(promoted.id, "Error: agent not found");
    return;
  }

  const client = makeClient(agent.gateway_url);
  try {
    try {
      await client.sendMessage(session.gateway_session_id, promoted.prompt);
    } catch {
      // sendMessage fails if gateway session completed — resume it
      await client.resume(session.gateway_session_id, promoted.prompt);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    await taskStore.updateStatus(promoted.id, "error", new Date());
    await taskStore.updateOutputPreview(promoted.id, `Error: ${msg}`.slice(0, 200));
  }
}

/**
 * Applies a gateway event to the task state.
 * Finds the latest task for the session and updates it accordingly.
 */
export async function applyEventToTask(
  deps: SseTaskUpdaterDeps,
  sessionId: number,
  event: GatewayEvent
): Promise<void> {
  const { taskStore } = deps;

  const task = await taskStore.latestForSession(sessionId);
  if (!task) return;

  // Set gateway_seq_start on first message.user event for this task
  if (event.event_type === "message.user" && task.gateway_seq_start == null) {
    await taskStore.setGatewaySeqStart(task.id, event.seq);
  }

  switch (event.event_type) {
    case "session.started": {
      // Adapter (re)started a session — if task was in error/interrupted, the adapter
      // is recovering (e.g. auto-compact after context overflow). Transition to running.
      if (task.status === "error" || task.status === "interrupted") {
        await taskStore.updateStatus(task.id, "running");
      }
      // Feature 1: capture model version on first session start (once per task)
      if (!task.model_version) {
        const model = process.env.MODEL || process.env.ANTHROPIC_MODEL || "unknown";
        await taskStore.setModelVersion(task.id, model);
      }
      break;
    }

    case "session.state_changed": {
      // The session changed state (e.g. running -> busy, completed -> running).
      // Key case: completed -> running/busy means the session RESUMED after
      // a prior session.completed — the task must be recovered from completed
      // back to running. This happens in multi-turn sessions where the agent
      // finishes a response, then the user sends more input.
      const toState = event.payload?.to as string | undefined;
      if ((toState === "running" || toState === "busy") && task.status === "completed") {
        // Feature 1: task was auto-completed, human came back with more input → override
        if (task.completion_mode === 'auto' && !task.human_decision) {
          await taskStore.autoSetHumanDecision(task.id, "overridden");
        }
        await taskStore.updateStatus(task.id, "running");
      }
      break;
    }

    case "message.user": {
      // User sent input (approval, response, etc.) — if task was waiting_input, resume it
      if (task.status === "waiting_input" || task.status === "error" || task.status === "interrupted") {
        await taskStore.updateStatus(task.id, "running");
      }
      // Feature 1: auto-detect human decision from user message content.
      // Only sets if human_decision is NULL (first decision wins).
      if (!task.human_decision) {
        const content = (event.payload.content as string | undefined) ?? "";
        const lower = content.toLowerCase();
        // Check override first — if the user is giving guidance, it's overridden.
        if (/^(no[.,\s]|don'?t\b|could you|please check|try\b|reconsider|instead|what about|why not)/i.test(lower.trim())) {
          await taskStore.autoSetHumanDecision(task.id, "overridden");
        } else if (/^approved\b/i.test(lower.trim()) || lower.includes("approved: proceed")) {
          await taskStore.autoSetHumanDecision(task.id, "approved");
        } else if (/^declined\b/i.test(lower.trim()) || lower.includes("declined: escalate")) {
          await taskStore.autoSetHumanDecision(task.id, "declined");
        }
      }
      break;
    }

    case "message.agent": {
      const content = (event.payload.content as string | undefined) ?? "";
      await taskStore.updateOutputPreview(task.id, content.slice(0, 200));
      await taskStore.updateFullOutput(task.id, content);  // Feature 1: full output for audit/memory
      // If agent is producing output, the task is running — transition from any
      // non-terminal state (error, interrupted, waiting_input) to running.
      if (task.status !== "running" && task.status !== "completed" && task.status !== "cancelled") {
        await taskStore.updateStatus(task.id, "running");
      }
      break;
    }

    case "session.waiting_input": {
      // Feature 1 (C2): if agent output contains an approval marker, transition
      // to pending_approval instead of waiting_input. The agent includes this
      // marker when proposing an action that requires human sign-off.
      const output = task.output ?? task.output_preview ?? "";
      const isApprovalRequest = /AWAITING_APPROVAL/i.test(output);
      await taskStore.updateStatus(
        task.id,
        isApprovalRequest ? "pending_approval" : "waiting_input"
      );
      break;
    }

    case "session.completed": {
      // Always update output_preview from message.agent (done above), but
      // only mark completed if completion_mode is 'auto'
      if (task.completion_mode === 'manual') {
        // Gateway completed but user must manually mark task as done.
        // Move to waiting_input so the UI shows "waiting for user".
        // Unless the task is awaiting approval — don't overwrite that state.
        if (task.status !== "pending_approval" && task.status !== "approved") {
          await taskStore.updateStatus(task.id, "waiting_input");
        }
      } else {
        await taskStore.updateStatus(task.id, "completed", new Date());
        await promoteNext(deps, sessionId);
      }
      break;
    }

    case "session.error": {
      const errorMsg = (event.payload.error as string | undefined) ?? "Unknown error";
      const isContextOverflow = errorMsg.includes("Prompt is too long")
        || errorMsg.includes("prompt_too_long")
        || errorMsg.includes("context window")
        || errorMsg.includes("context_length_exceeded")
        || errorMsg.includes("too many tokens");
      const isConversationLost = errorMsg.includes("No conversation found")
        || errorMsg.includes("conversation not found");
      const isAdapterRecoverable = event.payload.recoverable === true;
      const isTransientApiError = errorMsg.includes("504")
        || errorMsg.includes("502")
        || errorMsg.includes("503")
        || errorMsg.includes("429")
        || errorMsg.includes("rate limit")
        || errorMsg.includes("overloaded")
        || errorMsg.includes("API Error: 5");

      if (isConversationLost && task.status === "running") {
        // Conversation data is gone on Claude's side, but the adapter will
        // retry once without --resume (fresh start). Keep as "running" to
        // give the adapter a chance. If the retry also fails, another
        // session.error will arrive on a non-running task.
        console.log(`[sse] task ${task.id}: conversation lost while running ("${errorMsg.slice(0, 60)}"), keeping as running — adapter will retry fresh`);
        const current = await taskStore.getById(task.id);
        if (!current?.output_preview) {
          await taskStore.updateOutputPreview(task.id, `Recovering: session data lost, retrying fresh...`.slice(0, 200));
        }
      } else if (isConversationLost) {
        // Conversation lost on a non-running task — adapter already tried and failed.
        console.log(`[sse] task ${task.id}: conversation lost ("${errorMsg.slice(0, 80)}"), marking as error`);
        await taskStore.updateStatus(task.id, "error", new Date());
        await taskStore.updateOutputPreview(task.id, `Session data lost — please start a new task. (${errorMsg.slice(0, 100)})`.slice(0, 200));
        await promoteNext(deps, sessionId);
      } else if (isAdapterRecoverable) {
        // Adapter explicitly marked this as recoverable — keep running
        const retryAttempt = event.payload.retry_attempt;
        const retryIn = event.payload.retry_in_ms;
        const current = await taskStore.getById(task.id);
        const retryInfo = retryAttempt ? ` (retry ${retryAttempt}${retryIn ? ` in ${Math.round(Number(retryIn) / 1000)}s` : ""})` : "";
        if (!current?.output_preview) {
          await taskStore.updateOutputPreview(task.id, `Recovering${retryInfo}: ${errorMsg}`.slice(0, 200));
        }
        // Do NOT call promoteNext — the adapter is still handling this session
      } else if (isContextOverflow && task.status === "running") {
        // Context overflow while task is running. The adapter may auto-recover
        // (compact-and-resume). Keep as "running" to give the adapter a chance.
        // If the adapter recovers, we'll get session.started/session.completed next.
        // If the adapter doesn't recover, a second session.error will arrive,
        // or the reconciler will eventually detect the stale state.
        console.log(`[sse] task ${task.id}: context overflow while running ("${errorMsg.slice(0, 60)}"), keeping as running — adapter may recover`);
        const current = await taskStore.getById(task.id);
        if (!current?.output_preview) {
          await taskStore.updateOutputPreview(task.id, `Compacting: ${errorMsg}`.slice(0, 200));
        }
        // Do NOT call promoteNext — give the adapter time to recover
      } else if (isContextOverflow && (task.status === "error" || task.status === "interrupted")) {
        // Context overflow on an already-errored/interrupted task means the
        // adapter tried to recover but failed (e.g. session is genuinely full
        // and compaction couldn't reduce it enough). Move to "waiting_input"
        // instead of staying in "error" so the user can decide what to do
        // (e.g. start a new session, or retry after manual intervention).
        console.log(`[sse] task ${task.id}: context overflow recovery failed, moving to waiting_input`);
        await taskStore.updateStatus(task.id, "waiting_input");
        await taskStore.updateOutputPreview(task.id, `Session context full — please start a new session to continue. (${errorMsg.slice(0, 100)})`.slice(0, 200));
        // Do NOT call promoteNext — this session needs a fresh start
      } else if (task.status === "running") {
        // Error while task is running. The adapter may auto-recover.
        // Keep as "running" to give it a chance.
        if (isTransientApiError) {
          // Transient API errors (504, 429, etc.) — adapter will auto-retry
          await taskStore.incrementLlmError(task.id);
          console.log(`[sse] task ${task.id}: transient API error while running ("${errorMsg.slice(0, 60)}"), keeping as running — adapter will auto-retry`);
        } else {
          console.log(`[sse] task ${task.id}: error while running ("${errorMsg.slice(0, 60)}"), keeping as running — adapter may recover`);
        }
        const current = await taskStore.getById(task.id);
        if (!current?.output_preview) {
          await taskStore.updateOutputPreview(task.id, `Recovering: ${errorMsg}`.slice(0, 200));
        }
        // Do NOT call promoteNext — the adapter is still handling this session
      } else {
        // Non-recoverable error on a non-running task: mark task as error and promote next
        await taskStore.updateStatus(task.id, "error", new Date());
        const current = await taskStore.getById(task.id);
        if (!current?.output_preview) {
          await taskStore.updateOutputPreview(task.id, `Error: ${errorMsg}`.slice(0, 200));
        }
        await promoteNext(deps, sessionId);
      }
      break;
    }

    case "session.interrupted": {
      await taskStore.updateStatus(task.id, "interrupted", new Date());
      // Set output_preview to partial_content if empty
      const current = await taskStore.getById(task.id);
      if (!current?.output_preview) {
        const partial = (event.payload.partial_content as string | undefined) ?? "";
        if (partial) {
          await taskStore.updateOutputPreview(task.id, partial.slice(0, 200));
        }
      }
      await promoteNext(deps, sessionId);
      break;
    }

    default: {
      // Count tool errors: tool.finished with error=true in payload
      if (event.event_type === "tool.finished") {
        if (event.payload?.error === true) {
          await taskStore.incrementToolError(task.id);
        }
      }
      break;
    }
  }
}
