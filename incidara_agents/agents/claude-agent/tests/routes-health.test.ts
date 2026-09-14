import { describe, it, expect } from "vitest";
import express from "express";
import { SessionStore } from "../src/session-store.js";
import { healthRouter } from "../src/routes/health.js";

async function get(app: any) {
  const { createServer } = await import("node:http");
  return new Promise<any>((resolve) => {
    const server = createServer(app);
    server.listen(0, async () => {
      const { port } = server.address() as any;
      const r = await fetch(`http://127.0.0.1:${port}/health`);
      const body = await r.json();
      server.close();
      resolve(body);
    });
  });
}

describe("routes/health", () => {
  it("returns status=ok and uptime", async () => {
    const store = new SessionStore();
    const app = express();
    app.use("/", healthRouter({ store }));
    const body = await get(app);
    expect(body.status).toBe("ok");
    expect(body.uptime_s).toBeGreaterThanOrEqual(0);
    expect(body.sessions_active).toBe(0);
    expect(body.sessions_total).toBe(0);
  });

  it("counts active vs terminal sessions", async () => {
    const store = new SessionStore();
    const s1 = store.createSession({ workspacePath: "/w" });
    const s2 = store.createSession({ workspacePath: "/w" });
    const s3 = store.createSession({ workspacePath: "/w" });
    store.setStatus(s1.id, "running");
    store.setStatus(s2.id, "completed");
    store.setStatus(s3.id, "error");
    const app = express();
    app.use("/", healthRouter({ store }));
    const body = await get(app);
    expect(body.sessions_total).toBe(3);
    expect(body.sessions_active).toBe(1);
  });
});
