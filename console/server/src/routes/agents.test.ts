import { describe, it, expect, beforeAll, afterAll } from "vitest";
import express from "express";
import { createPool } from "../db/client.js";
import { makeAuthRouter } from "./auth.js";
import { makeAgentsRouter } from "./agents.js";
import type { Agent } from "../agent-registry.js";
import type { Group } from "../groups.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;
const SESSION_SECRET = "test-secret-that-is-at-least-32-chars!!";

const testAgents: Agent[] = [
  {
    id: "agent-owner",
    name: "Owner Agent",
    gateway_url: "http://127.0.0.1:8001",
    backend: "claude_code",
    access: {
      owners: ["owner@example.com"],
      shared_with: [],
      shared_with_groups: [],
    },
  },
  {
    id: "agent-shared",
    name: "Shared Agent",
    gateway_url: "http://127.0.0.1:8002",
    backend: "claude_code",
    access: {
      owners: ["admin@example.com"],
      shared_with: ["owner@example.com"],
      shared_with_groups: [],
    },
  },
  {
    id: "agent-group",
    name: "Group Agent",
    gateway_url: "http://127.0.0.1:8003",
    backend: "claude_code",
    access: {
      owners: ["admin@example.com"],
      shared_with: [],
      shared_with_groups: ["sre-team"],
    },
  },
  {
    id: "agent-forbidden",
    name: "Forbidden Agent",
    gateway_url: "http://127.0.0.1:8004",
    backend: "claude_code",
    access: {
      owners: ["other@example.com"],
      shared_with: [],
      shared_with_groups: [],
    },
  },
];

const testGroups: Group[] = [
  { id: "sre-team", name: "SRE Team", members: ["owner@example.com"] },
];

describe.skipIf(!DB_URL)("Agents routes", () => {
  let pool: pg.Pool;
  let app: express.Application;
  const ownerEmail = `agents_owner_${Date.now()}@example.com`;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));
    app.use(
      "/api/agents",
      makeAgentsRouter(pool, () => testAgents, () => testGroups, SESSION_SECRET)
    );

    // Create test user (using actual email)
    const authRouter = makeAuthRouter(pool, SESSION_SECRET);
    // Just signup via direct call
    const { signup } = await import("../auth.js");
    await signup(pool, { email: ownerEmail, password: "testpass1234" }).catch(() => {});
  });

  afterAll(async () => {
    await pool.query("DELETE FROM users WHERE email = $1", [ownerEmail]).catch(() => {});
    await pool.end();
  });

  async function makeRequest(
    method: string,
    path: string,
    body?: unknown,
    cookie?: string
  ): Promise<{ status: number; body: unknown }> {
    return new Promise((resolve) => {
      const server = app.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (cookie) headers["Cookie"] = cookie;

        fetch(`http://127.0.0.1:${port}${path}`, {
          method,
          headers,
          body: body ? JSON.stringify(body) : undefined,
        }).then(async (r) => {
          const b = await r.json().catch(() => ({}));
          server.close(() => resolve({ status: r.status, body: b }));
        });
      });
    });
  }

  async function loginAndGetCookie(email: string, password: string): Promise<string> {
    return new Promise((resolve) => {
      const server = app.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        fetch(`http://127.0.0.1:${port}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        }).then(async (r) => {
          const cookies = r.headers.getSetCookie?.() ?? [];
          const sessionCookie = cookies.find((c: string) => c.includes("incidara_session")) ?? "";
          server.close(() => resolve(sessionCookie.split(";")[0]));
        });
      });
    });
  }

  it("GET /api/agents without auth returns 401", async () => {
    const { status } = await makeRequest("GET", "/api/agents");
    expect(status).toBe(401);
  });

  it("GET /api/agents returns only accessible agents with access_level", async () => {
    // Login with owner email (uses agent owner email for access checks)
    // We need to use a custom agents array where owner is the test user
    const agentsWithTestOwner: Agent[] = [
      {
        id: "agent-my-owner",
        name: "My Owner Agent",
        gateway_url: "http://127.0.0.1:8001",
        backend: "claude_code",
        access: {
          owners: [ownerEmail],
          shared_with: [],
          shared_with_groups: [],
        },
      },
      {
        id: "agent-no-access",
        name: "No Access Agent",
        gateway_url: "http://127.0.0.1:8002",
        backend: "claude_code",
        access: {
          owners: ["other@example.com"],
          shared_with: [],
          shared_with_groups: [],
        },
      },
    ];

    const testApp = express();
    testApp.use(express.json());
    testApp.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));
    testApp.use(
      "/api/agents",
      makeAgentsRouter(pool, () => agentsWithTestOwner, () => testGroups, SESSION_SECRET)
    );

    const cookie = await new Promise<string>((resolve) => {
      const server = testApp.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        fetch(`http://127.0.0.1:${port}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: ownerEmail, password: "testpass1234" }),
        }).then(async (r) => {
          const cookies = r.headers.getSetCookie?.() ?? [];
          const sessionCookie = cookies.find((c: string) => c.includes("incidara_session")) ?? "";
          server.close(() => resolve(sessionCookie.split(";")[0]));
        });
      });
    });

    const { status, body } = await new Promise<{ status: number; body: unknown }>((resolve) => {
      const server = testApp.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        fetch(`http://127.0.0.1:${port}/api/agents`, {
          headers: { Cookie: cookie },
        }).then(async (r) => {
          const b = await r.json().catch(() => ({}));
          server.close(() => resolve({ status: r.status, body: b }));
        });
      });
    });

    expect(status).toBe(200);
    const b = body as { agents: Array<{ id: string; access_level: string }> };
    expect(b.agents).toHaveLength(1);
    expect(b.agents[0].id).toBe("agent-my-owner");
    expect(b.agents[0].access_level).toBe("owner");
    // gateway_url should not be present
    expect((b.agents[0] as Record<string, unknown>).gateway_url).toBeUndefined();
  });

  it("GET /api/agents/:id returns 403 for inaccessible agent", async () => {
    const agentsWithTestOwner: Agent[] = [
      {
        id: "agent-forbidden-test",
        name: "Forbidden",
        gateway_url: "http://127.0.0.1:8001",
        backend: "claude_code",
        access: {
          owners: ["other@example.com"],
          shared_with: [],
          shared_with_groups: [],
        },
      },
    ];

    const testApp = express();
    testApp.use(express.json());
    testApp.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));
    testApp.use(
      "/api/agents",
      makeAgentsRouter(pool, () => agentsWithTestOwner, () => testGroups, SESSION_SECRET)
    );

    const cookie = await new Promise<string>((resolve) => {
      const server = testApp.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        fetch(`http://127.0.0.1:${port}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: ownerEmail, password: "testpass1234" }),
        }).then(async (r) => {
          const cookies = r.headers.getSetCookie?.() ?? [];
          const sessionCookie = cookies.find((c: string) => c.includes("incidara_session")) ?? "";
          server.close(() => resolve(sessionCookie.split(";")[0]));
        });
      });
    });

    const { status } = await new Promise<{ status: number; body: unknown }>((resolve) => {
      const server = testApp.listen(0, () => {
        const port = (server.address() as { port: number }).port;
        fetch(`http://127.0.0.1:${port}/api/agents/agent-forbidden-test`, {
          headers: { Cookie: cookie },
        }).then(async (r) => {
          const b = await r.json().catch(() => ({}));
          server.close(() => resolve({ status: r.status, body: b }));
        });
      });
    });

    expect(status).toBe(403);
  });
});
