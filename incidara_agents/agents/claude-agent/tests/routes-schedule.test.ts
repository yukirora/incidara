import { describe, it, expect } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { scheduleRouter, scheduleTick, type Schedule } from "../src/routes/schedule.js";

function build() {
  const store = new SessionStore();
  const bus = new EventBus();
  const adapter = new ClaudeAdapter(store, bus, { queryFn: (async function* () {}) as any });
  const started: string[] = [];
  adapter.startSession = (s, prompt) => { started.push(`${s.id}:${prompt}`); };
  const app = express();
  app.use(express.json());
  app.use("/v1/schedule", scheduleRouter({ store, adapter }));
  return { app, store, adapter, started };
}

async function req(app: any, method: string, path: string, body?: any) {
  const { createServer } = await import("node:http");
  return new Promise<{ status: number; body: any }>((resolve) => {
    const server = createServer(app);
    server.listen(0, async () => {
      const { port } = server.address() as any;
      const r = await fetch(`http://127.0.0.1:${port}${path}`, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await r.text();
      server.close();
      resolve({ status: r.status, body: text ? JSON.parse(text) : null });
    });
  });
}

describe("routes/schedule", () => {
  it("POST creates a one-shot schedule", async () => {
    const { app } = build();
    const r = await req(app, "POST", "/v1/schedule", { prompt: "hi", delay: 60 });
    expect(r.status).toBe(200);
    expect(r.body.id).toMatch(/^sched-/);
    expect(r.body.type).toBe("once");
  });

  it("POST creates a recurring schedule", async () => {
    const { app } = build();
    const r = await req(app, "POST", "/v1/schedule", { prompt: "hi", interval: 120 });
    expect(r.body.type).toBe("recurring");
  });

  it("POST rejects missing prompt", async () => {
    const { app } = build();
    const r = await req(app, "POST", "/v1/schedule", { delay: 60 });
    expect(r.status).toBe(400);
  });

  it("POST rejects missing delay and interval", async () => {
    const { app } = build();
    const r = await req(app, "POST", "/v1/schedule", { prompt: "hi" });
    expect(r.status).toBe(400);
  });

  it("GET lists schedules", async () => {
    const { app } = build();
    await req(app, "POST", "/v1/schedule", { prompt: "hi", delay: 60 });
    const r = await req(app, "GET", "/v1/schedule");
    expect(r.body.schedules).toHaveLength(1);
  });

  it("DELETE removes schedule; 404 when missing", async () => {
    const { app } = build();
    const created = await req(app, "POST", "/v1/schedule", { prompt: "hi", delay: 60 });
    const del = await req(app, "DELETE", `/v1/schedule/${created.body.id}`);
    expect(del.status).toBe(200);
    expect(del.body.deleted).toBe(created.body.id);
    const missing = await req(app, "DELETE", "/v1/schedule/nope");
    expect(missing.status).toBe(404);
  });

  it("scheduleTick runs due once-schedules and removes them", () => {
    const { store, adapter, started } = build();
    const schedules = new Map<string, Schedule>();
    schedules.set("s1", {
      prompt: "fire", sessionKey: null, type: "once",
      interval: 60_000, nextRun: 100, createdAt: 0,
      lastRun: null, lastResult: null, running: false,
    });
    scheduleTick(schedules, store, adapter, 200);
    expect(started).toHaveLength(1);
    expect(started[0]).toContain(":fire");
    expect(schedules.has("s1")).toBe(false);
  });

  it("scheduleTick requeues recurring schedules", () => {
    const { store, adapter, started } = build();
    const schedules = new Map<string, Schedule>();
    schedules.set("s1", {
      prompt: "tick", sessionKey: null, type: "recurring",
      interval: 60_000, nextRun: 100, createdAt: 0,
      lastRun: null, lastResult: null, running: false,
    });
    scheduleTick(schedules, store, adapter, 200);
    expect(started).toHaveLength(1);
    expect(schedules.get("s1")!.nextRun).toBe(200 + 60_000);
  });

  it("scheduleTick skips not-yet-due schedules", () => {
    const { store, adapter, started } = build();
    const schedules = new Map<string, Schedule>();
    schedules.set("s1", {
      prompt: "future", sessionKey: null, type: "once",
      interval: 60_000, nextRun: 10_000, createdAt: 0,
      lastRun: null, lastResult: null, running: false,
    });
    scheduleTick(schedules, store, adapter, 500);
    expect(started).toHaveLength(0);
    expect(schedules.has("s1")).toBe(true);
  });
});
