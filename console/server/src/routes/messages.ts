import { Router } from "express";
import { z } from "zod";
import type pg from "pg";
import type { TaskStore } from "../task-store.js";
import type { SessionStore } from "../session-store.js";
import type { GatewayClient } from "../gateway-client.js";
import type { Agent } from "../agent-registry.js";

const SendMessageSchema = z.object({
  content: z.string().min(1),
});

interface MessagesRouterDeps {
  pool: pg.Pool;
  taskStore: TaskStore;
  sessionStore: SessionStore;
  getAgents: () => Agent[];
  makeClient: (gatewayUrl: string) => GatewayClient;
}

export function makeMessagesRouter(deps: MessagesRouterDeps): Router {
  const { pool, taskStore, sessionStore, getAgents, makeClient } = deps;
  const router = Router();

  // POST /sessions/:id/messages
  router.post("/sessions/:id/messages", async (req, res): Promise<void> => {
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

    const parsed = SendMessageSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const { content } = parsed.data;

    // Ensure session can continue even after long-idle/redeploy:
    // if no active task exists, reopen the latest task in this session.
    let activeTask = await taskStore.latestActiveForSession(sessionId);
    if (!activeTask) {
      activeTask = await taskStore.reopenLatestForSession(sessionId);
      if (!activeTask) {
        res.status(409).json({ error: "No task history to resume" });
        return;
      }
    }

    // Proxy to gateway
    const agent = getAgents().find((a) => a.id === session.agent_id);
    if (!agent) {
      res.status(502).json({ error: "Agent not found" });
      return;
    }

    // Get current event count before sending, so we can find the new message.user event
    let beforeSeq: number | undefined;
    try {
      const client = makeClient(agent.gateway_url);
      const state = await client.getState(session.gateway_session_id) as { last_seq?: number };
      beforeSeq = typeof state?.last_seq === "number" ? state.last_seq : undefined;
    } catch { /* best effort */ }

    try {
      const client = makeClient(agent.gateway_url);
      try {
        await client.sendMessage(session.gateway_session_id, content);
      } catch {
        // For recovered/redeployed sessions, sendMessage can fail while
        // resume still succeeds for the same gateway session id.
        await client.resume(session.gateway_session_id, content);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(502).json({ error: `Gateway error: ${msg}` });
      return;
    }

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

    res.json({ accepted: true });
  });

  return router;
}
