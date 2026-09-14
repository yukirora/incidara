import { describe, it, expect } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { eventsRouter } from "../src/routes/events.js";

function build() {
  const store = new SessionStore();
  const bus = new EventBus();
  const app = express();
  app.use(express.json());
  app.use("/sessions", eventsRouter({ store, bus }));
  return { app, store, bus };
}

async function req(app: any, method: string, path: string) {
  const { createServer } = await import("node:http");
  return new Promise<{ status: number; body: any }>((resolve) => {
    const server = createServer(app);
    server.listen(0, async () => {
      const { port } = server.address() as any;
      const r = await fetch(`http://127.0.0.1:${port}${path}`, { method });
      const text = await r.text();
      server.close();
      resolve({ status: r.status, body: text ? JSON.parse(text) : null });
    });
  });
}

describe("routes/events", () => {
  it("GET /sessions/:id/events returns historical events", async () => {
    const { app, store } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.appendEvent(s.id, "message.delta", { text: "a" });
    store.appendEvent(s.id, "message.delta", { text: "b" });
    const r = await req(app, "GET", `/sessions/${s.id}/events`);
    expect(r.status).toBe(200);
    expect(r.body.events).toHaveLength(2);
    expect(r.body.next_after_seq).toBe(2);
  });

  it("GET /sessions/:id/events?after_seq=N filters", async () => {
    const { app, store } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.appendEvent(s.id, "message.delta", { text: "a" });
    store.appendEvent(s.id, "message.delta", { text: "b" });
    store.appendEvent(s.id, "message.delta", { text: "c" });
    const r = await req(app, "GET", `/sessions/${s.id}/events?after_seq=1`);
    expect(r.body.events).toHaveLength(2);
    expect(r.body.events[0].seq).toBe(2);
  });

  it("GET 404 when session missing", async () => {
    const { app } = build();
    const r = await req(app, "GET", "/sessions/nope/events");
    expect(r.status).toBe(404);
  });

  it("GET /sessions/:id/events/stream delivers SSE events", async () => {
    const { app, store, bus } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.appendEvent(s.id, "message.delta", { text: "initial" });

    const { createServer } = await import("node:http");
    const server = createServer(app);
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as any;

    const res = await fetch(`http://127.0.0.1:${port}/sessions/${s.id}/events/stream`);
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();

    const chunk1 = decoder.decode((await reader.read()).value);
    expect(chunk1).toContain("message.delta");
    expect(chunk1).toContain("initial");

    bus.publish(store.appendEvent(s.id, "message.delta", { text: "live" }));
    const chunk2 = decoder.decode((await reader.read()).value);
    expect(chunk2).toContain("live");

    reader.cancel();
    server.close();
  });
});
