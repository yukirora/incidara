import { describe, it, expect, beforeAll, afterAll } from "vitest";
import express from "express";
import type { Server } from "http";
import { GatewayClient } from "./gateway-client.js";

function startMockGateway(): { server: Server; url: () => string } {
  const app = express();
  app.use(express.json());

  // POST /sessions
  app.post("/sessions", (req, res) => {
    res.status(201).json({ id: "gw-sess-1", status: "running", prompt: req.body?.prompt });
  });

  // Error route - MUST come before generic route
  app.post("/sessions/error-session/messages", (_req, res) => {
    res.status(500).json({ error: "Internal server error" });
  });

  // POST /sessions/:id/messages
  app.post("/sessions/:id/messages", (req, res) => {
    res.json({ accepted: true });
  });

  // POST /sessions/:id/interrupt
  app.post("/sessions/:id/interrupt", (_req, res) => {
    res.json({ ok: true });
  });

  // POST /sessions/:id/resume
  app.post("/sessions/:id/resume", (req, res) => {
    res.json({ ok: true, content: req.body?.content });
  });

  // GET /sessions/:id/state
  app.get("/sessions/:id/state", (req, res) => {
    res.json({ id: req.params.id, status: "running" });
  });

  // GET /sessions/:id/events
  app.get("/sessions/:id/events", (req, res) => {
    const after = req.query.after_seq;
    res.json({ events: [], after_seq: after ?? null });
  });

  // DELETE /sessions/:id
  app.delete("/sessions/:id", (_req, res) => {
    res.status(204).send();
  });

  // GET /sessions/:id/events/stream
  app.get("/sessions/:id/events/stream", (_req, res) => {
    res.setHeader("Content-Type", "text/event-stream");
    res.write("data: {\"seq\":1,\"event_type\":\"ping\"}\n\n");
    res.end();
  });

  let port = 0;
  const server = app.listen(0, "127.0.0.1");
  return {
    server,
    url: () => `http://127.0.0.1:${(server.address() as { port: number }).port}`,
  };
}

describe("GatewayClient", () => {
  let server: Server;
  let client: GatewayClient;
  let baseUrl: string;

  beforeAll(async () => {
    const mock = startMockGateway();
    server = mock.server;
    await new Promise<void>((resolve) => server.once("listening", resolve));
    baseUrl = mock.url();
    client = new GatewayClient(baseUrl);
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it("createSession returns session object", async () => {
    const session = await client.createSession({ prompt: "hello", title: "Test" });
    expect(session.id).toBe("gw-sess-1");
    expect(session.status).toBe("running");
  });

  it("sendMessage returns accepted", async () => {
    const result = await client.sendMessage("gw-sess-1", "my message") as { accepted: boolean };
    expect(result.accepted).toBe(true);
  });

  it("interrupt returns ok", async () => {
    const result = await client.interrupt("gw-sess-1") as { ok: boolean };
    expect(result.ok).toBe(true);
  });

  it("resume returns ok", async () => {
    const result = await client.resume("gw-sess-1", "continue") as { ok: boolean };
    expect(result.ok).toBe(true);
  });

  it("getState returns session state", async () => {
    const state = await client.getState("gw-sess-1") as { id: string; status: string };
    expect(state.id).toBe("gw-sess-1");
    expect(state.status).toBe("running");
  });

  it("getEvents returns events array", async () => {
    const result = await client.getEvents("gw-sess-1", 5) as { events: unknown[] };
    expect(result.events).toEqual([]);
  });

  it("deleteSession returns undefined (204)", async () => {
    const result = await client.deleteSession("gw-sess-1");
    expect(result).toBeUndefined();
  });

  it("openEventStream returns Response with SSE content-type", async () => {
    const res = await client.openEventStream("gw-sess-1");
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    await res.body?.cancel();
  });

  it("throws on non-2xx response", async () => {
    // Use an error-triggering client
    const badClient = new GatewayClient(baseUrl);
    // Override the error-route — manually hit the error endpoint
    await expect(badClient.sendMessage("error-session", "test")).rejects.toThrow(/500/);
  });
});
