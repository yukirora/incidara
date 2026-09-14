import { Router } from "express";
import { z } from "zod";
import { CronExpressionParser } from "cron-parser";
import type { ScheduleStore } from "../schedule-store.js";
import type { SessionStore } from "../session-store.js";
import type { TaskStore } from "../task-store.js";
import { GatewayClient } from "../gateway-client.js";
import type { Agent } from "../agent-registry.js";
import { fireSchedule } from "../scheduler.js";

// ── Zod schemas ───────────────────────────────────────────────────────────────

const CreateScheduleSchema = z
  .object({
    agent_id: z.string().min(1),
    name: z.string().optional(),
    prompt: z.string().min(1),
    trigger_type: z.enum(["once", "interval", "cron"]),
    run_at: z.string().datetime().optional(),
    interval_seconds: z.number().min(30).optional(),
    cron_expr: z.string().optional(),
    timezone: z.string().default("UTC"),
    session_mode: z.enum(["new", "reuse"]).default("new"),
    reuse_session_id: z.number().int().positive().optional(),
    completion_mode: z.enum(["auto", "manual"]).default("auto"),
  })
  .refine(
    (d) => {
      if (d.trigger_type === "once") return !!d.run_at;
      if (d.trigger_type === "interval") return !!d.interval_seconds;
      if (d.trigger_type === "cron") return !!d.cron_expr;
      return false;
    },
    { message: "Missing trigger config for the chosen trigger type" }
  )
  .refine(
    (d) => {
      if (d.trigger_type === "once" && d.run_at) {
        return new Date(d.run_at) > new Date();
      }
      return true;
    },
    { message: "run_at must be in the future for trigger_type='once'" }
  )
  .refine(
    (d) => {
      if (d.trigger_type === "cron" && d.cron_expr) {
        try {
          CronExpressionParser.parse(d.cron_expr, { tz: d.timezone });
          return true;
        } catch {
          return false;
        }
      }
      return true;
    },
    { message: "Invalid cron expression" }
  )
  .refine(
    (d) => {
      if (d.session_mode === "reuse") return !!d.reuse_session_id;
      return true;
    },
    { message: "reuse_session_id is required when session_mode='reuse'" }
  );

const PatchScheduleSchema = z
  .object({
    name: z.string().nullable().optional(),
    prompt: z.string().min(1).optional(),
    trigger_type: z.enum(["once", "interval", "cron"]).optional(),
    run_at: z.string().datetime().nullable().optional(),
    interval_seconds: z.number().min(30).nullable().optional(),
    cron_expr: z.string().nullable().optional(),
    timezone: z.string().optional(),
    session_mode: z.enum(["new", "reuse"]).optional(),
    reuse_session_id: z.number().int().positive().nullable().optional(),
    completion_mode: z.enum(["auto", "manual"]).optional(),
    enabled: z.boolean().optional(),
  })
  .refine(
    (d) => {
      if (d.trigger_type === "cron" && d.cron_expr) {
        try {
          CronExpressionParser.parse(d.cron_expr, { tz: d.timezone ?? "UTC" });
          return true;
        } catch {
          return false;
        }
      }
      return true;
    },
    { message: "Invalid cron expression" }
  );

// ── Router factory ────────────────────────────────────────────────────────────

interface SchedulesRouterDeps {
  scheduleStore: ScheduleStore;
  sessionStore: SessionStore;
  taskStore: TaskStore;
  getAgents: () => Agent[];
  makeClient: (gatewayUrl: string) => GatewayClient;
}

export function makeSchedulesRouter(deps: SchedulesRouterDeps): Router {
  const { scheduleStore, sessionStore, taskStore, getAgents, makeClient } = deps;
  const router = Router();

  // POST /api/schedules — create
  router.post("/", async (req, res): Promise<void> => {
    const auth = req.auth!;

    const parsed = CreateScheduleSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const data = parsed.data;

    // Check agent access
    const agent = getAgents().find((a) => a.id === data.agent_id);
    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }
    if (!await auth.canAccessAgent(agent)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    // Check reuse session ownership
    if (data.session_mode === "reuse" && data.reuse_session_id) {
      const session = await sessionStore.getById(data.reuse_session_id);
      if (!session) {
        res.status(404).json({ error: "Session not found" });
        return;
      }
      if (!auth.isSessionOwner(session)) {
        res.status(403).json({ error: "You do not own that session" });
        return;
      }
      if (session.agent_id !== data.agent_id) {
        res.status(400).json({ error: "Session belongs to a different agent" });
        return;
      }
    }

    const schedule = await scheduleStore.create({
      agentId: data.agent_id,
      name: data.name,
      ownerEmail: auth.email,
      prompt: data.prompt,
      triggerType: data.trigger_type,
      runAt: data.run_at ? new Date(data.run_at) : undefined,
      intervalSeconds: data.interval_seconds,
      cronExpr: data.cron_expr,
      timezone: data.timezone,
      sessionMode: data.session_mode,
      reuseSessionId: data.reuse_session_id,
      completionMode: data.completion_mode,
    });

    res.status(201).json({ schedule });
  });

  // GET /api/schedules — list
  router.get("/", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const agentId = req.query.agent_id as string | undefined;
    const enabledRaw = req.query.enabled as string | undefined;
    const enabled =
      enabledRaw === "true" ? true : enabledRaw === "false" ? false : undefined;

    // Non-admins see only their own; admins see all
    const schedules = await scheduleStore.list({
      ownerEmail: auth.isAdmin ? undefined : auth.email,
      agentId,
      enabled,
    });
    res.json({ schedules });
  });

  // GET /api/schedules/:id — get one
  router.get("/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid schedule id" });
      return;
    }

    const schedule = await scheduleStore.getById(id);
    if (!schedule) {
      res.status(404).json({ error: "Schedule not found" });
      return;
    }

    if (!auth.isAdmin && schedule.owner_email !== auth.email) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    res.json({ schedule });
  });

  // PATCH /api/schedules/:id — update
  router.patch("/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid schedule id" });
      return;
    }

    const schedule = await scheduleStore.getById(id);
    if (!schedule) {
      res.status(404).json({ error: "Schedule not found" });
      return;
    }

    if (!auth.isAdmin && schedule.owner_email !== auth.email) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    const parsed = PatchScheduleSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const data = parsed.data;

    const updated = await scheduleStore.update(id, {
      name: data.name,
      prompt: data.prompt,
      triggerType: data.trigger_type,
      runAt: data.run_at !== undefined ? (data.run_at ? new Date(data.run_at) : null) : undefined,
      intervalSeconds: data.interval_seconds,
      cronExpr: data.cron_expr,
      timezone: data.timezone,
      sessionMode: data.session_mode,
      reuseSessionId: data.reuse_session_id,
      completionMode: data.completion_mode,
      enabled: data.enabled,
    });

    res.json({ schedule: updated });
  });

  // DELETE /api/schedules/:id
  router.delete("/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid schedule id" });
      return;
    }

    const schedule = await scheduleStore.getById(id);
    if (!schedule) {
      res.status(404).json({ error: "Schedule not found" });
      return;
    }

    if (!auth.isAdmin && schedule.owner_email !== auth.email) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    await scheduleStore.delete(id);
    res.status(204).send();
  });

  // POST /api/schedules/:id/run-now
  router.post("/:id/run-now", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid schedule id" });
      return;
    }

    const schedule = await scheduleStore.getById(id);
    if (!schedule) {
      res.status(404).json({ error: "Schedule not found" });
      return;
    }

    if (!auth.isAdmin && schedule.owner_email !== auth.email) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    try {
      await fireSchedule(
        { scheduleStore, taskStore, sessionStore, getAgents, makeClient },
        schedule
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(502).json({ error: `Failed to fire schedule: ${msg}` });
      return;
    }

    await scheduleStore.recordFiring(id, {
      firedAt: new Date(),
      nextFireAt: schedule.next_fire_at,
    });

    res.json({ ok: true });
  });

  return router;
}
