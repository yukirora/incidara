import { describe, it, expect, beforeAll, afterAll } from "vitest";
import express from "express";
import { createPool } from "../db/client.js";
import { runMigrations } from "../db/migrate.js";
import path from "path";
import { fileURLToPath } from "url";
import { signup } from "../auth.js";
import { makeAuthRouter } from "./auth.js";
import { makeAdminRouter } from "./admin.js";
import { requireAuth } from "../middleware/require-auth.js";
import type { Agent } from "../agent-registry.js";
import { enrichAgentsWithDbGroups } from "../agent-registry.js";
import type { Group } from "../groups.js";
import type pg from "pg";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const DB_URL = process.env.CHAT_UI_DATABASE_URL;
const SESSION_SECRET = "test-secret-that-is-at-least-32-chars!!";
const ADMIN_GROUP = "admins";

const testAgents: Agent[] = [
  {
    id: "test-agent-1",
    name: "Test Agent 1",
    gateway_url: "http://127.0.0.1:9999",
    backend: "claude_code",
    access: { owners: [], shared_with: [], shared_with_groups: ["admins"] },
  },
];

describe.skipIf(!DB_URL)("Admin routes", () => {
  let pool: pg.Pool;
  let app: express.Application;
  const ts = Date.now();
  const adminEmail = `admin_route_admin_${ts}@example.com`;
  const normalEmail = `admin_route_user_${ts}@example.com`;
  const targetEmail = `admin_route_target_${ts}@example.com`;

  // YAML groups: admin is in "admins" group
  const yamlGroups: Group[] = [
    { id: "admins", name: "Admins", members: [adminEmail] },
    { id: "sre-team", name: "SRE Team", members: [] },
  ];

  async function resolveGroups(email: string): Promise<string[]> {
    const yamlGroupIds = yamlGroups
      .filter((g) => g.members.includes(email))
      .map((g) => g.id);
    const { rows } = await pool.query<{ group_id: string }>(
      "SELECT DISTINCT group_id FROM group_memberships WHERE user_email = $1",
      [email]
    );
    return [...new Set([...yamlGroupIds, ...rows.map((r) => r.group_id)])];
  }

  async function getAllGroups(): Promise<Group[]> {
    const { rows } = await pool.query<{ id: string; name: string }>(
      "SELECT id, name FROM custom_groups ORDER BY created_at"
    );
    const customIds = new Set(rows.map((r) => r.id));
    const extra = rows
      .filter((r) => !yamlGroups.some((g) => g.id === r.id))
      .map((r) => ({ id: r.id, name: r.name, members: [] as string[] }));
    void customIds; // used implicitly
    return [...yamlGroups, ...extra];
  }

  let adminCookie: string;
  let normalCookie: string;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    // Ensure migrations are applied (including new tables)
    const migrationsDir = path.resolve(__dirname, "../db/migrations");
    await runMigrations(pool, migrationsDir);

    app = express();
    app.use(express.json());
    app.use("/api/auth", makeAuthRouter(pool, SESSION_SECRET));
    app.use(
      "/api/admin",
      requireAuth(SESSION_SECRET),
      makeAdminRouter({
        pool,
        getAgents: () => testAgents,
        getGroups: () => yamlGroups,
        getAllGroups,
        getEnrichedAgents: () => enrichAgentsWithDbGroups(testAgents, pool),
        resolveGroups,
        adminGroup: ADMIN_GROUP,
      })
    );

    // Create test users
    await signup(pool, { email: adminEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: normalEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: targetEmail, password: "testpass1234" }).catch(() => {});

    // Get cookies
    adminCookie = await loginAndGetCookie(adminEmail, "testpass1234");
    normalCookie = await loginAndGetCookie(normalEmail, "testpass1234");
  });

  afterAll(async () => {
    await pool.query("DELETE FROM agent_group_access WHERE agent_id = 'test-agent-1'").catch(() => {});
    await pool.query("DELETE FROM custom_groups WHERE id LIKE 'test-custom-%'").catch(() => {});
    await pool.query("DELETE FROM group_memberships WHERE user_email IN ($1,$2,$3)", [
      adminEmail, normalEmail, targetEmail,
    ]).catch(() => {});
    await pool.query("DELETE FROM users WHERE email IN ($1,$2,$3)", [
      adminEmail, normalEmail, targetEmail,
    ]).catch(() => {});
    await pool.end();
  });

  async function request(
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
          const b = r.status === 204 ? {} : await r.json().catch(() => ({}));
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

  // ── Authorization ────────────────────────────────────────────────────────

  it("GET /api/admin/users returns 401 without auth", async () => {
    const { status } = await request("GET", "/api/admin/users");
    expect(status).toBe(401);
  });

  it("GET /api/admin/users returns 403 for non-admin user", async () => {
    const { status } = await request("GET", "/api/admin/users", undefined, normalCookie);
    expect(status).toBe(403);
  });

  // ── Users ────────────────────────────────────────────────────────────────

  it("GET /api/admin/users returns list for admin", async () => {
    const { status, body } = await request("GET", "/api/admin/users", undefined, adminCookie);
    expect(status).toBe(200);
    const b = body as { users: Array<{ email: string }> };
    expect(Array.isArray(b.users)).toBe(true);
    const emails = b.users.map((u) => u.email);
    expect(emails).toContain(adminEmail);
    expect(emails).toContain(normalEmail);
  });

  it("DELETE /api/admin/users/:email deletes a non-self user", async () => {
    // Create a throwaway user
    const throwawayEmail = `admin_route_throwaway_${ts}@example.com`;
    await signup(pool, { email: throwawayEmail, password: "testpass1234" }).catch(() => {});

    const { status } = await request(
      "DELETE",
      `/api/admin/users/${encodeURIComponent(throwawayEmail)}`,
      undefined,
      adminCookie
    );
    expect(status).toBe(204);
  });

  it("DELETE /api/admin/users/:email returns 400 when deleting self", async () => {
    const { status } = await request(
      "DELETE",
      `/api/admin/users/${encodeURIComponent(adminEmail)}`,
      undefined,
      adminCookie
    );
    expect(status).toBe(400);
  });

  it("DELETE /api/admin/users/:email returns 404 for unknown user", async () => {
    const { status } = await request(
      "DELETE",
      `/api/admin/users/nobody_unknown_xyz@example.com`,
      undefined,
      adminCookie
    );
    expect(status).toBe(404);
  });

  // ── Groups ───────────────────────────────────────────────────────────────

  it("GET /api/admin/groups returns group list with members", async () => {
    const { status, body } = await request("GET", "/api/admin/groups", undefined, adminCookie);
    expect(status).toBe(200);
    const b = body as { groups: Array<{ id: string; name: string; source: string; can_delete: boolean; members: Array<{ email: string; source: string }> }> };
    expect(Array.isArray(b.groups)).toBe(true);
    const adminGroup = b.groups.find((g) => g.id === "admins");
    expect(adminGroup).toBeDefined();
    expect(adminGroup!.source).toBe("yaml");
    expect(adminGroup!.can_delete).toBe(false);
    const adminMember = adminGroup!.members.find((m) => m.email === adminEmail);
    expect(adminMember).toBeDefined();
    expect(adminMember!.source).toBe("yaml");
  });

  it("POST /api/admin/groups creates a custom group", async () => {
    const { status, body } = await request(
      "POST",
      "/api/admin/groups",
      { id: `test-custom-${ts}`, name: "Test Custom Group" },
      adminCookie
    );
    expect(status).toBe(201);
    const b = body as { group: { id: string; name: string } };
    expect(b.group.id).toBe(`test-custom-${ts}`);
    expect(b.group.name).toBe("Test Custom Group");
  });

  it("POST /api/admin/groups returns 409 on duplicate group id", async () => {
    const { status } = await request(
      "POST",
      "/api/admin/groups",
      { id: `test-custom-${ts}`, name: "Duplicate" },
      adminCookie
    );
    expect(status).toBe(409);
  });

  it("POST /api/admin/groups returns 409 when YAML group id conflicts", async () => {
    const { status } = await request(
      "POST",
      "/api/admin/groups",
      { id: "admins", name: "Conflict" },
      adminCookie
    );
    expect(status).toBe(409);
  });

  it("DELETE /api/admin/groups/:groupId deletes custom group", async () => {
    // Create a group to delete
    await request(
      "POST",
      "/api/admin/groups",
      { id: `test-custom-del-${ts}`, name: "To Delete" },
      adminCookie
    );
    const { status } = await request(
      "DELETE",
      `/api/admin/groups/test-custom-del-${ts}`,
      undefined,
      adminCookie
    );
    expect(status).toBe(204);
  });

  it("DELETE /api/admin/groups/:groupId returns 400 for YAML group", async () => {
    const { status } = await request(
      "DELETE",
      "/api/admin/groups/admins",
      undefined,
      adminCookie
    );
    expect(status).toBe(400);
  });

  it("POST /api/admin/groups/:groupId/members adds a user", async () => {
    const { status, body } = await request(
      "POST",
      "/api/admin/groups/sre-team/members",
      { email: targetEmail },
      adminCookie
    );
    expect(status).toBe(201);
    const b = body as { membership: { group_id: string; user_email: string } };
    expect(b.membership.group_id).toBe("sre-team");
    expect(b.membership.user_email).toBe(targetEmail);
  });

  it("POST /api/admin/groups/:groupId/members returns 409 on duplicate", async () => {
    const { status } = await request(
      "POST",
      "/api/admin/groups/sre-team/members",
      { email: targetEmail },
      adminCookie
    );
    expect(status).toBe(409);
  });

  it("POST /api/admin/groups/:groupId/members returns 404 for unknown user", async () => {
    const { status } = await request(
      "POST",
      "/api/admin/groups/sre-team/members",
      { email: "nobody_nonexistent_xyz@example.com" },
      adminCookie
    );
    expect(status).toBe(404);
  });

  it("DELETE /api/admin/groups/:groupId/members/:email removes DB member", async () => {
    const { status } = await request(
      "DELETE",
      `/api/admin/groups/sre-team/members/${encodeURIComponent(targetEmail)}`,
      undefined,
      adminCookie
    );
    expect(status).toBe(204);
  });

  it("DELETE /api/admin/groups/:groupId/members/:email returns 404 for non-member", async () => {
    const { status } = await request(
      "DELETE",
      `/api/admin/groups/sre-team/members/${encodeURIComponent(targetEmail)}`,
      undefined,
      adminCookie
    );
    expect(status).toBe(404);
  });

  it("DELETE /api/admin/groups/:groupId/members/:email returns 400 for YAML member", async () => {
    const { status } = await request(
      "DELETE",
      `/api/admin/groups/admins/members/${encodeURIComponent(adminEmail)}`,
      undefined,
      adminCookie
    );
    expect(status).toBe(400);
  });

  // ── Agents ───────────────────────────────────────────────────────────────

  it("GET /api/admin/agents returns agent list with yaml_groups and db_groups", async () => {
    const { status, body } = await request("GET", "/api/admin/agents", undefined, adminCookie);
    expect(status).toBe(200);
    const b = body as { agents: Array<{ id: string; access: { owners: string[]; yaml_groups: string[]; db_groups: string[] } }> };
    expect(Array.isArray(b.agents)).toBe(true);
    expect(b.agents[0].id).toBe("test-agent-1");
    expect(Array.isArray(b.agents[0].access.yaml_groups)).toBe(true);
    expect(Array.isArray(b.agents[0].access.db_groups)).toBe(true);
    // gateway_url should not be exposed
    expect((b.agents[0] as Record<string, unknown>).gateway_url).toBeUndefined();
  });

  it("POST /api/admin/agents/:agentId/groups adds group access", async () => {
    const { status, body } = await request(
      "POST",
      "/api/admin/agents/test-agent-1/groups",
      { group_id: "sre-team" },
      adminCookie
    );
    expect(status).toBe(201);
    const b = body as { agent_id: string; group_id: string };
    expect(b.agent_id).toBe("test-agent-1");
    expect(b.group_id).toBe("sre-team");
  });

  it("GET /api/admin/agents includes db_groups after adding", async () => {
    const { status, body } = await request("GET", "/api/admin/agents", undefined, adminCookie);
    expect(status).toBe(200);
    const b = body as { agents: Array<{ id: string; access: { shared_with_groups: string[]; db_groups: string[] } }> };
    const agent = b.agents.find((a) => a.id === "test-agent-1");
    expect(agent).toBeDefined();
    expect(agent!.access.db_groups).toContain("sre-team");
    expect(agent!.access.shared_with_groups).toContain("sre-team");
  });

  it("DELETE /api/admin/agents/:agentId/groups/:groupId removes DB group access", async () => {
    const { status } = await request(
      "DELETE",
      "/api/admin/agents/test-agent-1/groups/sre-team",
      undefined,
      adminCookie
    );
    expect(status).toBe(204);
  });

  it("DELETE /api/admin/agents/:agentId/groups/:groupId returns 400 for YAML group", async () => {
    const { status } = await request(
      "DELETE",
      "/api/admin/agents/test-agent-1/groups/admins",
      undefined,
      adminCookie
    );
    expect(status).toBe(400);
  });

  it("DELETE /api/admin/agents/:agentId/groups/:groupId returns 404 for non-existent access", async () => {
    const { status } = await request(
      "DELETE",
      "/api/admin/agents/test-agent-1/groups/nonexistent-group",
      undefined,
      adminCookie
    );
    expect(status).toBe(404);
  });

  it("POST /api/admin/agents/:agentId/groups returns 404 for unknown agent", async () => {
    const { status } = await request(
      "POST",
      "/api/admin/agents/nonexistent-agent/groups",
      { group_id: "sre-team" },
      adminCookie
    );
    expect(status).toBe(404);
  });
});
