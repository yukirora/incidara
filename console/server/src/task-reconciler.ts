import type pg from "pg";
import type { TaskStore } from "./task-store.js";
import type { SessionStore } from "./session-store.js";
import type { GatewayClient } from "./gateway-client.js";
import type { Agent } from "./agent-registry.js";
import type { Task, TaskStatus } from "./task-store.js";
import { ACTIVE_STATUSES } from "./task-store.js";
import { promoteNext } from "./sse-task-updater.js";

export interface ReconcilerDeps {
  pool: pg.Pool;
  taskStore: TaskStore;
  sessionStore: SessionStore;
  getAgents: () => Agent[];
  makeClient: (gatewayUrl: string) => GatewayClient;
}

// Gateway states that map to terminal task statuses
const GATEWAY_TERMINAL_STATES: Record<string, "completed" | "error" | "interrupted"> = {
  completed: "completed",
  error: "error",
  interrupted: "interrupted",
};

export async function reconcileOnce(deps: ReconcilerDeps): Promise<void> {
  const { pool, taskStore, sessionStore, getAgents, makeClient } = deps;

  // Pass A: Status sync — check active + interrupted + errored tasks against gateway.
  // "error" tasks are included so the reconciler can recover them when the
  // adapter auto-recovered (e.g. compact-and-resume after context overflow)
  // but the SSE session.error event arrived before session.started.
  //
  // IMPORTANT: For sessions with multiple tasks, the gateway session state
  // reflects the LATEST round. Only the latest task should be synced against
  // the gateway state. Earlier tasks may have their own terminal state from
  // SSE events (error, interrupted, etc.) that should not be overridden.
  let tasksToReconcile: Task[] = [];
  try {
    // Only reconcile the latest task per session — older tasks keep their SSE-set status
    const { rows } = await pool.query<Task>(
      `SELECT DISTINCT ON (session_id) *
       FROM tasks
       WHERE status IN ('running', 'busy', 'waiting_input', 'interrupted', 'error')
       ORDER BY session_id, created_at DESC`
    );
    tasksToReconcile = rows;
  } catch (err) {
    console.error("reconcileOnce: failed to list tasks", err);
  }

  for (const task of tasksToReconcile) {
    try {
      const session = await sessionStore.getById(task.session_id);
      if (!session) continue;

      const agent = getAgents().find((a) => a.id === session.agent_id);
      if (!agent) continue;

      const client = makeClient(agent.gateway_url);

      // First check if the gateway is reachable at all
      let state: { status?: string } | null;
      try {
        state = await client.getState(session.gateway_session_id) as { status?: string } | null;
      } catch (err) {
        // Gateway unreachable — if task was running/waiting_input (agent may have
        // restarted), move to interrupted so user can resume later
        if (task.status === "running" || task.status === "waiting_input") {
          await pool.query(
            `UPDATE tasks SET status = 'interrupted', ended_at = now() WHERE id = $1`,
            [task.id]
          );
          console.log(`reconcileOnce: task ${task.id} ${task.status} → interrupted (gateway unreachable)`);
        }
        continue;
      }

      if (!state?.status) continue;

      // If task is interrupted but gateway says session is still active, reactivate
      // Map gateway "busy" to "running" since chat-ui doesn't distinguish them.
      if (task.status === "interrupted" && ACTIVE_STATUSES.includes(state.status as TaskStatus)) {
        const recoveredStatus = state.status === "busy" ? "running" : state.status;
        await pool.query(
          `UPDATE tasks SET status = $1, ended_at = NULL WHERE id = $2`,
          [recoveredStatus, task.id]
        );
        console.log(`reconcileOnce: task ${task.id} interrupted → ${recoveredStatus} (gateway still active)`);
        continue;
      }

      // Stale-running detection: if a task has been "running" for longer than
      // STALE_RUNNING_TIMEOUT_MS (default 30 min) with no gateway activity,
      // it's likely a zombie (e.g. container restart killed the session).
      // Check by fetching the session's updated_at from the gateway.
      if (task.status === "running" && state.status === "running") {
        const STALE_TIMEOUT_MS = parseInt(process.env.STALE_RUNNING_TIMEOUT_MS || "1800000", 10); // 30 min
        const taskAge = Date.now() - (task.started_at?.getTime() ?? task.created_at.getTime());
        if (taskAge > STALE_TIMEOUT_MS) {
          // Query gateway session details for updated_at
          try {
            const details = await client.getState(session.gateway_session_id) as Record<string, unknown>;
            const lastUpdate = details?.updated_at as string | undefined;
            if (lastUpdate) {
              const staleMs = Date.now() - new Date(lastUpdate).getTime();
              if (staleMs > STALE_TIMEOUT_MS) {
                await pool.query(
                  `UPDATE tasks SET status = 'error', ended_at = now(), output_preview = 'Session stale (no activity for ' || ($1::bigint / 60000)::text || ' min) — likely lost after restart. Start a new task.' WHERE id = $2`,
                  [staleMs, task.id]
                );
                console.log(`reconcileOnce: task ${task.id} running → error (stale for ${Math.round(staleMs / 60000)} min)`);
                continue;
              }
            }
          } catch {
            // Can't get details — skip staleness check this round
          }
        }
      }

      // If task is error but gateway session is running/waiting_input/busy, the adapter
      // auto-recovered (e.g. compact-and-resume). Re-activate the task.
      // Map gateway "busy" to "running" since chat-ui doesn't distinguish them.
      if (task.status === "error" && ACTIVE_STATUSES.includes(state.status as TaskStatus)) {
        const recoveredStatus = state.status === "busy" ? "running" : state.status;
        await pool.query(
          `UPDATE tasks SET status = $1, ended_at = NULL WHERE id = $2`,
          [recoveredStatus, task.id]
        );
        console.log(`reconcileOnce: task ${task.id} error → ${recoveredStatus} (gateway recovered)`);
        continue;
      }

      // Sync non-terminal state changes (running ↔ waiting_input)
      // This catches transitions that the SSE stream may have missed.
      // Note: gateway uses "busy" when agent is executing a tool; map it to "running"
      // because the chat-ui task model doesn't distinguish "busy" from "running".
      let syncStatus = state.status;
      if (syncStatus === "busy") syncStatus = "running";
      if (ACTIVE_STATUSES.includes(syncStatus as TaskStatus) && task.status !== syncStatus) {
        await pool.query(
          `UPDATE tasks SET status = $1 WHERE id = $2`,
          [syncStatus, task.id]
        );
        console.log(`reconcileOnce: task ${task.id} ${task.status} → ${syncStatus} (gateway state sync)`);
        continue;
      }

      const terminalStatus = GATEWAY_TERMINAL_STATES[state.status];
      if (terminalStatus) {
        // If manual completion mode and gateway says completed, move to waiting_input
        // (user must manually mark the task as completed)
        if (
          terminalStatus === "completed" &&
          task.completion_mode === "manual"
        ) {
          if (task.status !== "waiting_input") {
            await pool.query(
              `UPDATE tasks SET status = $1, ended_at = NULL WHERE id = $2`,
              ["waiting_input", task.id]
            );
            console.log(`reconcileOnce: task ${task.id} running → waiting_input (manual completion, gateway completed)`);
          }
          continue;
        }

        // If gateway session is interrupted (e.g. after agent docker restart) and
        // task was running/waiting_input, attempt to resume the task by sending
        // the original prompt back to the gateway session. This recovers tasks
        // that were active when the agent container restarted.
        if (terminalStatus === "interrupted" && task.completion_mode === "manual") {
          // For manual tasks, move to interrupted so the user knows the agent stopped
          // (not waiting_input which implies the agent finished successfully)
          if (task.status !== "interrupted") {
            await pool.query(
              `UPDATE tasks SET status = $1, ended_at = now() WHERE id = $2`,
              ["interrupted", task.id]
            );
            console.log(`reconcileOnce: task ${task.id} ${task.status} → interrupted (gateway interrupted, manual task)`);
          }
          continue;
        }

        // For auto tasks with interrupted gateway, attempt auto-resume
        if (terminalStatus === "interrupted" && task.completion_mode === "auto") {
          try {
            await client.resume(session.gateway_session_id, task.prompt);
            await pool.query(
              `UPDATE tasks SET status = 'running', ended_at = NULL WHERE id = $1`,
              [task.id]
            );
            console.log(`reconcileOnce: task ${task.id} interrupted → running (auto-resumed via gateway)`);
          } catch (resumeErr) {
            // Resume failed — mark as interrupted, user can manually retry
            if (task.status !== "interrupted") {
              await pool.query(
                `UPDATE tasks SET status = 'interrupted', ended_at = now() WHERE id = $1`,
                [task.id]
              );
              console.log(`reconcileOnce: task ${task.id} → interrupted (auto-resume failed: ${resumeErr instanceof Error ? resumeErr.message : String(resumeErr)})`);
            }
          }
          continue;
        }

        // Skip if already in this terminal state (avoid log spam)
        if (task.status === terminalStatus) continue;

        // Context overflow (session-level "Prompt is too long") is NOT truly terminal —
        // the user can start a new session to continue. Move to waiting_input instead
        // of error so the UI shows a recoverable state.
        if (terminalStatus === "error") {
          const lastPreview = (state as Record<string, unknown>).last_message_preview as string | undefined;
          const isContextOverflow = lastPreview?.includes("Prompt is too long")
            || lastPreview?.includes("prompt_too_long")
            || lastPreview?.includes("context window")
            || lastPreview?.includes("context_length_exceeded");

          if (isContextOverflow) {
            await pool.query(
              `UPDATE tasks SET status = 'waiting_input', ended_at = NULL WHERE id = $1`,
              [task.id]
            );
            console.log(`reconcileOnce: task ${task.id} → waiting_input (session context full)`);
            continue;
          }
        }

        await pool.query(
          `UPDATE tasks SET status = $1, ended_at = now() WHERE id = $2`,
          [terminalStatus, task.id]
        );
        console.log(`reconcileOnce: task ${task.id} → ${terminalStatus}`);
      }
    } catch (err) {
      console.error(`reconcileOnce: error processing task ${task.id}:`, err);
    }
  }

  // Pass B: Queue promotion — find sessions with pending tasks and no active tasks
  let sessionIds: number[] = [];
  try {
    const { rows } = await pool.query<{ session_id: number }>(
      `SELECT DISTINCT session_id
       FROM tasks t
       WHERE status = 'pending'
         AND NOT EXISTS (
           SELECT 1 FROM tasks
           WHERE session_id = t.session_id
             AND status IN ('running', 'busy', 'waiting_input')
         )`
    );
    sessionIds = rows.map((r) => r.session_id);
  } catch (err) {
    console.error("reconcileOnce: failed to get sessions with pending tasks", err);
  }

  for (const sessionId of sessionIds) {
    try {
      await promoteNext({ taskStore, sessionStore, getAgents, makeClient }, sessionId);
    } catch (err) {
      console.error(`reconcileOnce: error promoting task for session ${sessionId}:`, err);
    }
  }
}

export function startReconciler(
  deps: ReconcilerDeps,
  intervalMs: number = 60_000
): () => void {
  const timer = setInterval(() => {
    reconcileOnce(deps).catch((err) => {
      console.error("Reconciler error:", err);
    });
  }, intervalMs);

  // .unref() so it doesn't block process shutdown
  timer.unref();

  return () => clearInterval(timer);
}
