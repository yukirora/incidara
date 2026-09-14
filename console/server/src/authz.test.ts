import { describe, it, expect, vi } from "vitest";
import { AuthContext } from "./authz.js";
import type { Agent } from "./agent-registry.js";
import type { PermissionStore } from "./permission-store.js";

const agent: Agent = {
  id: "triage",
  name: "Triage",
  gateway_url: "http://127.0.0.1:8000",
  backend: "claude_code",
  access: { owners: ["owner@example.com"], groups: ["sre-team"] },
};

function context(email: string, groups: string[] = [], levels: Record<string, string> = {}) {
  const store = {
    getUserAgentLevels: vi.fn(async () => levels),
    getUserDashboardAccess: vi.fn(async () => ({})),
  } as unknown as PermissionStore;
  return new AuthContext(email, groups, "admins", false, store);
}

describe("AuthContext", () => {
  it("allows an agent owner", async () => {
    await expect(context("owner@example.com").canAccessAgent(agent)).resolves.toBe(true);
  });

  it("allows a configured group", async () => {
    await expect(context("user@example.com", ["sre-team"]).canAccessAgent(agent)).resolves.toBe(true);
  });

  it("allows database-granted access", async () => {
    await expect(context("user@example.com", [], { triage: "readonly" }).canAccessAgent(agent)).resolves.toBe(true);
  });

  it("denies an unconfigured user", async () => {
    await expect(context("nobody@example.com").canAccessAgent(agent)).resolves.toBe(false);
  });

  it("lets administrators access every agent", async () => {
    await expect(context("admin@example.com", ["admins"]).canAccessAgent(agent)).resolves.toBe(true);
  });

  it("lets owners view and operate their sessions", async () => {
    const auth = context("OWNER@EXAMPLE.COM");
    const session = { owner_email: "owner@example.com", shared_with_groups: [] };
    await expect(auth.canViewSession(session)).resolves.toBe(true);
    expect(auth.isSessionOwner(session)).toBe(true);
  });

  it("does not give a random user session ownership", () => {
    const session = { owner_email: "owner@example.com", shared_with_groups: [] };
    expect(context("viewer@example.com").isSessionOwner(session)).toBe(false);
  });
});
