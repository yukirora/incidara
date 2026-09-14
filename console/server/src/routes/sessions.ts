import { Router } from "express";
import { z } from "zod";
import type { SessionStore } from "../session-store.js";
import type { TaskStore } from "../task-store.js";
import type { GatewayClient } from "../gateway-client.js";
import type { Agent } from "../agent-registry.js";

const CreateSessionSchema = z.object({
  prompt: z.string().min(1),
  title: z.string().optional(),
  workspace_path: z.string().optional(),
  completion_mode: z.enum(['auto', 'manual']).optional(),
  parent_task_id: z.number().int().nullable().optional(),
});

const PatchSessionSchema = z.object({
  title: z.string().optional(),
  shared_with_groups: z.array(z.string()).optional(),
});

interface SessionsRouterDeps {
  store: SessionStore;
  taskStore: TaskStore;
  getAgents: () => Agent[];
  getEnrichedAgents: () => Promise<Agent[]>;
  makeClient: (gatewayUrl: string) => GatewayClient;
}

export function makeSessionsRouter(deps: SessionsRouterDeps): Router {
  const { store, taskStore, getAgents, getEnrichedAgents, makeClient } = deps;
  const router = Router();

  // POST /api/agents/:agentId/sessions
  router.post("/agents/:agentId/sessions", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const enrichedAgents = await getEnrichedAgents();
    const agent = enrichedAgents.find((a) => a.id === req.params.agentId);

    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    if (!await auth.canAccessAgent(agent)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    const parsed = CreateSessionSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const { prompt, title, workspace_path, completion_mode, parent_task_id } = parsed.data;

    // Create session at gateway
    const client = makeClient(agent.gateway_url);
    let gwSession: { id: string; status: string; [key: string]: unknown };
    try {
      gwSession = await client.createSession({ prompt, title, workspace_path });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(502).json({ error: `Gateway error: ${msg}` });
      return;
    }

    // Create session in DB
    const session = await store.create({
      gatewaySessionId: gwSession.id,
      agentId: agent.id,
      ownerEmail: auth.email,
      title: title ?? undefined,
    });

    // Create initial task row with status='running'
    const task = await taskStore.create({
      sessionId: session.id,
      prompt,
      submitterEmail: auth.email,
      status: "running",
      title,
      completionMode: completion_mode,
      parentTaskId: parent_task_id,
    });

    res.status(201).json({ session, task });
  });

  // GET /api/sessions
  router.get("/sessions", async (req, res): Promise<void> => {
    const auth = req.auth!;

    const scope = (req.query.scope as "mine" | "shared" | "all" | undefined) ?? "all";
    const agentId = req.query.agent_id as string | undefined;

    // Compute accessible agent IDs — agent access gates session visibility
    // Use enriched agents (YAML + DB group overrides) for accurate access check
    const enrichedAgents = await getEnrichedAgents();
    const agentAccessBools = await Promise.all(enrichedAgents.map(a => auth.canAccessAgent(a)));
    const accessibleAgentIds = enrichedAgents.filter((_, i) => agentAccessBools[i]).map(a => a.id);

    const sessions = await store.listVisible({
      email: auth.email,
      groups: auth.groups,
      isAdmin: auth.isAdmin,
      accessibleAgentIds,
      agentId,
      scope,
    });
    res.json({ sessions });
  });

  // GET /api/sessions/:id
  router.get("/sessions/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid session id" });
      return;
    }

    const session = await store.getById(id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }

    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!await auth.canViewSession(session, agent)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    res.json({ session });
  });

  // PATCH /api/sessions/:id
  router.patch("/sessions/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid session id" });
      return;
    }

    const session = await store.getById(id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }

    if (!auth.isSessionOwner(session)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    const parsed = PatchSessionSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const { title, shared_with_groups } = parsed.data;

    let updated = session;
    if (title !== undefined) {
      updated = (await store.updateTitle(id, title)) ?? updated;
    }
    if (shared_with_groups !== undefined) {
      updated = (await store.updateSharing(id, { shared_with_groups })) ?? updated;
    }

    res.json({ session: updated });
  });

  // DELETE /api/sessions/:id
  router.delete("/sessions/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const id = parseInt(req.params.id, 10);

    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid session id" });
      return;
    }

    const session = await store.getById(id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }

    if (!auth.isSessionOwner(session)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    // Best-effort gateway delete
    try {
      const agent = getAgents().find((a) => a.id === session.agent_id);
      if (agent) {
        const client = makeClient(agent.gateway_url);
        await client.deleteSession(session.gateway_session_id);
      }
    } catch {
      // Ignore gateway errors on delete
    }

    await store.delete(id);
    res.status(204).send();
  });

  return router;
}
