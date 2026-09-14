import { describe, it, expect } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { compatRouter, sessionIdForKey } from "../src/routes/compat.js";

function mockAdapter(store: SessionStore, bus: EventBus) {
  const adapter = new ClaudeAdapter(store, bus, { queryFn: (async function* () {}) as any });
  adapter.startSession = (s, _prompt) => {
    Promise.resolve().then(() => {
      bus.publish(store.appendEvent(s.id, "session.started", {}));
      bus.publish(store.appendEvent(s.id, "message.delta", { text: "hello" }));
      bus.publish(store.appendEvent(s.id, "message.agent", { content: "hello" }));
      store.setStatus(s.id, "completed");
      bus.publish(store.appendEvent(s.id, "session.completed", {}));
    });
  };
  return adapter;
}

function buildApp() {
  const store = new SessionStore();
  const bus = new EventBus();
  const adapter = mockAdapter(store, bus);
  const app = express();
  app.use(express.json());
  app.use("/v1", compatRouter({ store, bus, adapter }));
  return { app, store };
}

async function withServer<T>(app: any, fn: (port: number) => Promise<T>): Promise<T> {
  const { createServer } = await import("node:http");
  const server = createServer(app);
  await new Promise<void>((r) => server.listen(0, r));
  const { port } = server.address() as any;
  try { return await fn(port); } finally { server.close(); }
}

describe("routes/compat", () => {
  it("sessionIdForKey is deterministic", () => {
    expect(sessionIdForKey("abc")).toBe(sessionIdForKey("abc"));
    expect(sessionIdForKey("abc")).not.toBe(sessionIdForKey("xyz"));
    expect(sessionIdForKey("abc")).toMatch(/^sess_[0-9a-f]{8}$/);
  });

  it("POST /v1/chat/completions non-streaming returns OpenAI-format body", async () => {
    const { app } = buildApp();
    const body = await withServer(app, async (port) => {
      const r = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Key": "k1" },
        body: JSON.stringify({ messages: [{ role: "user", content: "hi" }], stream: false }),
      });
      return await r.json();
    });
    expect(body.object).toBe("chat.completion");
    expect(body.choices[0].message.role).toBe("assistant");
    expect(body.choices[0].message.content).toContain("hello");
    expect(body.session_key).toBe("k1");
  });

  it("POST /v1/chat/completions streaming returns SSE chunks", async () => {
    const { app } = buildApp();
    const text = await withServer(app, async (port) => {
      const r = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Key": "k2" },
        body: JSON.stringify({ messages: [{ role: "user", content: "hi" }], stream: true }),
      });
      return await r.text();
    });
    expect(text).toContain("chat.completion.chunk");
    expect(text).toContain("\"role\":\"assistant\"");
    expect(text).toContain("hello");
    expect(text).toContain("[DONE]");
  });

  it("rejects when no user message present", async () => {
    const { app } = buildApp();
    const status = await withServer(app, async (port) => {
      const r = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: [] }),
      });
      return r.status;
    });
    expect(status).toBe(400);
  });

  it("same X-Session-Key reuses the same session", async () => {
    const { app, store } = buildApp();
    await withServer(app, async (port) => {
      const call = () => fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Key": "stable-key" },
        body: JSON.stringify({ messages: [{ role: "user", content: "hi" }], stream: false }),
      }).then((r) => r.json());
      await call();
      await call();
    });
    const ids = store.listSessions().map((s) => s.id);
    expect(new Set(ids).size).toBe(1);
    expect(ids[0]).toBe(sessionIdForKey("stable-key"));
  });

  it("GET /v1/models returns claude-code", async () => {
    const { app } = buildApp();
    const body = await withServer(app, async (port) => {
      return (await fetch(`http://127.0.0.1:${port}/v1/models`)).json();
    });
    expect(body.data[0].id).toBe("claude-code");
  });
});
