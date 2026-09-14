import { Router } from "express";
import type { Response as ExpressResponse } from "express";
import type { TaskStore } from "../task-store.js";
import type { SessionStore } from "../session-store.js";
import type { Session } from "../session-store.js";
import type { GatewayClient } from "../gateway-client.js";
import type { Agent } from "../agent-registry.js";
import type { SseTaskUpdaterDeps } from "../sse-task-updater.js";
import { extractCompleteSseEvents, applyEventToTask } from "../sse-task-updater.js";
import type { TaskStore as TS } from "../task-store.js";
import type { AccessLevel } from "../permission-store.js";
import type { PermissionStore } from "../permission-store.js";
import type pg from "pg";

/**
 * Derive a display name from email.
 * "demo.user@example.com" → "demo.user"
 * Falls back to full email if no @ sign.
 */
function displayNameFromEmail(email: string): string {
  const atIdx = email.indexOf("@");
  if (atIdx > 0) return email.slice(0, atIdx);
  return email;
}

/**
 * Deduplicate consecutive message.user events with the same content.
 *
 * The agent adapter publishes a manual "message.user" event before calling
 * the SDK, and the SDK emits its own "message.user" event. This results in
 * duplicate user messages (e.g. seq=1 and seq=4 with identical content,
 * separated by session.started + session.state_changed). The duplicate
 * creates a phantom "round" with no agent response, making the first
 * user message appear broken.
 *
 * Strategy: if a message.user event at seq=N has the same content as a
 * recent message.user at seq=M (where N-M <= 5), and there was a
 * session.started event between them, drop the earlier one.
 */
function dedupInitialUserMessage(events: any[]): any[] {
  if (events.length < 3) return events;

  // Find duplicate pairs: message.user(A) → session.started → message.user(A)
  const toSkip = new Set<number>();
  let lastUserSeq: number | null = null;
  let lastUserContent: string | null = null;
  let sawSessionStarted = false;

  for (const e of events) {
    const seq = e.seq as number;
    const et = e.event_type as string;

    if (et === "message.user") {
      const content = String((e.payload ?? {}).content ?? "");
      if (lastUserContent !== null && sawSessionStarted && content === lastUserContent && seq - lastUserSeq! <= 5) {
        // This is a duplicate — skip the earlier one
        toSkip.add(lastUserSeq!);
      }
      lastUserSeq = seq;
      lastUserContent = content;
      sawSessionStarted = false;
    } else if (et === "session.started") {
      sawSessionStarted = true;
    }
  }

  if (toSkip.size === 0) return events;
  return events.filter((e) => !toSkip.has(e.seq as number));
}

/**
 * Compact streaming events for history loads.
 *
 * When loading historical events (not live SSE), we compact `message.delta`
 * and `tool.stdout` to reduce payload ~90% without losing any content.
 *
 * Rules:
 * - `message.delta` with kind="thinking": MERGE consecutive thinking deltas
 *   into a single consolidated event. Thinking content is NOT included in
 *   `message.agent`, so we must preserve it — but we don't need hundreds of
 *   single-token deltas when one merged event suffices.
 * - `message.delta` with kind="text" (or no kind): MERGE consecutive text
 *   deltas into a single consolidated event. If a `message.agent` follows,
 *   the merged text delta is redundant and can be dropped entirely.
 * - `tool.stdout`: drop entirely — `tool.finished` has the result.
 */
function compactStreamingEvents(events: any[]): any[] {
  if (events.length <= 50) return events; // too small to bother

  const result: any[] = [];
  let textBuf: string[] = [];   // accumulated text deltas
  let thinkingBuf: string[] = []; // accumulated thinking deltas
  let lastDeltaEvent: any = null; // reference for creating merged event
  let lastDeltaKind: string | null = null;
  let lastToolUseId: string = "";  // track which tool is active (for tool.stdin accumulation)
  // Support multiple concurrent tool.stdin buffers (interleaved partials from auto-allowed tools)
  let toolStdinBufs: Map<string, string[]> = new Map();  // toolUseId → chunks
  let toolStdinBuf: { toolUseId: string; chunks: string[] } | null = null; // deprecated, kept for fallback
  // Sub-agent delta merging (same as main agent but keyed by task_id)
  let subTextBuf: string[] = [];
  let subThinkingBuf: string[] = [];
  let lastSubDeltaKind: string | null = null;
  let lastSubTaskId: string = "";
  let lastSubDeltaEvent: any = null;

  function flushBuffer(kind: string, buf: string[], refEvent: any) {
    if (buf.length === 0) return;
    if (buf.length === 1) {
      // Single delta — keep as-is (already in result or will be added)
      return;
    }
    // Multiple deltas — merge into one by updating the last one we added
    const merged = buf.join("");
    // Find and update the last delta of this kind in result
    for (let i = result.length - 1; i >= 0; i--) {
      const r = result[i];
      if (r.event_type === "message.delta" && r.payload?.kind === kind) {
        r.payload = { ...r.payload, text: merged, _merged_count: buf.length };
        return;
      }
    }
  }

  function flushToolStdin(toolUseId?: string) {
    // Flush specific tool or all pending tool stdin buffers
    if (toolUseId) {
      const chunks = toolStdinBufs.get(toolUseId);
      if (!chunks || chunks.length === 0) return;
      const fullInput = chunks.join("");
      let parsed: any = fullInput;
      try { parsed = JSON.parse(fullInput); } catch { /* keep as string */ }
      for (let i = result.length - 1; i >= 0; i--) {
        const r = result[i];
        if (r.event_type === "tool.started" && r.payload?.tool_use_id === toolUseId) {
          r.payload = { ...r.payload, input: parsed, _stdin_merged_count: chunks.length };
          break;
        }
      }
      toolStdinBufs.delete(toolUseId);
    } else {
      // Flush all
      for (const [id, chunks] of toolStdinBufs) {
        if (chunks.length === 0) continue;
        const fullInput = chunks.join("");
        let parsed: any = fullInput;
        try { parsed = JSON.parse(fullInput); } catch { /* keep as string */ }
        for (let i = result.length - 1; i >= 0; i--) {
          const r = result[i];
          if (r.event_type === "tool.started" && r.payload?.tool_use_id === id) {
            r.payload = { ...r.payload, input: parsed, _stdin_merged_count: chunks.length };
            break;
          }
        }
      }
      toolStdinBufs.clear();
    }
  }

  function flushSubDeltaBuffers() {
    // Merge sub-agent delta buffers into the last event of each kind
    if (subThinkingBuf.length > 1) {
      const merged = subThinkingBuf.join("");
      for (let i = result.length - 1; i >= 0; i--) {
        const r = result[i];
        if (r.event_type === "subagent.delta" && r.payload?.kind === "thinking" && r.payload?.task_id === lastSubTaskId) {
          r.payload = { ...r.payload, text: merged, _merged_count: subThinkingBuf.length };
          break;
        }
      }
    }
    if (subTextBuf.length > 1) {
      const merged = subTextBuf.join("");
      for (let i = result.length - 1; i >= 0; i--) {
        const r = result[i];
        if (r.event_type === "subagent.delta" && (r.payload?.kind === "text" || !r.payload?.kind) && r.payload?.task_id === lastSubTaskId) {
          r.payload = { ...r.payload, text: merged, _merged_count: subTextBuf.length };
          break;
        }
      }
    }
    subThinkingBuf = [];
    subTextBuf = [];
    lastSubDeltaKind = null;
    lastSubTaskId = "";
  }

  for (const e of events) {
    const et = e.event_type as string;

    if (et === "message.delta") {
      const kind = (e.payload?.kind as string) || "text";
      const text = (e.payload?.text as string) || "";

      if (kind === lastDeltaKind) {
        // Same kind — accumulate
        if (kind === "thinking") {
          thinkingBuf.push(text);
        } else {
          textBuf.push(text);
        }
        // Don't add to result yet — we'll merge
      } else {
        // Kind changed — flush previous buffer
        if (lastDeltaKind === "thinking") {
          flushBuffer("thinking", thinkingBuf, lastDeltaEvent);
          thinkingBuf = [];
        } else if (lastDeltaKind === "text") {
          flushBuffer("text", textBuf, lastDeltaEvent);
          textBuf = [];
        }
        // Start new buffer
        if (kind === "thinking") {
          thinkingBuf = [text];
        } else {
          textBuf = [text];
        }
        lastDeltaKind = kind;
        lastDeltaEvent = e;
        result.push(e);
      }
    } else if (et === "message.agent") {
      // Flush any pending buffers
      if (lastDeltaKind === "thinking") {
        flushBuffer("thinking", thinkingBuf, lastDeltaEvent);
      }
      if (textBuf.length > 0) {
        flushBuffer("text", textBuf, lastDeltaEvent);
      }
      // Text deltas before message.agent are redundant (message.agent has full text)
      // Remove the merged text delta from result
      for (let i = result.length - 1; i >= 0; i--) {
        if (result[i].event_type === "message.delta" && result[i].payload?.kind !== "thinking") {
          result.splice(i, 1);
          break;
        }
      }
      // Reset buffers
      textBuf = [];
      thinkingBuf = [];
      lastDeltaKind = null;
      lastDeltaEvent = null;
      result.push(e);
    } else if (et === "tool.stdout") {
      // tool.stdout carries tool input JSON (from SDK's input_json_delta).
      // Merge partial chunks per tool_use_id into a single event with full input.
      const partial = String(e.payload?.partial ?? e.payload?.partial_json ?? "");
      // Use tool_use_id from the payload if available (newer adapter includes it)
      // Otherwise fall back to lastToolUseId
      const stdoutToolId = String(e.payload?.tool_use_id ?? lastToolUseId);
      if (stdoutToolId) {
        const existing = toolStdinBufs.get(stdoutToolId) ?? [];
        existing.push(partial);
        toolStdinBufs.set(stdoutToolId, existing);
      }
      continue;
    } else if (et === "subagent.delta") {
      // Merge consecutive sub-agent deltas of same kind and task_id
      const kind = (e.payload?.kind as string) || "text";
      const taskId = String(e.payload?.task_id ?? "");
      const text = (e.payload?.text as string) || "";

      if (taskId === lastSubTaskId && kind === lastSubDeltaKind) {
        // Same task + same kind — accumulate
        if (kind === "thinking") {
          subThinkingBuf.push(text);
        } else {
          subTextBuf.push(text);
        }
        // Don't add to result yet
      } else {
        // Different task or kind — flush previous sub-agent buffers
        flushSubDeltaBuffers();
        // Start new buffer
        if (kind === "thinking") {
          subThinkingBuf = [text];
        } else {
          subTextBuf = [text];
        }
        lastSubDeltaKind = kind;
        lastSubTaskId = taskId;
        lastSubDeltaEvent = e;
        result.push(e);
      }
    } else if (et === "subagent.tool.progress") {
      // Skip periodic tool progress events — too noisy
      continue;
    } else if (et === "mcp.progress") {
      // Keep only the latest mcp.progress per tool_use_id — earlier ones are stale
      const toolId = String(e.payload?.tool_use_id ?? "");
      const existingIdx = result.findIndex(
        (r) => r.event_type === "mcp.progress" && String(r.payload?.tool_use_id ?? "") === toolId
      );
      if (existingIdx >= 0) {
        result[existingIdx] = e; // replace with newer
      } else {
        // Flush pending buffers before adding
        if (lastDeltaKind === "thinking") { flushBuffer("thinking", thinkingBuf, lastDeltaEvent); thinkingBuf = []; }
        if (textBuf.length > 0) { flushBuffer("text", textBuf, lastDeltaEvent); textBuf = []; }
        lastDeltaKind = null;
        flushSubDeltaBuffers();
        result.push(e);
      }
    } else {
      // Flush any pending buffers before non-delta events
      if (lastDeltaKind === "thinking") {
        flushBuffer("thinking", thinkingBuf, lastDeltaEvent);
        thinkingBuf = [];
      }
      if (textBuf.length > 0) {
        flushBuffer("text", textBuf, lastDeltaEvent);
        textBuf = [];
      }
      lastDeltaKind = null;
      // Flush sub-agent delta buffers
      flushSubDeltaBuffers();
      // Track tool_use_id for tool.stdout → tool.stdin merging
      if (et === "tool.started" && e.payload?.tool_use_id) {
        lastToolUseId = String(e.payload.tool_use_id);
      }
      // Flush tool stdin before tool.finished (so input is before result)
      if (et === "tool.finished" && e.payload?.tool_use_id) {
        flushToolStdin(String(e.payload.tool_use_id));
      }
      // Also flush on permission.requested — tools that need approval have
      // their complete input by this point, even though tool.finished hasn't fired yet
      if (et === "permission.requested" && e.payload?.tool_use_id) {
        flushToolStdin(String(e.payload.tool_use_id));
      }
      result.push(e);
    }
  }

  // Flush remaining buffers
  if (lastDeltaKind === "thinking" && thinkingBuf.length > 0) {
    flushBuffer("thinking", thinkingBuf, lastDeltaEvent);
  }
  if (textBuf.length > 0) {
    flushBuffer("text", textBuf, lastDeltaEvent);
  }
  flushSubDeltaBuffers();
  flushToolStdin(); // flush all remaining

  return result;
}

/**
 * Access-level event filter state for SSE streaming.
 * Manages buffering for readonly_summary mode (emit last message.agent before user message).
 */
export interface AccessFilterState {
  level: AccessLevel;
  /** For readonly_summary: buffer the last message.agent before a user message */
  pendingAgentMsg: Record<string, unknown> | null;
  /** Track whether a tool call is in progress (tool.started seen, tool.finished not yet) */
  toolActive: boolean;
}

export function makeAccessFilterState(level: AccessLevel): AccessFilterState {
  return { level, pendingAgentMsg: null, toolActive: false };
}

/**
 * Filter a single event based on the user's access level.
 * Returns { event } to forward, or null to drop.
 *
 * For readonly_summary: buffers message.agent, emits last one before message.user.
 */
export function applyAccessFilter(
  event: Record<string, unknown>,
  state: AccessFilterState,
): Record<string, unknown> | null {
  const { level, pendingAgentMsg } = state;
  const et = event.event_type as string;

  // No filtering for interactive and readonly
  if (level === "interactive" || level === "readonly") {
    return event;
  }

  if (level === "readonly_summary") {
    // Buffer message.agent, emit pending one before user message
    if (et === "message.agent") {
      state.pendingAgentMsg = { ...event };
      return null; // don't emit yet
    }

    if (et === "message.user") {
      // Signal the caller to flush the pending agent message before this user message
      if (state.pendingAgentMsg) {
        (event as any).__flushPendingAgent = true;
        // DON'T clear pendingAgentMsg here — caller does it after emitting
        return event;
      }
      return event;
    }

    // Drop all tool events and thinking
    if (
      et === "tool.started" ||
      et === "tool.stdout" ||
      et === "tool.finished" ||
      et === "subagent.started" ||
      et === "subagent.updated" ||
      et === "subagent.finished" ||
      et === "subagent.delta" ||
      et === "subagent.tool.started" ||
      et === "subagent.tool.finished" ||
      et === "subagent.tool.progress" ||
      et === "mcp.progress" ||
      et === "permission.requested" ||
      et === "question.requested"
    ) {
      return null;
    }

    // Drop thinking deltas
    if (et === "message.delta" && (event.payload as any)?.kind === "thinking") {
      return null;
    }

    // Drop text deltas (only message.agent should reach user in summary mode)
    if (et === "message.delta") {
      return null;
    }

    return event;
  }

  if (level === "readonly_input") {
    // Keep tool.started (name + input), send tool.finished with output stripped
    if (et === "tool.started") {
      state.toolActive = true;
      return event;
    }
    if (et === "tool.finished") {
      // Send tool.finished so UI knows it completed, but strip the output
      state.toolActive = false;
      const stripped = { ...event, payload: { ...(event.payload as Record<string, unknown>) } };
      delete (stripped.payload as Record<string, unknown>).output;
      delete (stripped.payload as Record<string, unknown>).result;
      delete (stripped.payload as Record<string, unknown>).error;
      return stripped;
    }
    if (et === "tool.stdout") {
      return null; // partial input already merged into tool.started
    }
    if (et === "message.agent") {
      state.toolActive = false;
    }

    // Hide thinking (always)
    if (et === "message.delta" && (event.payload as any)?.kind === "thinking") {
      return null;
    }

    // Keep everything else: session.*, message.user, message.agent, permission.*
    return event;
  }

  return event;
}

/**
 * Flush any pending buffered events for the access filter.
 * Call this at the end of a stream or when you need to force-flush.
 * Returns an array of events to emit (may be empty).
 */
export function flushAccessFilter(state: AccessFilterState): Record<string, unknown>[] {
  if (state.pendingAgentMsg) {
    const pending = state.pendingAgentMsg;
    state.pendingAgentMsg = null;
    return [pending];
  }
  return [];
}

/**
 * Backfill gateway_seq_start for tasks that are missing it.
 * Scans events for message.user boundaries and maps them to tasks
 * ordered by created_at. Runs async after the events response is sent.
 */
async function backfillSeqStarts(
  taskStore: TS,
  uiSessionId: number,
  events: Array<{ seq: number; event_type: string; payload?: any }>,
): Promise<void> {
  // Get all tasks for this session, ordered by creation time (internal query)
  const tasks = await taskStore.allForSession(uiSessionId);

  // Find all message.user events with content
  const userEvents = events
    .filter((e) => e.event_type === "message.user")
    .map((e) => ({ seq: e.seq, content: (e.payload as any)?.content as string ?? "" }))
    .sort((a, b) => a.seq - b.seq);

  // Track which gateway seqs have already been claimed
  const claimedSeqs = new Set(
    tasks.filter((t) => t.gateway_seq_start != null).map((t) => t.gateway_seq_start!),
  );

  // Match tasks to message.user events by prompt content
  for (const task of tasks) {
    if (task.gateway_seq_start != null) continue;

    // Find the first unclaimed message.user whose content matches the task's prompt
    const match = userEvents.find(
      (e) => !claimedSeqs.has(e.seq) && e.content.trim() === task.prompt.trim(),
    );
    if (match) {
      await taskStore.setGatewaySeqStart(task.id, match.seq);
      claimedSeqs.add(match.seq);
    }
  }
}

interface ProxyRouterDeps {
  taskStore: TaskStore;
  sessionStore: SessionStore;
  pool: pg.Pool;
  getAgents: () => Agent[];
  makeClient: (gatewayUrl: string) => GatewayClient;
  applyEventFn?: typeof applyEventToTask;
}

export function makeProxyRouter(deps: ProxyRouterDeps): Router {
  const { taskStore, sessionStore, pool, getAgents, makeClient } = deps;
  const applyEventFn = deps.applyEventFn ?? applyEventToTask;
  const router = Router();

  /**
   * Look up display name for an email from the users table.
   * Returns user.name if set, otherwise derives from email local part.
   */
  async function resolveDisplayName(email: string): Promise<string> {
    try {
      const { rows } = await pool.query<{ name: string | null }>(
        `SELECT name FROM users WHERE email = $1`,
        [email],
      );
      if (rows[0]?.name) return rows[0].name;
    } catch {
      // Table might not exist or other DB error — fall back to email parsing
    }
    return displayNameFromEmail(email);
  }

  /**
   * Enrich message.user events with user_name.
   *
   * Strategy:
   * 1. The first message.user per task → use task.submitter_email
   * 2. Follow-up message.user events → match to message_senders rows (timestamp-based)
   * 3. Fallback → session owner email
   *
   * Modifies events in place.
   */
  async function enrichUserEvents(
    events: Array<Record<string, unknown>>,
    session: Session,
  ): Promise<void> {
    // Load tasks for this session to map first message.user → submitter
    const tasks = await taskStore.allForSession(session.id);
    const sortedTasks = [...tasks].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );

    // Identify which seqs are "first message" of each task
    const taskFirstSeqs = new Map<number, string>(); // seq → submitter_email
    for (const task of sortedTasks) {
      if (task.gateway_seq_start != null) {
        taskFirstSeqs.set(task.gateway_seq_start, task.submitter_email ?? session.owner_email);
      }
    }

    // Load message_senders for follow-up messages, ordered by time
    let senders: Array<{ sender_email: string; gateway_seq: number | null; created_at: string }> = [];
    try {
      const { rows } = await pool.query<{ sender_email: string; gateway_seq: number | null; created_at: string }>(
        `SELECT sender_email, gateway_seq, created_at FROM message_senders WHERE session_id = $1 ORDER BY created_at ASC`,
        [session.id],
      );
      senders = rows;
    } catch {
      // Table might not exist yet (migration not applied) — fallback to owner
    }

    // Build seq→email map for direct matching, and timestamp-indexed list for unmatched
    const senderBySeq = new Map<number, string>();
    const unmatchedSenders: Array<{ email: string; createdAt: number }> = [];
    for (const s of senders) {
      if (s.gateway_seq != null && s.gateway_seq > 0) {
        senderBySeq.set(s.gateway_seq, s.sender_email);
      } else {
        unmatchedSenders.push({ email: s.sender_email, createdAt: new Date(s.created_at).getTime() });
      }
    }
    // Sort unmatched by time for timestamp-based matching
    unmatchedSenders.sort((a, b) => a.createdAt - b.createdAt);

    // Collect unique emails to batch-resolve display names
    const allEmails = new Set<string>();
    for (const task of sortedTasks) {
      if (task.submitter_email) allEmails.add(task.submitter_email);
    }
    for (const s of senders) {
      allEmails.add(s.sender_email);
    }
    allEmails.add(session.owner_email);

    // Batch resolve display names
    const nameMap = new Map<string, string>();
    try {
      const { rows } = await pool.query<{ email: string; name: string | null }>(
        `SELECT email, name FROM users WHERE email = ANY($1)`,
        [Array.from(allEmails)],
      );
      for (const row of rows) {
        nameMap.set(row.email, row.name || displayNameFromEmail(row.email));
      }
    } catch {
      // users table issue — fall back to email parsing
    }

    // Fill in any emails not found in DB
    for (const email of allEmails) {
      if (!nameMap.has(email)) {
        nameMap.set(email, displayNameFromEmail(email));
      }
    }

    // Enrich events
    for (const evt of events) {
      if (evt.event_type !== "message.user") continue;

      const seq = evt.seq as number;
      const payload = (evt.payload ?? {}) as Record<string, unknown>;

      if (taskFirstSeqs.has(seq)) {
        const submitterEmail = taskFirstSeqs.get(seq)!;
        payload.user_name = nameMap.get(submitterEmail) ?? displayNameFromEmail(submitterEmail);
      } else if (senderBySeq.has(seq)) {
        const email = senderBySeq.get(seq)!;
        payload.user_name = nameMap.get(email) ?? displayNameFromEmail(email);
      } else {
        // Timestamp-based matching for unmatched senders:
        // Find the unmatched sender whose created_at is closest to the event timestamp
        const evtTime = evt.timestamp ? new Date(evt.timestamp as string).getTime() : NaN;
        let bestEmail: string | null = null;
        if (!isNaN(evtTime) && unmatchedSenders.length > 0) {
          let bestIdx = -1;
          let bestDist = Infinity;
          for (let i = 0; i < unmatchedSenders.length; i++) {
            if (unmatchedSenders[i].email === "__used__") continue;
            const dist = Math.abs(unmatchedSenders[i].createdAt - evtTime);
            if (dist < bestDist) {
              bestDist = dist;
              bestIdx = i;
            }
          }
          // Only match if within 5 seconds — otherwise we don't know who sent it
          if (bestIdx >= 0 && bestDist < 5000) {
            bestEmail = unmatchedSenders[bestIdx].email;
            unmatchedSenders[bestIdx].email = "__used__"; // mark as consumed
          }
        }
        payload.user_name = bestEmail
          ? (nameMap.get(bestEmail) ?? displayNameFromEmail(bestEmail))
          : (nameMap.get(session.owner_email) ?? displayNameFromEmail(session.owner_email));
      }

      evt.payload = payload;
    }
  }

  function sendError(res: ExpressResponse, status: number, error: string): void {
    res.status(status).json({ error });
  }

  // GET /:id/state
  router.get("/:id/state", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!await auth.canViewSession(session, agent)) { sendError(res, 403, "Forbidden"); return; }
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    try {
      const client = makeClient(agent.gateway_url);
      const state = await client.getState(session.gateway_session_id);
      res.json(state);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      sendError(res, 502, `Gateway error: ${msg}`);
    }
  });

  // GET /:id/rounds
  router.get("/:id/rounds", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!await auth.canViewSession(session, agent)) { sendError(res, 403, "Forbidden"); return; }
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    try {
      const client = makeClient(agent.gateway_url);
      const result = await client.getRounds(session.gateway_session_id);
      res.json(result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      sendError(res, 502, `Gateway error: ${msg}`);
    }
  });

  // GET /:id/events
  router.get("/:id/events", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!await auth.canViewSession(session, agent)) { sendError(res, 403, "Forbidden"); return; }
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    const afterSeq = req.query.after_seq ? parseInt(req.query.after_seq as string, 10) : undefined;
    const beforeSeq = req.query.before_seq ? parseInt(req.query.before_seq as string, 10) : undefined;

    try {
      const client = makeClient(agent.gateway_url);
      // Load ALL events across pages, then compact once.
      // If we compact per-page, tool.stdout partials spanning a page boundary
      // get truncated — tool.started can't be updated from a different page.
      const allEvents = await client.getAllEvents(session.gateway_session_id, afterSeq, beforeSeq);

      if (allEvents.length > 0) {
        // Apply access-level filtering before sending
        const agentAccessLevel = await auth.agentLevel(agent.id) ?? "interactive";
        const accessFilter = makeAccessFilterState(agentAccessLevel as any);
        const filteredEvents: Record<string, unknown>[] = [];

        for (const evt of allEvents) {
          const result = applyAccessFilter(evt as any, accessFilter);
          if (result === null) continue;

          const ev = result as any;
          // Handle readonly_summary: flush pending agent before user
          if (ev.__flushPendingAgent && accessFilter.pendingAgentMsg) {
            filteredEvents.push(accessFilter.pendingAgentMsg);
            accessFilter.pendingAgentMsg = null;
            delete ev.__flushPendingAgent;
          }

          filteredEvents.push(ev);
        }
        // Flush any remaining buffer at end
        for (const pending of flushAccessFilter(accessFilter)) {
          filteredEvents.push(pending);
        }

        // Enrich message.user events with user_name
        await enrichUserEvents(filteredEvents as any, session);
        // Deduplicate initial user messages (adapter bug: publishes duplicate)
        const deduped = dedupInitialUserMessage(filteredEvents as any);
        // Compact streaming events (message.delta, tool.stdout) for history loads.
        // These are useless for rendering past events — the final message.agent
        // and tool.finished contain the complete output. Saves ~97% payload.
        const compacted = compactStreamingEvents(deduped as any);
        // Backfill gateway_seq_start for any tasks missing it
        backfillSeqStarts(taskStore, session.id, compacted).catch(() => {});
        res.json({ events: compacted });
      } else {
        res.json({ events: [] });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      sendError(res, 502, `Gateway error: ${msg}`);
    }
  });

  // GET /:id/events/stream
  router.get("/:id/events/stream", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id as string, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!await auth.canViewSession(session, agent)) { sendError(res, 403, "Forbidden"); return; }
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    const afterSeq = req.query.after_seq ? parseInt(req.query.after_seq as string, 10) : undefined;
    const rawLastEventId = req.headers["last-event-id"];
    const lastEventId = Array.isArray(rawLastEventId) ? rawLastEventId[0] : rawLastEventId;

    let gwResponse: Response;
    try {
      const client = makeClient(agent.gateway_url);
      gwResponse = await client.openEventStream(session.gateway_session_id, afterSeq, lastEventId);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      sendError(res, 502, `Gateway error: ${msg}`);
      return;
    }

    // Set SSE headers
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    const body = gwResponse.body;
    if (!body) {
      res.end();
      return;
    }

    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    // Access-level event filter
    const agentAccessLevel = await auth.agentLevel(agent.id) ?? "interactive";
    const accessFilter = makeAccessFilterState(agentAccessLevel as any);

    const sseTaskDeps: SseTaskUpdaterDeps = {
      taskStore,
      sessionStore,
      makeClient,
      getAgents,
    };

    // For SSE, resolve sender names on the fly for message.user events.
    let sseSenders: Array<{ sender_email: string; gateway_seq: number | null; created_at: string }> = [];
    try {
      const { rows } = await pool.query<{ sender_email: string; gateway_seq: number | null; created_at: string }>(
        `SELECT sender_email, gateway_seq, created_at FROM message_senders WHERE session_id = $1 ORDER BY created_at ASC`,
        [session.id],
      );
      sseSenders = rows;
    } catch { /* table not yet migrated */ }

    // Build seq→email map and timestamp-indexed list for SSE
    const sseSenderBySeq = new Map<number, string>();
    const sseUnmatchedSenders: Array<{ email: string; createdAt: number }> = [];
    for (const s of sseSenders) {
      if (s.gateway_seq != null && s.gateway_seq > 0) {
        sseSenderBySeq.set(s.gateway_seq, s.sender_email);
      } else {
        sseUnmatchedSenders.push({ email: s.sender_email, createdAt: new Date(s.created_at).getTime() });
      }
    }
    sseUnmatchedSenders.sort((a, b) => a.createdAt - b.createdAt);
    let sseUnmatchedIdx = 0;

    const ownerDisplayName = await resolveDisplayName(session.owner_email);

    const sseNameCache = new Map<string, string>();
    sseNameCache.set(session.owner_email, ownerDisplayName);
    for (const s of sseSenders) {
      if (!sseNameCache.has(s.sender_email)) {
        sseNameCache.set(s.sender_email, await resolveDisplayName(s.sender_email));
      }
    }

    // Load task first-seq map for SSE
    const sseTasks = await taskStore.allForSession(session.id);
    const sseSortedTasks = [...sseTasks].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
    const sseTaskFirstSeqs = new Map<number, string>();
    for (const task of sseSortedTasks) {
      if (task.gateway_seq_start != null) {
        sseTaskFirstSeqs.set(task.gateway_seq_start, task.submitter_email ?? session.owner_email);
      }
    }

    const pump = async (): Promise<void> => {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const { parsed, remainder } = extractCompleteSseEvents(buffer);
          buffer = remainder;

          if (parsed.length === 0) {
            // No complete events yet — just keep buffering, don't forward raw bytes
            // (forwarding raw bytes would cause duplicates when we re-serialize later)
            continue;
          }

          // Deduplicate initial user messages (adapter bug: publishes duplicate)
          const dedupedParsed = dedupInitialUserMessage(parsed);

          for (const event of dedupedParsed) {
            // Apply access-level filtering
            const filtered = applyAccessFilter(event as any, accessFilter);
            if (filtered === null) continue;

            // For readonly_summary: flush pending message.agent before message.user
            const evt = (filtered as any);
            if (evt.__flushPendingAgent && accessFilter.pendingAgentMsg) {
              const pending = accessFilter.pendingAgentMsg;
              accessFilter.pendingAgentMsg = null;
              delete evt.__flushPendingAgent;
              const pSeq = pending.seq;
              const pType = pending.event_type as string;
              if (pSeq != null) res.write(`id: ${pSeq}\n`);
              res.write(`event: ${pType}\n`);
              res.write(`data: ${JSON.stringify(pending)}\n\n`);
            }

            if (evt.event_type === "message.user") {
              const seq = evt.seq as number;
              const payload = (evt.payload ?? {}) as Record<string, unknown>;

              if (sseTaskFirstSeqs.has(seq)) {
                const email = sseTaskFirstSeqs.get(seq)!;
                payload.user_name = sseNameCache.get(email) ?? displayNameFromEmail(email);
              } else if (sseSenderBySeq.has(seq)) {
                const email = sseSenderBySeq.get(seq)!;
                payload.user_name = sseNameCache.get(email) ?? displayNameFromEmail(email);
              } else {
                // Timestamp-based matching for unmatched senders
                const evtTime = evt.timestamp ? new Date(evt.timestamp as string).getTime() : NaN;
                let bestEmail: string | null = null;
                if (!isNaN(evtTime) && sseUnmatchedSenders.length > 0) {
                  let bestIdx = -1;
                  let bestDist = Infinity;
                  for (let i = 0; i < sseUnmatchedSenders.length; i++) {
                    if (sseUnmatchedSenders[i].email === "__used__") continue;
                    const dist = Math.abs(sseUnmatchedSenders[i].createdAt - evtTime);
                    if (dist < bestDist) { bestDist = dist; bestIdx = i; }
                  }
                  if (bestIdx >= 0 && bestDist < 5000) {
                    bestEmail = sseUnmatchedSenders[bestIdx].email;
                    sseUnmatchedSenders[bestIdx].email = "__used__";
                  }
                }
                if (bestEmail) {
                  payload.user_name = sseNameCache.get(bestEmail) ?? displayNameFromEmail(bestEmail);
                  try {
                    await pool.query(
                      `UPDATE message_senders SET gateway_seq = $1 WHERE session_id = $2 AND sender_email = $3 AND gateway_seq IS NULL ORDER BY created_at ASC LIMIT 1`,
                      [seq, session.id, bestEmail]
                    );
                  } catch { /* non-critical */ }
                } else {
                  // Ran out of pre-loaded senders — refresh from DB to pick up new ones
                  try {
                    const { rows: fresh } = await pool.query<{ sender_email: string; gateway_seq: number | null }>(
                      `SELECT sender_email, gateway_seq FROM message_senders WHERE session_id = $1 AND gateway_seq IS NULL ORDER BY created_at ASC`,
                      [session.id],
                    );
                    if (fresh.length > 0) {
                      for (const s of fresh) {
                        if (!sseNameCache.has(s.sender_email)) {
                          sseNameCache.set(s.sender_email, await resolveDisplayName(s.sender_email));
                        }
                      }
                      const freshWithTime = fresh.map(s => ({
                        email: s.sender_email,
                        createdAt: Date.now()
                      }));
                      if (freshWithTime.length > 0) {
                        bestEmail = freshWithTime[0].email;
                        payload.user_name = sseNameCache.get(bestEmail) ?? displayNameFromEmail(bestEmail);
                        try {
                          await pool.query(
                            `UPDATE message_senders SET gateway_seq = $1 WHERE session_id = $2 AND sender_email = $3 AND gateway_seq IS NULL ORDER BY created_at ASC LIMIT 1`,
                            [seq, session.id, bestEmail]
                          );
                        } catch { /* non-critical */ }
                      } else {
                        payload.user_name = ownerDisplayName;
                      }
                    } else {
                      payload.user_name = ownerDisplayName;
                    }
                  } catch {
                    payload.user_name = ownerDisplayName;
                  }
                }
              }
              evt.payload = payload;
            }
            const seq = evt.seq;
            const eventType = evt.event_type as string;
            if (seq != null) res.write(`id: ${seq}\n`);
            res.write(`event: ${eventType}\n`);
            res.write(`data: ${JSON.stringify(evt)}\n\n`);
          }

          // Flush any remaining buffered agent message at end of stream
          for (const pending of flushAccessFilter(accessFilter)) {
            const pSeq = pending.seq;
            const pType = pending.event_type as string;
            if (pSeq != null) res.write(`id: ${pSeq}\n`);
            res.write(`event: ${pType}\n`);
            res.write(`data: ${JSON.stringify(pending)}\n\n`);
          }

          for (const event of parsed) {
            applyEventFn(sseTaskDeps, session.id, event).catch((err) => {
              console.error("applyEventToTask error:", err);
            });
          }
        }
      } catch {
        // Client likely disconnected
      } finally {
        res.end();
      }
    };

    req.on("close", () => {
      reader.cancel().catch(() => {});
    });

    void pump();
  });

  // ─── Owner-only actions ────────────────────────────────────────────

  // POST /:id/permissions/:permId/approve
  router.post("/:id/permissions/:permId/approve", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }
    if (!auth.isSessionOwner(session)) { sendError(res, 403, "Forbidden"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    try {
      const client = makeClient(agent.gateway_url);
      const result = await client.approvePermission(session.gateway_session_id, req.params.permId);
      // Note: sender is recorded by the sendGuidance() call that follows this approve,
      // via the /messages route which captures gateway_seq for reliable matching.
      res.json(result);
    } catch (err) {
      sendError(res, 502, `Gateway error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });

  // POST /:id/permissions/:permId/deny
  router.post("/:id/permissions/:permId/deny", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }
    if (!auth.isSessionOwner(session)) { sendError(res, 403, "Forbidden"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    try {
      const client = makeClient(agent.gateway_url);
      const result = await client.denyPermission(session.gateway_session_id, req.params.permId, req.body?.reason);
      // Note: sender is recorded by the sendGuidance() call that follows this deny,
      // via the /messages route which captures gateway_seq for reliable matching.
      res.json(result);
    } catch (err) {
      sendError(res, 502, `Gateway error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });

  // POST /:id/questions/:questionId/answer
  router.post("/:id/questions/:questionId/answer", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }
    if (!auth.isSessionOwner(session)) { sendError(res, 403, "Forbidden"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    const answers = req.body?.answers;
    if (!answers || typeof answers !== "object") {
      sendError(res, 400, "answers object is required");
      return;
    }

    try {
      const client = makeClient(agent.gateway_url);
      const result = await client.answerQuestion(session.gateway_session_id, req.params.questionId, answers);
      res.json(result);
    } catch (err) {
      sendError(res, 502, `Gateway error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });

  // POST /:id/interrupt
  router.post("/:id/interrupt", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }
    if (!auth.isSessionOwner(session)) { sendError(res, 403, "Forbidden"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    try {
      const client = makeClient(agent.gateway_url);
      const result = await client.interrupt(session.gateway_session_id);
      res.json(result);
    } catch (err) {
      sendError(res, 502, `Gateway error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });

  // POST /:id/resume
  router.post("/:id/resume", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);
    if (isNaN(sessionId)) { sendError(res, 400, "Invalid session id"); return; }

    const session = await sessionStore.getById(sessionId);
    if (!session) { sendError(res, 404, "Session not found"); return; }
    if (!auth.isSessionOwner(session)) { sendError(res, 403, "Forbidden"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    // Get current event count before resuming, so we can find the new message.user event
    let beforeSeq: number | undefined;
    try {
      const client = makeClient(agent.gateway_url);
      const state = await client.getState(session.gateway_session_id) as { last_seq?: number };
      beforeSeq = typeof state?.last_seq === "number" ? state.last_seq : undefined;
    } catch { /* best effort */ }

    try {
      const client = makeClient(agent.gateway_url);
      const result = await client.resume(session.gateway_session_id, req.body?.content);

      // Record who sent this message, with the gateway_seq for reliable matching
      try {
        let gatewaySeq: number | null = null;
        if (beforeSeq != null) {
          const client = makeClient(agent.gateway_url);
          const eventsData = await client.getEvents(session.gateway_session_id, beforeSeq) as {
            events?: Array<{ seq: number; event_type: string }>;
          };
          const msgEvents = (eventsData?.events ?? [])
            .filter((e: { seq: number; event_type: string }) => e.event_type === "message.user")
            .sort((a: { seq: number }, b: { seq: number }) => b.seq - a.seq);
          if (msgEvents.length > 0) {
            gatewaySeq = msgEvents[0].seq;
          }
        }
        await pool.query(
          `INSERT INTO message_senders (session_id, sender_email, gateway_seq) VALUES ($1, $2, $3)`,
          [sessionId, auth.email, gatewaySeq],
        );
      } catch {
        // Non-critical — enrichment falls back to task submitter / session owner
      }

      res.json(result);
    } catch (err) {
      sendError(res, 502, `Gateway error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });

  // --- Sub-agent transcript endpoint ---
  router.get("/:id/subagents/:taskId/transcript", async (req, res) => {
    const { id, taskId } = req.params;
    const session = await sessionStore.getById(Number(id));
    if (!session) { sendError(res, 404, "Session not found"); return; }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!agent) { sendError(res, 502, "Agent not found"); return; }

    try {
      const client = makeClient(agent.gateway_url);
      const result = await client.getSubagentTranscript(session.gateway_session_id, taskId);
      res.json(result);
    } catch (err) {
      sendError(res, 502, `Gateway error: ${err instanceof Error ? err.message : String(err)}`);
    }
  });

  return router;
}
