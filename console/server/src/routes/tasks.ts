import { Router } from "express";
import { z } from "zod";
import type { TaskStore, TaskStatus } from "../task-store.js";
import type { SessionStore } from "../session-store.js";
import type { GatewayClient } from "../gateway-client.js";
import type { Agent } from "../agent-registry.js";
import { promoteNext } from "../sse-task-updater.js";

const CreateTaskSchema = z.object({
  prompt: z.string().min(1),
  title: z.string().optional(),
  completion_mode: z.enum(['auto', 'manual']).optional(),
});

interface TasksRouterDeps {
  taskStore: TaskStore;
  sessionStore: SessionStore;
  getAgents: () => Agent[];
  getEnrichedAgents: () => Promise<Agent[]>;
  makeClient: (gatewayUrl: string) => GatewayClient;
}

export function makeTasksRouter(deps: TasksRouterDeps): Router {
  const { taskStore, sessionStore, getAgents, getEnrichedAgents, makeClient } = deps;
  const router = Router();

  // GET /tasks
  router.get("/tasks", async (req, res): Promise<void> => {
    const auth = req.auth!;

    const status = req.query.status as TaskStatus | undefined;
    const statusesRaw = req.query.statuses as string | undefined;
    const statuses = statusesRaw ? statusesRaw.split(",") : undefined;
    const agentId = req.query.agent_id as string | undefined;
    const scope = req.query.scope as "mine" | "shared" | "all" | undefined;
    const titleSearch = req.query.title_search as string | undefined;
    const limit = Math.min(parseInt(req.query.limit as string ?? "50", 10), 200);
    const offset = parseInt(req.query.offset as string ?? "0", 10);
    const sessionIdRaw = req.query.session_id as string | undefined;
    const sessionId = sessionIdRaw ? parseInt(sessionIdRaw, 10) : undefined;

    // Compute accessible agent IDs — agent access gates task visibility
    // Use enriched agents (YAML + DB group overrides) for accurate access check
    const enrichedAgents = await getEnrichedAgents();
    const agentAccessBools = await Promise.all(enrichedAgents.map(a => auth.canAccessAgent(a)));
    const accessibleAgentIds = enrichedAgents.filter((_, i) => agentAccessBools[i]).map(a => a.id);

    const result = await taskStore.list({
      email: auth.email,
      groups: auth.groups,
      isAdmin: auth.isAdmin,
      accessibleAgentIds,
      status,
      statuses,
      agentId,
      sessionId: sessionId !== undefined && !isNaN(sessionId) ? sessionId : undefined,
      scope,
      titleSearch,
      limit,
      offset,
    });

    res.json(result);
  });

  // GET /tasks/:id
  router.get("/tasks/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid task id" });
      return;
    }

    const task = await taskStore.getById(id);
    if (!task) {
      res.status(404).json({ error: "Task not found" });
      return;
    }

    const session = await sessionStore.getById(task.session_id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!await auth.canViewSession(session, agent)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    res.json({ task });
  });

  // POST /tasks/:id/complete
  router.post("/tasks/:id/complete", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid task id" });
      return;
    }

    const task = await taskStore.getById(id);
    if (!task) {
      res.status(404).json({ error: "Task not found" });
      return;
    }

    if (!auth.isTaskOwner(task.submitter_email)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    if (task.status !== "running" && task.status !== "waiting_input" && task.status !== "busy" && task.status !== "interrupted") {
      res.status(409).json({ error: "Task is not in a completable state" });
      return;
    }

    const updated = await taskStore.completeManually(id);
    if (!updated) {
      res.status(409).json({ error: "Task could not be completed — status may have changed" });
      return;
    }

    // Trigger queue promotion
    await promoteNext(
      { taskStore, sessionStore, getAgents, makeClient },
      task.session_id
    );

    res.json({ task: updated });
  });

  // DELETE /tasks/:id
  router.delete("/tasks/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid task id" });
      return;
    }

    const task = await taskStore.getById(id);
    if (!task) {
      res.status(404).json({ error: "Task not found" });
      return;
    }

    if (!auth.isTaskOwner(task.submitter_email)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    const rowCount = await taskStore.cancelPending(id);
    if (rowCount === 0) {
      res.status(409).json({ error: "Task is not pending and cannot be cancelled" });
      return;
    }

    res.status(204).send();
  });

  // POST /sessions/:id/tasks
  router.post("/sessions/:id/tasks", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const sessionId = parseInt(req.params.id, 10);

    if (isNaN(sessionId)) {
      res.status(400).json({ error: "Invalid session id" });
      return;
    }

    const session = await sessionStore.getById(sessionId);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }

    if (!auth.isSessionOwner(session)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    const parsed = CreateTaskSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const { prompt, title, completion_mode } = parsed.data;

    // Check if there's an active task
    const activeTask = await taskStore.latestActiveForSession(sessionId);

    if (activeTask) {
      // Queue the task as pending — no gateway call
      const task = await taskStore.create({
        sessionId,
        prompt,
        submitterEmail: auth.email,
        status: "pending",
        title,
        completionMode: completion_mode,
      });
      res.status(201).json({ task });
    } else {
      // No active task — run immediately
      const task = await taskStore.create({
        sessionId,
        prompt,
        submitterEmail: auth.email,
        status: "running",
        title,
        completionMode: completion_mode,
      });

      // Send to gateway — use sendMessage if session is active, resume if completed
      const agent = getAgents().find((a) => a.id === session.agent_id);
      if (agent) {
        try {
          const client = makeClient(agent.gateway_url);
          try {
            await client.sendMessage(session.gateway_session_id, prompt);
          } catch {
            // sendMessage fails if gateway session is completed — resume it
            await client.resume(session.gateway_session_id, prompt);
          }
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          await taskStore.updateStatus(task.id, "error", new Date());
          await taskStore.updateOutputPreview(task.id, `Error: ${msg}`.slice(0, 200));
        }
      }

      const updatedTask = await taskStore.getById(task.id);
      res.status(201).json({ task: updatedTask ?? task });
    }
  });

  return router;
}
