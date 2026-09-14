import { describe, it, expect } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { eventsRouter } from "../src/routes/events.js";

describe("SSE reconnect", () => {
  it("Last-Event-ID header skips already-seen events", async () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const s = store.createSession({ workspacePath: "/w" });
    store.appendEvent(s.id, "message.delta", { text: "a" });
    store.appendEvent(s.id, "message.delta", { text: "b" });
    store.appendEvent(s.id, "message.delta", { text: "c" });

    const app = express();
    app.use(express.json());
    app.use("/sessions", eventsRouter({ store, bus }));

    const { createServer } = await import("node:http");
    const server = createServer(app);
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as any;

    const res = await fetch(`http://127.0.0.1:${port}/sessions/${s.id}/events/stream`, {
      headers: { "Last-Event-ID": "1" },
    });
    const reader = res.body!.getReader();
    const dec = new TextDecoder();
    const chunk = dec.decode((await reader.read()).value);
    expect(chunk).not.toContain("\"text\":\"a\"");
    expect(chunk).toContain("\"text\":\"b\"");
    reader.cancel();
    server.close();
  });
});
