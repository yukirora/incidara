import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from "vitest";
import express from "express";
import type { Server } from "http";
import { createPool } from "../db/client.js";
import { signup } from "../auth.js";
import { makeAuthRouter } from "./auth.js";
import { makeProxyRouter } from "./proxy.js";
import { SessionStore } from "../session-store.js";
import { TaskStore } from "../task-store.js";
import { GatewayClient } from "../gateway-client.js";
import { requireAuth } from "../middleware/require-auth.js";
import type { Agent } from "../agent-registry.js";
import type { Group } from "../groups.js";
import type { SseTaskUpdaterDeps } from "../sse-task-updater.js";
import type { GatewayEvent } from "../sse-task-updater.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;
const SESSION_SECRET = "test-secret-that-is-at-least-32-chars!!";
let gwIdCounter = 0;

function startMockGateway(): { server: Server; url: () => string } {
  const app = express();
  app.use(express.json());

  app.get("/sessions/:id/state", (req, res) => {
    res.json({ id: req.params.id, status: "running" });
  });

  app.get("/sessions/:id/events", (req, res) => {
    res.json({ events: [] });
  });

  app.get("/sessions/:id/events/stream", (_req, res) => {
    res.setHeader("Content-Type", "text/event-stream");
    res.write(`data: {"seq":1,"session_id":"gw-s1","event_type":"message.agent","timestamp":"t","payload":{"content":"hello"}}\n\n`);
    res.end();
  });

  app.post("/sessions/:id/interrupt", (_req, res) => {
    res.json({ ok: true });
  });

  app.post("/sessions/:id/resume", (_req, res) => {
    res.json({ ok: true });
  });

  const server = app.listen(0, "127.0.0.1");
  return {
    server,
    url: () => `http://127.0.0.1:${(server.address() as { port: number }).port}`,
  };
}

describe.skipIf(!DB_URL)("Proxy routes", () => {
  let pool: pg.Pool;
  let appServer: Server;
  let gatewayServer: Server;
  let appUrl: string;
  let sessionStore: SessionStore;
  let taskStore: TaskStore;
  const ownerEmail = `proxy_owner_${Date.now()}@example.com`;
  const otherEmail = `proxy_other_${Date.now()}@example.com`;
  let ownerCookie: string;
  let otherCookie: string;
  let testSessionId: number;
  const applyEventSpy = vi.fn(async (_deps: SseTaskUpdaterDeps, _sessionId: number, _event: GatewayEvent) => {});

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    sessionStore = new SessionStore(pool);
    taskStore = new TaskStore(pool);

    await signup(pool, { email: ownerEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: otherEmail, password: "testpass1234" }).catch(() => {});

    const mock = startMockGateway();
    gatewayServer = mock.server;
    await new Promise<void>((r) => gatewayServer.once("listening", r));
    const gatewayUrl = mock.url();

    const testAgent: Agent = {
      id: "proxy-test-agent",
      name: "Proxy Test Agent",
      gateway_url: gatewayUrl,
      backend: "claude_code",
      access: { owners: [ownerEmail], shared_with: [], shared_with_groups: [] },
    };

    const app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));

    app.use(
      "/api/sessions",
      requireAuth(SESSION_SECRET),
      makeProxyRouter({
        taskStore,
        sessionStore,
        getAgents: () => [testAgent],
        getGroups: () => [] as Group[],
        makeClient: (url) => new GatewayClient(url),
        applyEventFn: applyEventSpy,
      })
    );

    appServer = app.listen(0, "127.0.0.1");
    await new Promise<void>((r) => appServer.once("listening", r));
    appUrl = `http://127.0.0.1:${(appServer.address() as { port: number }).port}`;

    ownerCookie = await loginAndGetCookie(appUrl, ownerEmail, "testpass1234");
    otherCookie = await loginAndGetCookie(appUrl, otherEmail, "testpass1234");
  });

  afterAll(async () => {
    appServer.close();
    gatewayServer.close();
    await pool.query("DELETE FROM tasks WHERE submitter_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM users WHERE email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.end();
  });

  beforeEach(async () => {
    await pool.query("DELETE FROM tasks WHERE submitter_email = ANY($1)", [[ownerEmail, otherEmail]]);
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]);
    applyEventSpy.mockClear();

    const session = await sessionStore.create({
      gatewaySessionId: `gw-proxy-${Date.now()}-${++gwIdCounter}`,
      agentId: "proxy-test-agent",
      ownerEmail,
    });
    testSessionId = session.id;
  });

  async function loginAndGetCookie(url: string, email: string, password: string): Promise<string> {
    const res = await fetch(`${url}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const cookies = res.headers.getSetCookie?.() ?? [];
    const sessionCookie = cookies.find((c: string) => c.includes("incidara_session")) ?? "";
    return sessionCookie.split(";")[0];
  }

  async function req(
    method: string,
    path: string,
    body?: unknown,
    cookie?: string
  ): Promise<{ status: number; body: unknown }> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (cookie) headers["Cookie"] = cookie;
    const res = await fetch(`${appUrl}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    const b = await res.json().catch(() => ({}));
    return { status: res.status, body: b };
  }

  it("GET /api/sessions/:id/state proxies to gateway for owner", async () => {
    const { status, body } = await req("GET", `/api/sessions/${testSessionId}/state`, undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { status: string };
    expect(b.status).toBe("running");
  });

  it("GET /api/sessions/:id/state returns 403 for non-viewer", async () => {
    const { status } = await req("GET", `/api/sessions/${testSessionId}/state`, undefined, otherCookie);
    expect(status).toBe(403);
  });

  it("GET /api/sessions/:id/events returns events list", async () => {
    const { status, body } = await req("GET", `/api/sessions/${testSessionId}/events`, undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { events: unknown[] };
    expect(b.events).toEqual([]);
  });

  it("GET /api/sessions/:id/events returns 403 for non-viewer", async () => {
    const { status } = await req("GET", `/api/sessions/${testSessionId}/events`, undefined, otherCookie);
    expect(status).toBe(403);
  });

  it("POST /api/sessions/:id/interrupt returns 403 for non-owner", async () => {
    const { status } = await req("POST", `/api/sessions/${testSessionId}/interrupt`, {}, otherCookie);
    expect(status).toBe(403);
  });

  it("POST /api/sessions/:id/interrupt works for owner", async () => {
    const { status, body } = await req("POST", `/api/sessions/${testSessionId}/interrupt`, {}, ownerCookie);
    expect(status).toBe(200);
    const b = body as { ok: boolean };
    expect(b.ok).toBe(true);
  });

  it("POST /api/sessions/:id/resume returns 403 for non-owner", async () => {
    const { status } = await req("POST", `/api/sessions/${testSessionId}/resume`, {}, otherCookie);
    expect(status).toBe(403);
  });

  it("POST /api/sessions/:id/resume works for owner", async () => {
    const { status, body } = await req("POST", `/api/sessions/${testSessionId}/resume`, {}, ownerCookie);
    expect(status).toBe(200);
    const b = body as { ok: boolean };
    expect(b.ok).toBe(true);
  });

  it("GET /api/sessions/:id/events/stream proxies bytes and triggers applyEventToTask", async () => {
    const res = await fetch(`${appUrl}/api/sessions/${testSessionId}/events/stream`, {
      headers: { Cookie: ownerCookie },
    });

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");

    // Read the stream
    const text = await res.text();
    expect(text).toContain("message.agent");

    // applyEventSpy should have been called
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(applyEventSpy).toHaveBeenCalledWith(
      expect.any(Object),
      testSessionId,
      expect.objectContaining({ event_type: "message.agent" })
    );
  });

  it("GET /api/sessions/:id/events/stream returns 403 for non-viewer", async () => {
    const res = await fetch(`${appUrl}/api/sessions/${testSessionId}/events/stream`, {
      headers: { Cookie: otherCookie },
    });
    expect(res.status).toBe(403);
    await res.body?.cancel();
  });
});
