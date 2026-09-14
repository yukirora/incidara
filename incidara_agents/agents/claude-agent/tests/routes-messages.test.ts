import { describe, it, expect } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { messagesRouter } from "../src/routes/messages.js";

function build() {
  const store = new SessionStore();
  const bus = new EventBus();
  const sent: string[] = [];
  const adapter = new ClaudeAdapter(store, bus, {
    queryFn: (async function* () {}) as any,
  });
  adapter.startSession = (s, prompt) => { sent.push(prompt); };
  adapter.interrupt = (s) => { s.abortController.abort(); };
  const app = express();
  app.use(express.json());
  app.use("/sessions", messagesRouter({ store, adapter }));
  return { app, store, adapter, sent };
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

describe("routes/messages", () => {
  it("POST /sessions/:id/messages sends follow-up when status is running", async () => {
    const { app, store, sent } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "running");
    const r = await req(app, "POST", `/sessions/${s.id}/messages`, { content: "more" });
    expect(r.status).toBe(202);
    expect(sent).toEqual(["more"]);
  });

  it("POST /sessions/:id/messages rejects when status is busy", async () => {
    const { app, store } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "busy");
    const r = await req(app, "POST", `/sessions/${s.id}/messages`, { content: "x" });
    expect(r.status).toBe(409);
  });

  it("POST /sessions/:id/interrupt aborts", async () => {
    const { app, store } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "busy");
    const r = await req(app, "POST", `/sessions/${s.id}/interrupt`);
    expect(r.status).toBe(200);
    expect(s.abortController.signal.aborted).toBe(true);
  });

  it("POST /sessions/:id/resume restarts interrupted session", async () => {
    const { app, store, sent } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "interrupted");
    const r = await req(app, "POST", `/sessions/${s.id}/resume`, { content: "continue" });
    expect(r.status).toBe(202);
    expect(sent).toEqual(["continue"]);
  });

  it("POST /sessions/:id/messages resets aborted signal for interrupted session", async () => {
    const { app, store, sent } = build();
    const s = store.createSession({ workspacePath: "/w" });
    // Simulate prior interrupted run with an aborted signal.
    s.abortController.abort();
    store.setStatus(s.id, "interrupted");

    const r = await req(app, "POST", `/sessions/${s.id}/messages`, { content: "resume via message" });
    expect(r.status).toBe(202);
    expect(sent).toEqual(["resume via message"]);
    expect(s.abortController.signal.aborted).toBe(false);
  });

  it("404 when session missing", async () => {
    const { app } = build();
    const r = await req(app, "POST", "/sessions/nope/messages", { content: "x" });
    expect(r.status).toBe(404);
  });
});
