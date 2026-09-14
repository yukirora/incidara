import { describe, it, expect, beforeEach } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { sessionsRouter } from "../src/routes/sessions.js";

function mockQuery(_evts: any[] = [{ type: "system", subtype: "init", session_id: "c1" }, { type: "result", subtype: "success" }]) {
  return async function* () { for (const e of _evts) yield e; };
}

function buildApp() {
  const store = new SessionStore();
  const bus = new EventBus();
  const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery()()) as any });
  const app = express();
  app.use(express.json());
  app.use("/sessions", sessionsRouter({ store, adapter }));
  return { app, store };
}

async function request(app: any, method: string, path: string, body?: any) {
  const { createServer } = await import("node:http");
  return new Promise<{ status: number; body: any }>((resolve) => {
    const server = createServer(app);
    server.listen(0, async () => {
      const { port } = server.address() as any;
      const res = await fetch(`http://127.0.0.1:${port}${path}`, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await res.text();
      server.close();
      resolve({ status: res.status, body: text ? JSON.parse(text) : null });
    });
  });
}

describe("routes/sessions", () => {
  it("POST /sessions creates a session", async () => {
    const { app } = buildApp();
    const r = await request(app, "POST", "/sessions", { prompt: "hello", workspace_path: "/w" });
    expect(r.status).toBe(201);
    expect(r.body.id).toMatch(/^sess_/);
    expect(r.body.status).toBe("starting");
  });

  it("POST /sessions rejects missing prompt", async () => {
    const { app } = buildApp();
    const r = await request(app, "POST", "/sessions", { workspace_path: "/w" });
    expect(r.status).toBe(400);
  });

  it("GET /sessions lists all sessions", async () => {
    const { app, store } = buildApp();
    store.createSession({ workspacePath: "/w" });
    store.createSession({ workspacePath: "/w" });
    const r = await request(app, "GET", "/sessions");
    expect(r.status).toBe(200);
    expect(r.body.sessions).toHaveLength(2);
  });

  it("GET /sessions/:id returns session", async () => {
    const { app, store } = buildApp();
    const s = store.createSession({ workspacePath: "/w", title: "t" });
    const r = await request(app, "GET", `/sessions/${s.id}`);
    expect(r.status).toBe(200);
    expect(r.body.id).toBe(s.id);
    expect(r.body.title).toBe("t");
  });

  it("GET /sessions/:id 404 when not found", async () => {
    const { app } = buildApp();
    const r = await request(app, "GET", "/sessions/nope");
    expect(r.status).toBe(404);
  });

  it("DELETE /sessions/:id removes the session", async () => {
    const { app, store } = buildApp();
    const s = store.createSession({ workspacePath: "/w" });
    const r = await request(app, "DELETE", `/sessions/${s.id}`);
    expect(r.status).toBe(204);
    expect(store.getSession(s.id)).toBeUndefined();
  });
});
