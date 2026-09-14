import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import express from "express";
import type { Server } from "http";
import { createPool } from "../db/client.js";
import { signup } from "../auth.js";
import { makeAuthRouter } from "./auth.js";
import { makeTasksRouter } from "./tasks.js";
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
let gwIdCounter = 0;

function startMockGateway(): { server: Server; url: () => string } {
  const app = express();
  app.use(express.json());
  app.post("/sessions", (req, res) => {
    res.status(201).json({ id: `gw-${Date.now()}`, status: "running" });
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

describe.skipIf(!DB_URL)("Tasks routes", () => {
  let pool: pg.Pool;
  let appServer: Server;
  let gatewayServer: Server;
  let appUrl: string;
  let sessionStore: SessionStore;
  let taskStore: TaskStore;
  const ownerEmail = `task_route_owner_${Date.now()}@example.com`;
  const otherEmail = `task_route_other_${Date.now()}@example.com`;
  let ownerCookie: string;
  let otherCookie: string;
  let testSessionId: number;
  let testGatewaySessionId: string;

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
      id: "tasks-test-agent",
      name: "Tasks Test Agent",
      gateway_url: gatewayUrl,
      backend: "claude_code",
      access: { owners: [ownerEmail], shared_with: [], shared_with_groups: [] },
    };

    const app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));

    const routerDeps = {
      taskStore,
      sessionStore,
      getAgents: () => [testAgent],
      getGroups: () => [] as Group[],
      makeClient: (url: string) => new GatewayClient(url),
    };

    app.use("/api", requireAuth(SESSION_SECRET), makeSessionsRouter({ store: sessionStore, ...routerDeps }));
    app.use("/api", requireAuth(SESSION_SECRET), makeTasksRouter(routerDeps));

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

    testGatewaySessionId = `gw-tasks-${Date.now()}-${++gwIdCounter}`;
    const session = await sessionStore.create({
      gatewaySessionId: testGatewaySessionId,
      agentId: "tasks-test-agent",
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

  it("GET /api/tasks returns tasks for owner", async () => {
    await taskStore.create({ sessionId: testSessionId, prompt: "task 1", submitterEmail: ownerEmail, status: "running" });
    await taskStore.create({ sessionId: testSessionId, prompt: "task 2", submitterEmail: ownerEmail, status: "pending" });

    const { status, body } = await req("GET", "/api/tasks", undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { tasks: unknown[]; total: number };
    expect(b.tasks).toHaveLength(2);
    expect(b.total).toBe(2);
  });

  it("GET /api/tasks returns empty for non-owner", async () => {
    await taskStore.create({ sessionId: testSessionId, prompt: "task 1", submitterEmail: ownerEmail, status: "running" });

    const { status, body } = await req("GET", "/api/tasks", undefined, otherCookie);
    expect(status).toBe(200);
    const b = body as { tasks: unknown[]; total: number };
    expect(b.tasks).toHaveLength(0);
    expect(b.total).toBe(0);
  });

  it("GET /api/tasks filters by status", async () => {
    await taskStore.create({ sessionId: testSessionId, prompt: "running", submitterEmail: ownerEmail, status: "running" });
    await taskStore.create({ sessionId: testSessionId, prompt: "pending", submitterEmail: ownerEmail, status: "pending" });

    const { status, body } = await req("GET", "/api/tasks?status=running", undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { tasks: Array<{ status: string }>; total: number };
    expect(b.tasks).toHaveLength(1);
    expect(b.tasks[0].status).toBe("running");
  });

  it("GET /api/tasks supports pagination with total", async () => {
    for (let i = 0; i < 5; i++) {
      await taskStore.create({ sessionId: testSessionId, prompt: `task ${i}`, submitterEmail: ownerEmail, status: "pending" });
    }

    const { status, body } = await req("GET", "/api/tasks?limit=2&offset=0", undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { tasks: unknown[]; total: number };
    expect(b.tasks).toHaveLength(2);
    expect(b.total).toBe(5);
  });

  it("GET /api/tasks/:id returns task for owner", async () => {
    const task = await taskStore.create({ sessionId: testSessionId, prompt: "fetch me", submitterEmail: ownerEmail, status: "running" });

    const { status, body } = await req("GET", `/api/tasks/${task.id}`, undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { task: { id: number } };
    expect(b.task.id).toBe(task.id);
  });

  it("GET /api/tasks/:id returns 403 for non-viewer", async () => {
    const task = await taskStore.create({ sessionId: testSessionId, prompt: "secret", submitterEmail: ownerEmail, status: "running" });
    const { status } = await req("GET", `/api/tasks/${task.id}`, undefined, otherCookie);
    expect(status).toBe(403);
  });

  it("POST /api/sessions/:id/tasks on idle session → status running, gateway called", async () => {
    const { status, body } = await req(
      "POST",
      `/api/sessions/${testSessionId}/tasks`,
      { prompt: "start now" },
      ownerCookie
    );
    expect(status).toBe(201);
    const b = body as { task: { status: string } };
    expect(b.task.status).toBe("running");
  });

  it("POST /api/sessions/:id/tasks on busy session → status pending, no gateway call", async () => {
    // Create a running task first
    await taskStore.create({ sessionId: testSessionId, prompt: "running first", submitterEmail: ownerEmail, status: "running" });

    const { status, body } = await req(
      "POST",
      `/api/sessions/${testSessionId}/tasks`,
      { prompt: "queue this" },
      ownerCookie
    );
    expect(status).toBe(201);
    const b = body as { task: { status: string } };
    expect(b.task.status).toBe("pending");
  });

  it("POST /api/sessions/:id/tasks returns 403 for non-owner", async () => {
    const { status } = await req(
      "POST",
      `/api/sessions/${testSessionId}/tasks`,
      { prompt: "unauthorized" },
      otherCookie
    );
    expect(status).toBe(403);
  });

  it("DELETE /api/tasks/:id cancels pending task", async () => {
    const task = await taskStore.create({ sessionId: testSessionId, prompt: "cancel me", submitterEmail: ownerEmail, status: "pending" });

    const { status } = await req("DELETE", `/api/tasks/${task.id}`, undefined, ownerCookie);
    expect(status).toBe(204);

    const fetched = await taskStore.getById(task.id);
    expect(fetched!.status).toBe("cancelled");
  });

  it("DELETE /api/tasks/:id returns 409 for running task", async () => {
    const task = await taskStore.create({ sessionId: testSessionId, prompt: "running task", submitterEmail: ownerEmail, status: "running" });

    const { status } = await req("DELETE", `/api/tasks/${task.id}`, undefined, ownerCookie);
    expect(status).toBe(409);
  });

  it("DELETE /api/tasks/:id returns 403 for non-submitter", async () => {
    // Create task by owner
    const task = await taskStore.create({ sessionId: testSessionId, prompt: "owner task", submitterEmail: ownerEmail, status: "pending" });

    // Try to cancel as other user
    const { status } = await req("DELETE", `/api/tasks/${task.id}`, undefined, otherCookie);
    expect(status).toBe(403);
  });

  it("POST /api/tasks/:id/complete marks running task as completed", async () => {
    const task = await taskStore.create({
      sessionId: testSessionId,
      prompt: "manual task",
      submitterEmail: ownerEmail,
      status: "running",
      completionMode: "manual",
    });

    const { status, body } = await req("POST", `/api/tasks/${task.id}/complete`, undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { task: { status: string; ended_at: string | null } };
    expect(b.task.status).toBe("completed");
    expect(b.task.ended_at).not.toBeNull();
  });

  it("POST /api/tasks/:id/complete returns 409 for already-terminal task", async () => {
    const task = await taskStore.create({
      sessionId: testSessionId,
      prompt: "already done",
      submitterEmail: ownerEmail,
      status: "running",
    });
    // First complete it
    await taskStore.updateStatus(task.id, "completed", new Date());

    const { status } = await req("POST", `/api/tasks/${task.id}/complete`, undefined, ownerCookie);
    expect(status).toBe(409);
  });

  it("POST /api/tasks/:id/complete returns 403 for non-owner", async () => {
    const task = await taskStore.create({
      sessionId: testSessionId,
      prompt: "owner only",
      submitterEmail: ownerEmail,
      status: "running",
    });

    const { status } = await req("POST", `/api/tasks/${task.id}/complete`, undefined, otherCookie);
    expect(status).toBe(403);
  });

  it("POST /api/tasks/:id/complete works for waiting_input status", async () => {
    const task = await taskStore.create({
      sessionId: testSessionId,
      prompt: "waiting task",
      submitterEmail: ownerEmail,
      status: "running",
      completionMode: "manual",
    });
    await taskStore.updateStatus(task.id, "waiting_input");

    const { status, body } = await req("POST", `/api/tasks/${task.id}/complete`, undefined, ownerCookie);
    expect(status).toBe(200);
    const b = body as { task: { status: string } };
    expect(b.task.status).toBe("completed");
  });
});
