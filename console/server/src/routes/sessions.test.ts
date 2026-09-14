import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import express from "express";
import type { Server } from "http";
import { createPool } from "../db/client.js";
import { signup } from "../auth.js";
import { makeAuthRouter } from "./auth.js";
import { makeSessionsRouter } from "./sessions.js";
import { SessionStore } from "../session-store.js";
import { TaskStore } from "../task-store.js";
import { GatewayClient } from "../gateway-client.js";
import { requireAuth } from "../middleware/require-auth.js";
import type { Agent } from "../agent-registry.js";
import type { Group } from "../groups.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;
const SESSION_SECRET = "test-secret-that-is-at-least-32-chars!!";

function startMockGateway(): { server: Server; url: () => string } {
  const app = express();
  app.use(express.json());

  app.post("/sessions", (req, res) => {
    res.status(201).json({ id: `gw-${Date.now()}`, status: "running", prompt: req.body?.prompt });
  });

  app.post("/sessions/:id/messages", (_req, res) => {
    res.json({ accepted: true });
  });

  app.delete("/sessions/:id", (_req, res) => {
    res.status(204).send();
  });

  const server = app.listen(0, "127.0.0.1");
  return {
    server,
    url: () => `http://127.0.0.1:${(server.address() as { port: number }).port}`,
  };
}

describe.skipIf(!DB_URL)("Sessions routes", () => {
  let pool: pg.Pool;
  let appServer: Server;
  let gatewayServer: Server;
  let gatewayUrl: string;
  let appUrl: string;
  let sessionStore: SessionStore;
  let taskStore: TaskStore;
  const ownerEmail = `sess_route_owner_${Date.now()}@example.com`;
  const otherEmail = `sess_route_other_${Date.now()}@example.com`;
  let ownerCookie: string;
  let otherCookie: string;

  const testAgent: Agent = {
    id: "test-agent",
    name: "Test Agent",
    gateway_url: "placeholder",
    backend: "claude_code",
    access: { owners: [ownerEmail], shared_with: [], shared_with_groups: [] },
  };

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    sessionStore = new SessionStore(pool);
    taskStore = new TaskStore(pool);

    await signup(pool, { email: ownerEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: otherEmail, password: "testpass1234" }).catch(() => {});

    // Start mock gateway
    const mock = startMockGateway();
    gatewayServer = mock.server;
    await new Promise<void>((r) => gatewayServer.once("listening", r));
    gatewayUrl = mock.url();

    // Build the app
    const app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));

    const agent = { ...testAgent, gateway_url: gatewayUrl };

    app.use(
      "/api",
      requireAuth(SESSION_SECRET),
      makeSessionsRouter({
        store: sessionStore,
        taskStore,
        getAgents: () => [agent],
        getGroups: () => [] as Group[],
        makeClient: (url) => new GatewayClient(url),
      })
    );

    appServer = app.listen(0, "127.0.0.1");
    await new Promise<void>((r) => appServer.once("listening", r));
    appUrl = `http://127.0.0.1:${(appServer.address() as { port: number }).port}`;

    // Login both users
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

  it("POST /api/agents/:agentId/sessions creates session and task", async () => {
    const { status, body } = await req(
      "POST",
      "/api/agents/test-agent/sessions",
      { prompt: "hello world" },
      ownerCookie
    );

    expect(status).toBe(201);
    const b = body as { session: { id: number; gateway_session_id: string }; task: { status: string } };
    expect(b.session.id).toBeTypeOf("number");
    expect(b.session.gateway_session_id).toBeTruthy();
    expect(b.task.status).toBe("running");
  });

  it("POST /api/agents/:agentId/sessions returns 403 for unauthorized user", async () => {
    const { status } = await req(
      "POST",
      "/api/agents/test-agent/sessions",
      { prompt: "hello" },
      otherCookie
    );
    expect(status).toBe(403);
  });

  it("POST /api/agents/:agentId/sessions returns 404 for unknown agent", async () => {
    const { status } = await req(
      "POST",
      "/api/agents/nonexistent/sessions",
      { prompt: "hello" },
      ownerCookie
    );
    expect(status).toBe(404);
  });

  it("POST /api/agents/:agentId/sessions returns 401 without auth", async () => {
    const { status } = await req(
      "POST",
      "/api/agents/test-agent/sessions",
      { prompt: "hello" }
    );
    expect(status).toBe(401);
  });

  it("GET /api/sessions returns sessions", async () => {
    await sessionStore.create({
      gatewaySessionId: "gw-list-1",
      agentId: "test-agent",
      ownerEmail,
    });

    const { status, body } = await req("GET", "/api/sessions", undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { sessions: Array<{ gateway_session_id: string }> };
    expect(b.sessions).toHaveLength(1);
    expect(b.sessions[0].gateway_session_id).toBe("gw-list-1");
  });

  it("GET /api/sessions/:id returns session for owner", async () => {
    const session = await sessionStore.create({
      gatewaySessionId: "gw-get-1",
      agentId: "test-agent",
      ownerEmail,
    });

    const { status, body } = await req("GET", `/api/sessions/${session.id}`, undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { session: { id: number } };
    expect(b.session.id).toBe(session.id);
  });

  it("GET /api/sessions/:id returns 403 for non-viewer", async () => {
    const session = await sessionStore.create({
      gatewaySessionId: "gw-403-get",
      agentId: "test-agent",
      ownerEmail,
    });

    const { status } = await req("GET", `/api/sessions/${session.id}`, undefined, otherCookie);
    expect(status).toBe(403);
  });

  it("GET /api/sessions/:id returns 404 for missing session", async () => {
    const { status } = await req("GET", "/api/sessions/999999", undefined, ownerCookie);
    expect(status).toBe(404);
  });

  it("PATCH /api/sessions/:id updates title for owner", async () => {
    const session = await sessionStore.create({
      gatewaySessionId: "gw-patch-1",
      agentId: "test-agent",
      ownerEmail,
    });

    const { status, body } = await req(
      "PATCH",
      `/api/sessions/${session.id}`,
      { title: "Updated Title" },
      ownerCookie
    );

    expect(status).toBe(200);
    const b = body as { session: { title: string } };
    expect(b.session.title).toBe("Updated Title");
  });

  it("PATCH /api/sessions/:id returns 403 for non-owner", async () => {
    const session = await sessionStore.create({
      gatewaySessionId: "gw-patch-403",
      agentId: "test-agent",
      ownerEmail,
    });

    const { status } = await req(
      "PATCH",
      `/api/sessions/${session.id}`,
      { title: "Hacker" },
      otherCookie
    );

    expect(status).toBe(403);
  });

  it("DELETE /api/sessions/:id deletes session for owner", async () => {
    const session = await sessionStore.create({
      gatewaySessionId: "gw-del-1",
      agentId: "test-agent",
      ownerEmail,
    });

    const { status } = await req("DELETE", `/api/sessions/${session.id}`, undefined, ownerCookie);
    expect(status).toBe(204);

    const fetched = await sessionStore.getById(session.id);
    expect(fetched).toBeNull();
  });

  it("DELETE /api/sessions/:id returns 403 for non-owner", async () => {
    const session = await sessionStore.create({
      gatewaySessionId: "gw-del-403",
      agentId: "test-agent",
      ownerEmail,
    });

    const { status } = await req("DELETE", `/api/sessions/${session.id}`, undefined, otherCookie);
    expect(status).toBe(403);
  });

  it("DELETE /api/sessions/:id returns 404 for missing session", async () => {
    const { status } = await req("DELETE", "/api/sessions/999999", undefined, ownerCookie);
    expect(status).toBe(404);
  });
});
