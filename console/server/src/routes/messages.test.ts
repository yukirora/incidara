import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import express from "express";
import type { Server } from "http";
import { createPool } from "../db/client.js";
import { signup } from "../auth.js";
import { makeAuthRouter } from "./auth.js";
import { makeMessagesRouter } from "./messages.js";
import { SessionStore } from "../session-store.js";
import { TaskStore } from "../task-store.js";
import { GatewayClient } from "../gateway-client.js";
import { requireAuth } from "../middleware/require-auth.js";
import type { Agent } from "../agent-registry.js";
import type { Group } from "../groups.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;
const SESSION_SECRET = "test-secret-that-is-at-least-32-chars!!";
let gwIdCounter = 0;

function startMockGateway(): { server: Server; url: () => string } {
  const app = express();
  app.use(express.json());
  app.post("/sessions/:id/messages", (_req, res) => {
    res.json({ accepted: true });
  });
  app.post("/sessions/:id/resume", (_req, res) => {
    res.json({ accepted: true, resumed: true });
  });
  const server = app.listen(0, "127.0.0.1");
  return {
    server,
    url: () => `http://127.0.0.1:${(server.address() as { port: number }).port}`,
  };
}

describe.skipIf(!DB_URL)("Messages routes", () => {
  let pool: pg.Pool;
  let appServer: Server;
  let gatewayServer: Server;
  let appUrl: string;
  let sessionStore: SessionStore;
  let taskStore: TaskStore;
  const ownerEmail = `msg_route_owner_${Date.now()}@example.com`;
  const otherEmail = `msg_route_other_${Date.now()}@example.com`;
  let ownerCookie: string;
  let otherCookie: string;
  let testSessionId: number;

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
      id: "msg-test-agent",
      name: "Messages Test Agent",
      gateway_url: gatewayUrl,
      backend: "claude_code",
      access: { owners: [ownerEmail], shared_with: [], shared_with_groups: [] },
    };

    const app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));

    app.use(
      "/api",
      requireAuth(SESSION_SECRET),
      makeMessagesRouter({
        taskStore,
        sessionStore,
        getAgents: () => [testAgent],
        getGroups: () => [] as Group[],
        makeClient: (url) => new GatewayClient(url),
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

    const session = await sessionStore.create({
      gatewaySessionId: `gw-msg-${Date.now()}-${++gwIdCounter}`,
      agentId: "msg-test-agent",
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

  it("POST /api/sessions/:id/messages sends guidance to active task", async () => {
    await taskStore.create({ sessionId: testSessionId, prompt: "initial", submitterEmail: ownerEmail, status: "running" });

    const { status, body } = await req(
      "POST",
      `/api/sessions/${testSessionId}/messages`,
      { content: "follow up guidance" },
      ownerCookie
    );

    expect(status).toBe(200);
    const b = body as { accepted: boolean };
    expect(b.accepted).toBe(true);
  });

  it("POST /api/sessions/:id/messages returns 409 when no task history exists", async () => {
    // No tasks - session is idle
    const { status, body } = await req(
      "POST",
      `/api/sessions/${testSessionId}/messages`,
      { content: "no task active" },
      ownerCookie
    );

    expect(status).toBe(409);
    const b = body as { error: string };
    expect(b.error).toContain("No task history to resume");
  });

  it("POST /api/sessions/:id/messages resumes when latest task is completed", async () => {
    const task = await taskStore.create({ sessionId: testSessionId, prompt: "done", submitterEmail: ownerEmail, status: "running" });
    await taskStore.updateStatus(task.id, "completed", new Date());

    const { status } = await req(
      "POST",
      `/api/sessions/${testSessionId}/messages`,
      { content: "too late" },
      ownerCookie
    );

    expect(status).toBe(200);
  });

  it("POST /api/sessions/:id/messages returns 403 for non-owner", async () => {
    await taskStore.create({ sessionId: testSessionId, prompt: "initial", submitterEmail: ownerEmail, status: "running" });

    const { status } = await req(
      "POST",
      `/api/sessions/${testSessionId}/messages`,
      { content: "spy message" },
      otherCookie
    );

    expect(status).toBe(403);
  });

  it("POST /api/sessions/:id/messages does not create a task row", async () => {
    await taskStore.create({ sessionId: testSessionId, prompt: "initial", submitterEmail: ownerEmail, status: "running" });

    const countBefore = (await pool.query("SELECT COUNT(*) FROM tasks WHERE session_id = $1", [testSessionId])).rows[0].count;

    await req(
      "POST",
      `/api/sessions/${testSessionId}/messages`,
      { content: "guidance" },
      ownerCookie
    );

    const countAfter = (await pool.query("SELECT COUNT(*) FROM tasks WHERE session_id = $1", [testSessionId])).rows[0].count;
    expect(countAfter).toBe(countBefore);
  });
});
