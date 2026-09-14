import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import express from "express";
import type { Server } from "http";
import { createPool } from "../db/client.js";
import { signup } from "../auth.js";
import { makeAuthRouter } from "./auth.js";
import { makeSchedulesRouter } from "./schedules.js";
import { ScheduleStore } from "../schedule-store.js";
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
  app.post("/sessions", (_req, res) => {
    res.status(201).json({ id: `gw-${Date.now()}`, status: "running" });
  });
  app.post("/sessions/:id/messages", (_req, res) => {
    res.json({ accepted: true });
  });
  const server = app.listen(0, "127.0.0.1");
  return {
    server,
    url: () => `http://127.0.0.1:${(server.address() as { port: number }).port}`,
  };
}

describe.skipIf(!DB_URL)("Schedules routes", () => {
  let pool: pg.Pool;
  let appServer: Server;
  let gatewayServer: Server;
  let gatewayUrl: string;
  let appUrl: string;
  let scheduleStore: ScheduleStore;
  let sessionStore: SessionStore;
  let taskStore: TaskStore;
  const ownerEmail = `sched_route_owner_${Date.now()}@example.com`;
  const otherEmail = `sched_route_other_${Date.now()}@example.com`;
  let ownerCookie: string;
  let otherCookie: string;

  const testAgent: Agent = {
    id: "sched-test-agent",
    name: "Schedule Test Agent",
    gateway_url: "placeholder",
    backend: "claude_code",
    access: { owners: [ownerEmail], shared_with: [], shared_with_groups: [] },
  };

  const groups: Group[] = [];

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    scheduleStore = new ScheduleStore(pool);
    sessionStore = new SessionStore(pool);
    taskStore = new TaskStore(pool);

    await signup(pool, { email: ownerEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: otherEmail, password: "testpass1234" }).catch(() => {});

    const mock = startMockGateway();
    gatewayServer = mock.server;
    await new Promise<void>((r) => gatewayServer.once("listening", r));
    gatewayUrl = mock.url();
    testAgent.gateway_url = gatewayUrl;

    const app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));
    const auth = requireAuth(SESSION_SECRET);
    app.use(
      "/api/schedules",
      auth,
      makeSchedulesRouter({
        scheduleStore,
        sessionStore,
        taskStore,
        getAgents: () => [testAgent],
        getGroups: () => groups,
        makeClient: (url) => new GatewayClient(url),
      })
    );

    appServer = app.listen(0, "127.0.0.1");
    await new Promise<void>((r) => appServer.once("listening", r));
    appUrl = `http://127.0.0.1:${(appServer.address() as { port: number }).port}`;

    // Login
    const loginOwner = await fetch(`${appUrl}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: ownerEmail, password: "testpass1234" }),
    });
    ownerCookie = loginOwner.headers.get("set-cookie") ?? "";

    const loginOther = await fetch(`${appUrl}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: otherEmail, password: "testpass1234" }),
    });
    otherCookie = loginOther.headers.get("set-cookie") ?? "";
  });

  afterAll(async () => {
    await pool.query("DELETE FROM tasks WHERE submitter_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM schedules WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM users WHERE email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    appServer.close();
    gatewayServer.close();
    await pool.end();
  });

  beforeEach(async () => {
    await pool.query("DELETE FROM tasks WHERE submitter_email = ANY($1)", [[ownerEmail, otherEmail]]);
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]);
    await pool.query("DELETE FROM schedules WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]);
  });

  async function post(path: string, body: unknown, cookie = ownerCookie) {
    return fetch(`${appUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: cookie },
      body: JSON.stringify(body),
    });
  }

  async function get(path: string, cookie = ownerCookie) {
    return fetch(`${appUrl}${path}`, { headers: { Cookie: cookie } });
  }

  async function patch(path: string, body: unknown, cookie = ownerCookie) {
    return fetch(`${appUrl}${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Cookie: cookie },
      body: JSON.stringify(body),
    });
  }

  async function del(path: string, cookie = ownerCookie) {
    return fetch(`${appUrl}${path}`, { method: "DELETE", headers: { Cookie: cookie } });
  }

  // ── POST /api/schedules ────────────────────────────────────────────────────

  it("creates an interval schedule", async () => {
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "run me",
      trigger_type: "interval",
      interval_seconds: 3600,
      session_mode: "new",
    });

    expect(res.status).toBe(201);
    const body = await res.json() as { schedule: { id: number; trigger_type: string } };
    expect(body.schedule.trigger_type).toBe("interval");
    expect(body.schedule.id).toBeTypeOf("number");
  });

  it("creates a once schedule with future run_at", async () => {
    const runAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "once",
      trigger_type: "once",
      run_at: runAt,
      session_mode: "new",
    });

    expect(res.status).toBe(201);
  });

  it("creates a cron schedule", async () => {
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "every hour",
      trigger_type: "cron",
      cron_expr: "0 * * * *",
      timezone: "UTC",
      session_mode: "new",
    });

    expect(res.status).toBe(201);
  });

  it("400 when trigger_type=once but run_at missing", async () => {
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "p",
      trigger_type: "once",
      session_mode: "new",
    });
    expect(res.status).toBe(400);
  });

  it("400 when trigger_type=interval but interval_seconds missing", async () => {
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "p",
      trigger_type: "interval",
      session_mode: "new",
    });
    expect(res.status).toBe(400);
  });

  it("400 when interval_seconds < 30", async () => {
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "p",
      trigger_type: "interval",
      interval_seconds: 10,
      session_mode: "new",
    });
    expect(res.status).toBe(400);
  });

  it("400 when cron_expr is invalid", async () => {
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "p",
      trigger_type: "cron",
      cron_expr: "not-valid-cron",
      timezone: "UTC",
      session_mode: "new",
    });
    expect(res.status).toBe(400);
  });

  it("400 when once run_at is in the past", async () => {
    const runAt = new Date(Date.now() - 60_000).toISOString();
    const res = await post("/api/schedules", {
      agent_id: "sched-test-agent",
      prompt: "p",
      trigger_type: "once",
      run_at: runAt,
      session_mode: "new",
    });
    expect(res.status).toBe(400);
  });

  it("403 when user has no access to agent", async () => {
    const res = await post(
      "/api/schedules",
      {
        agent_id: "sched-test-agent",
        prompt: "p",
        trigger_type: "interval",
        interval_seconds: 60,
        session_mode: "new",
      },
      otherCookie
    );
    expect(res.status).toBe(403);
  });

  it("404 when agent not found", async () => {
    const res = await post("/api/schedules", {
      agent_id: "unknown-agent",
      prompt: "p",
      trigger_type: "interval",
      interval_seconds: 60,
      session_mode: "new",
    });
    expect(res.status).toBe(404);
  });

  // ── GET /api/schedules ─────────────────────────────────────────────────────

  it("lists only owner's schedules", async () => {
    // Create two owner schedules
    await post("/api/schedules", { agent_id: "sched-test-agent", prompt: "a", trigger_type: "interval", interval_seconds: 60, session_mode: "new" });
    await post("/api/schedules", { agent_id: "sched-test-agent", prompt: "b", trigger_type: "interval", interval_seconds: 60, session_mode: "new" });

    const res = await get("/api/schedules");
    expect(res.status).toBe(200);
    const body = await res.json() as { schedules: { owner_email: string }[] };
    expect(body.schedules.length).toBe(2);
    expect(body.schedules.every((s) => s.owner_email === ownerEmail)).toBe(true);
  });

  it("GET /api/schedules/:id returns schedule for owner", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "get me", trigger_type: "interval", interval_seconds: 60, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await get(`/api/schedules/${created.schedule.id}`);
    expect(res.status).toBe(200);
    const body = await res.json() as { schedule: { prompt: string } };
    expect(body.schedule.prompt).toBe("get me");
  });

  it("GET /api/schedules/:id returns 403 for non-owner", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "secret", trigger_type: "interval", interval_seconds: 60, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await get(`/api/schedules/${created.schedule.id}`, otherCookie);
    expect(res.status).toBe(403);
  });

  it("GET /api/schedules/:id returns 404 for unknown id", async () => {
    const res = await get("/api/schedules/999999999");
    expect(res.status).toBe(404);
  });

  // ── PATCH /api/schedules/:id ───────────────────────────────────────────────

  it("PATCH updates prompt and returns schedule", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "old", trigger_type: "interval", interval_seconds: 60, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await patch(`/api/schedules/${created.schedule.id}`, { prompt: "new" });
    expect(res.status).toBe(200);
    const body = await res.json() as { schedule: { prompt: string } };
    expect(body.schedule.prompt).toBe("new");
  });

  it("PATCH 403 for non-owner", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "p", trigger_type: "interval", interval_seconds: 60, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await patch(`/api/schedules/${created.schedule.id}`, { prompt: "hacked" }, otherCookie);
    expect(res.status).toBe(403);
  });

  it("PATCH 400 on invalid cron expr", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "p", trigger_type: "cron", cron_expr: "0 * * * *", timezone: "UTC", session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await patch(`/api/schedules/${created.schedule.id}`, { cron_expr: "bad-cron", trigger_type: "cron" });
    expect(res.status).toBe(400);
  });

  // ── DELETE /api/schedules/:id ──────────────────────────────────────────────

  it("DELETE removes schedule", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "del", trigger_type: "interval", interval_seconds: 60, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const delRes = await del(`/api/schedules/${created.schedule.id}`);
    expect(delRes.status).toBe(204);

    const getRes = await get(`/api/schedules/${created.schedule.id}`);
    expect(getRes.status).toBe(404);
  });

  it("DELETE 403 for non-owner", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "del", trigger_type: "interval", interval_seconds: 60, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await del(`/api/schedules/${created.schedule.id}`, otherCookie);
    expect(res.status).toBe(403);
  });

  // ── POST /api/schedules/:id/run-now ───────────────────────────────────────

  it("run-now fires the schedule immediately", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "run now test", trigger_type: "interval", interval_seconds: 3600, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await post(`/api/schedules/${created.schedule.id}/run-now`, {});
    expect(res.status).toBe(200);
    const body = await res.json() as { ok: boolean };
    expect(body.ok).toBe(true);
  });

  it("run-now 403 for non-owner", async () => {
    const created = await (await post("/api/schedules", {
      agent_id: "sched-test-agent", prompt: "p", trigger_type: "interval", interval_seconds: 3600, session_mode: "new",
    })).json() as { schedule: { id: number } };

    const res = await post(`/api/schedules/${created.schedule.id}/run-now`, {}, otherCookie);
    expect(res.status).toBe(403);
  });

  it("run-now 404 for unknown schedule", async () => {
    const res = await post("/api/schedules/999999999/run-now", {});
    expect(res.status).toBe(404);
  });
});
