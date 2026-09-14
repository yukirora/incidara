import { describe, it, expect, beforeEach, afterAll, beforeAll } from "vitest";
import { createPool } from "./db/client.js";
import { SessionStore } from "./session-store.js";
import { signup } from "./auth.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;

describe.skipIf(!DB_URL)("SessionStore", () => {
  let pool: pg.Pool;
  let store: SessionStore;
  const ownerEmail = `sess_owner_${Date.now()}@example.com`;
  const otherEmail = `sess_other_${Date.now()}@example.com`;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    store = new SessionStore(pool);
    // Create users needed for FK
    await signup(pool, { email: ownerEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: otherEmail, password: "testpass1234" }).catch(() => {});
  });

  afterAll(async () => {
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM users WHERE email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.end();
  });

  beforeEach(async () => {
    // Only delete sessions owned by our test users to avoid cross-test interference
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]);
  });

  it("creates a session and retrieves it by id", async () => {
    const session = await store.create({
      gatewaySessionId: "gw-1",
      agentId: "agent-a",
      ownerEmail,
      title: "Test session",
    });

    expect(session.id).toBeTypeOf("number");
    expect(session.gateway_session_id).toBe("gw-1");
    expect(session.agent_id).toBe("agent-a");
    expect(session.owner_email).toBe(ownerEmail);
    expect(session.title).toBe("Test session");
    expect(session.shared_with_users).toEqual([]);
    expect(session.shared_with_groups).toEqual([]);

    const fetched = await store.getById(session.id);
    expect(fetched).not.toBeNull();
    expect(fetched!.id).toBe(session.id);
  });

  it("getById returns null for missing id", async () => {
    const result = await store.getById(999999);
    expect(result).toBeNull();
  });

  it("getByGatewayId retrieves by gateway id", async () => {
    await store.create({
      gatewaySessionId: "gw-lookup",
      agentId: "agent-a",
      ownerEmail,
    });

    const found = await store.getByGatewayId("gw-lookup");
    expect(found).not.toBeNull();
    expect(found!.gateway_session_id).toBe("gw-lookup");

    const notFound = await store.getByGatewayId("gw-nonexistent");
    expect(notFound).toBeNull();
  });

  it("listVisible returns only owner's sessions when scope=mine", async () => {
    await store.create({ gatewaySessionId: "gw-mine-1", agentId: "agent-a", ownerEmail });
    await store.create({ gatewaySessionId: "gw-other-1", agentId: "agent-a", ownerEmail: otherEmail });

    const sessions = await store.listVisible({ email: ownerEmail, groups: [], scope: "mine" });
    expect(sessions).toHaveLength(1);
    expect(sessions[0].gateway_session_id).toBe("gw-mine-1");
  });

  it("listVisible returns shared sessions when scope=shared", async () => {
    const ownedSession = await store.create({
      gatewaySessionId: "gw-vis-owned",
      agentId: "agent-a",
      ownerEmail: otherEmail,
    });
    // Share with ownerEmail via user list
    await store.updateSharing(ownedSession.id, { shared_with_users: [ownerEmail] });

    const sessions = await store.listVisible({ email: ownerEmail, groups: [], scope: "shared" });
    expect(sessions.some((s) => s.gateway_session_id === "gw-vis-owned")).toBe(true);
    // The ownerEmail's own sessions should NOT be in scope=shared
    await store.create({ gatewaySessionId: "gw-vis-own2", agentId: "agent-a", ownerEmail });
    const sessions2 = await store.listVisible({ email: ownerEmail, groups: [], scope: "shared" });
    expect(sessions2.some((s) => s.gateway_session_id === "gw-vis-own2")).toBe(false);
  });

  it("listVisible with group visibility", async () => {
    const s = await store.create({
      gatewaySessionId: "gw-group-1",
      agentId: "agent-a",
      ownerEmail: otherEmail,
    });
    await store.updateSharing(s.id, { shared_with_groups: ["sre-team"] });

    // ownerEmail is in sre-team
    const sessions = await store.listVisible({ email: ownerEmail, groups: ["sre-team"] });
    expect(sessions.some((s) => s.gateway_session_id === "gw-group-1")).toBe(true);

    // User not in sre-team should not see it
    const sessions2 = await store.listVisible({ email: ownerEmail, groups: ["other-team"] });
    expect(sessions2.some((s) => s.gateway_session_id === "gw-group-1")).toBe(false);
  });

  it("listVisible filters by agentId", async () => {
    await store.create({ gatewaySessionId: "gw-agent-a", agentId: "agent-a", ownerEmail });
    await store.create({ gatewaySessionId: "gw-agent-b", agentId: "agent-b", ownerEmail });

    const sessions = await store.listVisible({ email: ownerEmail, groups: [], agentId: "agent-a" });
    expect(sessions).toHaveLength(1);
    expect(sessions[0].agent_id).toBe("agent-a");
  });

  it("updateTitle changes the title", async () => {
    const session = await store.create({
      gatewaySessionId: "gw-title",
      agentId: "agent-a",
      ownerEmail,
    });

    const updated = await store.updateTitle(session.id, "New title");
    expect(updated!.title).toBe("New title");
  });

  it("updateSharing updates shared_with_users and groups", async () => {
    const session = await store.create({
      gatewaySessionId: "gw-share",
      agentId: "agent-a",
      ownerEmail,
    });

    const updated = await store.updateSharing(session.id, {
      shared_with_users: [otherEmail],
      shared_with_groups: ["dev-team"],
    });

    expect(updated!.shared_with_users).toContain(otherEmail);
    expect(updated!.shared_with_groups).toContain("dev-team");
  });

  it("delete removes the session", async () => {
    const session = await store.create({
      gatewaySessionId: "gw-delete",
      agentId: "agent-a",
      ownerEmail,
    });

    await store.delete(session.id);
    const result = await store.getById(session.id);
    expect(result).toBeNull();
  });
});
