import type { ScheduleStore, Schedule } from "./schedule-store.js";
import { computeNextFire } from "./schedule-store.js";
import type { TaskStore } from "./task-store.js";
import type { SessionStore } from "./session-store.js";
import type { GatewayClient } from "./gateway-client.js";
import type { Agent } from "./agent-registry.js";

export interface SchedulerDeps {
  scheduleStore: ScheduleStore;
  taskStore: TaskStore;
  sessionStore: SessionStore;
  getAgents: () => Agent[];
  makeClient: (gatewayUrl: string) => GatewayClient;
}

/**
 * Fire a single schedule: create a session + task (new mode) or add task to
 * an existing session (reuse mode). Tags the task with from_schedule_id.
 *
 * Does NOT update the schedule record — call recordFiring separately.
 * Throws on hard errors (agent not found, gateway down).
 */
export async function fireSchedule(deps: SchedulerDeps, schedule: Schedule): Promise<void> {
  const { taskStore, sessionStore, getAgents, makeClient } = deps;

  const agent = getAgents().find((a) => a.id === schedule.agent_id);
  if (!agent) {
    throw new Error(`[scheduler] agent ${schedule.agent_id} not found`);
  }

  const client = makeClient(agent.gateway_url);

  if (schedule.session_mode === "new") {
    // Create a brand-new session + initial task at the gateway
    const gw = await client.createSession({ prompt: schedule.prompt });
    const session = await sessionStore.create({
      gatewaySessionId: gw.id,
      agentId: schedule.agent_id,
      ownerEmail: schedule.owner_email,
      title: schedule.name ?? undefined,
    });
    await taskStore.create({
      sessionId: session.id,
      prompt: schedule.prompt,
      submitterEmail: schedule.owner_email,
      status: "running",
      fromScheduleId: schedule.id,
      completionMode: schedule.completion_mode,
    });
  } else {
    // Reuse an existing session
    const sessionId = schedule.reuse_session_id;
    if (!sessionId) {
      throw new Error(`[scheduler] schedule ${schedule.id} has session_mode=reuse but no reuse_session_id`);
    }

    const session = await sessionStore.getById(sessionId);
    if (!session) {
      throw new Error(`[scheduler] session ${sessionId} not found for schedule ${schedule.id}`);
    }

    // Check if there's an active task — if so, queue as pending
    const activeTask = await taskStore.latestActiveForSession(sessionId);

    if (activeTask) {
      // Queue as pending — gateway will be called when previous task completes
      await taskStore.create({
        sessionId,
        prompt: schedule.prompt,
        submitterEmail: schedule.owner_email,
        status: "pending",
        fromScheduleId: schedule.id,
        completionMode: schedule.completion_mode,
      });
    } else {
      // Run immediately
      const task = await taskStore.create({
        sessionId,
        prompt: schedule.prompt,
        submitterEmail: schedule.owner_email,
        status: "running",
        fromScheduleId: schedule.id,
        completionMode: schedule.completion_mode,
      });

      try {
        await client.sendMessage(session.gateway_session_id, schedule.prompt);
      } catch (err) {
        // Mark as error on gateway failure
        const msg = err instanceof Error ? err.message : String(err);
        await taskStore.updateStatus(task.id, "error", new Date());
        await taskStore.updateOutputPreview(task.id, `Error: ${msg}`.slice(0, 200));
        throw err; // rethrow so caller knows firing failed
      }
    }
  }
}

/**
 * One iteration of the scheduler loop:
 * 1. Claim due schedules
 * 2. Fire each one
 * 3. Record the firing (advancing next_fire_at or disabling once schedules)
 *
 * Gateway errors are logged but do NOT disable the schedule (retried next tick).
 */
export async function fireOnce(deps: SchedulerDeps): Promise<void> {
  const { scheduleStore } = deps;

  const now = new Date();
  const due = await scheduleStore.claimDue({ now, limit: 50 });

  for (const schedule of due) {
    let gatewayError = false;
    try {
      await fireSchedule(deps, schedule);
    } catch (err) {
      console.error(`[scheduler] failed to fire schedule ${schedule.id}:`, err);
      gatewayError = true;
    }

    if (gatewayError) {
      // Don't update the schedule — next tick will retry
      continue;
    }

    // Compute next_fire_at
    const firedAt = new Date();
    const nextFireAt = computeNextFire({
      triggerType: schedule.trigger_type,
      runAt: schedule.run_at,
      intervalSeconds: schedule.interval_seconds,
      cronExpr: schedule.cron_expr,
      timezone: schedule.timezone,
      from: firedAt,
    });

    // once schedules become null → disable; others advance
    const disableIfDone = schedule.trigger_type === "once";
    await scheduleStore.recordFiring(schedule.id, { firedAt, nextFireAt, disableIfDone });
  }
}

/**
 * Start the periodic scheduler. Returns a stop function.
 */
export function startScheduler(deps: SchedulerDeps, intervalMs = 30_000): () => void {
  const timer = setInterval(() => {
    fireOnce(deps).catch((err) => {
      console.error("[scheduler] unhandled error in fireOnce:", err);
    });
  }, intervalMs);

  timer.unref();

  return () => clearInterval(timer);
}
