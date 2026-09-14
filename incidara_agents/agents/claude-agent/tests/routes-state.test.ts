import { describe, it, expect } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { stateRouter } from "../src/routes/state.js";

function build() {
  const store = new SessionStore();
  const app = express();
  app.use(express.json());
  app.use("/sessions", stateRouter({ store }));
  return { app, store };
}

async function req(app: any, path: string) {
  const { createServer } = await import("node:http");
  return new Promise<{ status: number; body: any }>((resolve) => {
    const server = createServer(app);
    server.listen(0, async () => {
      const { port } = server.address() as any;
      const r = await fetch(`http://127.0.0.1:${port}${path}`);
      const text = await r.text();
      server.close();
      resolve({ status: r.status, body: text ? JSON.parse(text) : null });
    });
  });
}

describe("routes/state", () => {
  it("GET /sessions/:id/state returns compact state", async () => {
    const { app, store } = build();
    const s = store.createSession({ workspacePath: "/w" });
    s.claudeSessionId = "c1";
    s.currentTool = "Bash";
    s.lastMessagePreview = "running ls";
    store.setStatus(s.id, "busy");
    store.appendEvent(s.id, "message.delta", { text: "x" });
    const r = await req(app, `/sessions/${s.id}/state`);
    expect(r.status).toBe(200);
    expect(r.body).toMatchObject({
      session_id: s.id,
      status: "busy",
      current_tool: "Bash",
      last_message_preview: "running ls",
      waiting_for_input: false,
      claude_session_id: "c1",
    });
    expect(r.body.latest_seq).toBeGreaterThan(0);
  });

  it("waiting_for_input true when status is waiting_input", async () => {
    const { app, store } = build();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "waiting_input");
    const r = await req(app, `/sessions/${s.id}/state`);
    expect(r.body.waiting_for_input).toBe(true);
  });

  it("404 when session missing", async () => {
    const { app } = build();
    const r = await req(app, "/sessions/nope/state");
    expect(r.status).toBe(404);
  });
});
