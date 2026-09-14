import { Router } from "express";
import { z } from "zod";
import type pg from "pg";
import type { Agent } from "../agent-registry.js";
import type { EnrichedAgent } from "../agent-registry.js";
import type { Group } from "../groups.js";
import { DASHBOARDS } from "../dashboard-registry.js";
import {
  listAllUsers,
  deleteUser,
  createUser,
  getDbGroupMemberships,
  addGroupMember,
  removeGroupMember,
  listCustomGroups,
  createCustomGroup,
  deleteCustomGroup,
  addAgentGroupAccess,
  removeAgentGroupAccess,
  updateAgentGroupAccessLevel,
  getAllAgentGroupAccess,
  getDashboardPermissions,
  upsertDashboardPermission,
} from "../groups.js";
import { hashPassword } from "../auth.js";

interface AdminRouterDeps {
  pool: pg.Pool;
  getAgents: () => Agent[];
  getGroups: () => Group[];
  getAllGroups: () => Promise<Group[]>;
  getEnrichedAgents: () => Promise<EnrichedAgent[]>;
}

export function makeAdminRouter(deps: AdminRouterDeps): Router {
  const { pool, getAgents, getGroups, getAllGroups, getEnrichedAgents } = deps;
  const router = Router();

  // Admin check middleware — uses req.auth
  router.use((req, res, next): void => {
    const auth = req.auth!;
    if (!auth.isAdmin) {
      res.status(403).json({ error: "Forbidden: admin access required" });
      return;
    }
    next();
  });

  // ── Users ─────────────────────────────────────────────────────────────────

  // GET /api/admin/users — list all users
  router.get("/users", async (_req, res): Promise<void> => {
    try {
      const users = await listAllUsers(pool);
      res.json({ users });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // DELETE /api/admin/users/:email — delete a user (cascade via FK)
  router.delete("/users/:email", async (req, res): Promise<void> => {
    const { email } = req.params;
    const auth = req.auth!;

    if (email.toLowerCase() === auth.email.toLowerCase()) {
      res.status(400).json({ error: "Cannot delete your own account" });
      return;
    }

    try {
      const deleted = await deleteUser(pool, email);
      if (!deleted) {
        res.status(404).json({ error: "User not found" });
        return;
      }
      res.status(204).send();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // ── Groups ────────────────────────────────────────────────────────────────

  // GET /api/admin/groups — list all groups (YAML + custom) with members
  router.get("/groups", async (_req, res): Promise<void> => {
    try {
      const allGroups = await getAllGroups();
      const dbMemberships = await getDbGroupMemberships(pool);
      const customGroups = await listCustomGroups(pool);
      const customGroupIds = new Set(customGroups.map((cg) => cg.id));
      const yamlGroups = getGroups();
      const yamlGroupIds = new Set(yamlGroups.map((g) => g.id));

      const result = allGroups.map((group) => {
        const isYaml = yamlGroupIds.has(group.id);
        const isCustom = customGroupIds.has(group.id);
        const yamlMembers = group.members.map((email) => ({
          email,
          source: "yaml" as const,
          added_by: null,
          added_at: null,
        }));

        const dbMembers = dbMemberships
          .filter((m) => m.group_id === group.id)
          .filter((m) => !group.members.includes(m.user_email))
          .map((m) => ({
            email: m.user_email,
            source: "db" as const,
            added_by: m.added_by,
            added_at: m.created_at,
          }));

        return {
          id: group.id,
          name: group.name,
          source: isYaml ? ("yaml" as const) : ("db" as const),
          can_delete: isCustom && !isYaml,
          members: [...yamlMembers, ...dbMembers],
        };
      });

      res.json({ groups: result });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // POST /api/admin/groups — create a new custom group
  const CreateGroupSchema = z.object({
    id: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/, "Group ID must be lowercase alphanumeric, dash or underscore"),
    name: z.string().min(1).max(255),
  });

  router.post("/groups", async (req, res): Promise<void> => {
    const parsed = CreateGroupSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }
    const { id, name } = parsed.data;

    const yamlGroups = getGroups();
    if (yamlGroups.some((g) => g.id === id)) {
      res.status(409).json({ error: "A YAML-defined group with this ID already exists" });
      return;
    }

    try {
      const group = await createCustomGroup(pool, id, name, req.auth!.email);
      res.status(201).json({ group });
    } catch (err: unknown) {
      const e = err as { code?: string };
      if (e.code === "23505") {
        res.status(409).json({ error: "A group with this ID already exists" });
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // DELETE /api/admin/groups/:groupId — delete a custom group
  router.delete("/groups/:groupId", async (req, res): Promise<void> => {
    const { groupId } = req.params;

    const yamlGroups = getGroups();
    if (yamlGroups.some((g) => g.id === groupId)) {
      res.status(400).json({ error: "Cannot delete YAML-defined groups" });
      return;
    }

    try {
      const deleted = await deleteCustomGroup(pool, groupId);
      if (!deleted) {
        res.status(404).json({ error: "Group not found" });
        return;
      }
      res.status(204).send();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // POST /api/admin/groups/:groupId/members — add user to group
  const AddMemberSchema = z.object({
    email: z.string().min(1),
  });

  router.post("/groups/:groupId/members", async (req, res): Promise<void> => {
    const { groupId } = req.params;
    const parsed = AddMemberSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const { email } = parsed.data;

    const { rows: userRows } = await pool.query(
      "SELECT email FROM users WHERE email = $1",
      [email]
    );
    if (userRows.length === 0) {
      res.status(404).json({ error: "User not found" });
      return;
    }

    try {
      const membership = await addGroupMember(pool, groupId, email, req.auth!.email);
      res.status(201).json({ membership });
    } catch (err: unknown) {
      const e = err as { code?: string };
      if (e.code === "23505") {
        res.status(409).json({ error: "User is already a member of this group" });
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // DELETE /api/admin/groups/:groupId/members/:email — remove from group (DB only)
  router.delete("/groups/:groupId/members/:email", async (req, res): Promise<void> => {
    const { groupId, email } = req.params;

    const yamlGroups = getGroups();
    const group = yamlGroups.find((g) => g.id === groupId);
    if (group && group.members.includes(email)) {
      res.status(400).json({ error: "Cannot remove YAML-defined group members" });
      return;
    }

    const removed = await removeGroupMember(pool, groupId, email);
    if (!removed) {
      res.status(404).json({ error: "Membership not found" });
      return;
    }

    res.status(204).send();
  });

  // ── Agents ────────────────────────────────────────────────────────────────

  // GET /api/admin/agents — list agents with enriched access config
  router.get("/agents", async (_req, res): Promise<void> => {
    try {
      const enriched = await getEnrichedAgents();
      const agents = enriched.map((agent) => ({
        id: agent.id,
        name: agent.name,
        description: agent.description,
        backend: agent.backend,
        access: {
          owners: agent.access.owners,
          groups: agent.access.groups,
          yaml_groups: agent.yaml_groups,
          db_groups: agent.db_groups,
        },
        group_levels: agent.group_levels,
      }));
      res.json({ agents });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // PATCH /api/admin/agents/:agentId/groups/:groupId — update access level
  const AccessLevelSchema = z.object({
    access_level: z.enum(["interactive", "readonly", "readonly_input", "readonly_summary"]),
  });

  router.patch("/agents/:agentId/groups/:groupId", async (req, res): Promise<void> => {
    const { agentId, groupId } = req.params;
    const parsed = AccessLevelSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const agents = getAgents();
    if (!agents.some((a) => a.id === agentId)) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    try {
      await updateAgentGroupAccessLevel(pool, agentId, groupId, parsed.data.access_level);
      res.json({ agent_id: agentId, group_id: groupId, access_level: parsed.data.access_level });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // GET /api/admin/dashboards — get all dashboard permissions + available dashboards
  router.get("/dashboards", async (_req, res): Promise<void> => {
    try {
      const permissions = await getDashboardPermissions(pool);
      res.json({ permissions, dashboards: DASHBOARDS });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // PATCH /api/admin/groups/:groupId/dashboards/:dashboard — set permission
  const DashboardPermSchema = z.object({
    allowed: z.boolean(),
  });

  router.patch("/groups/:groupId/dashboards/:dashboard", async (req, res): Promise<void> => {
    const { groupId, dashboard } = req.params;
    const parsed = DashboardPermSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    try {
      await upsertDashboardPermission(pool, groupId, dashboard, parsed.data.allowed);
      res.json({ group_id: groupId, dashboard, allowed: parsed.data.allowed });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // POST /api/admin/users — create user with password
  const CreateUserSchema = z.object({
    email: z.string().email(),
    password: z.string().min(8),
    name: z.string().optional(),
    groups: z.array(z.string()).optional(),
  });

  router.post("/users", async (req, res): Promise<void> => {
    const parsed = CreateUserSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }

    const { email, password, name, groups } = parsed.data;

    try {
      const hash = await hashPassword(password);
      const user = await createUser(pool, email, hash, name ?? null);

      // Add to requested groups
      if (groups && groups.length > 0) {
        for (const groupId of groups) {
          try {
            await addGroupMember(pool, groupId, email, req.auth!.email);
          } catch {
            // Group may not exist — skip
          }
        }
      }

      res.status(201).json({ user: { id: user.id, email: user.email, name: user.name, created_at: user.created_at } });
    } catch (err: unknown) {
      const e = err as { code?: string };
      if (e.code === "23505") {
        res.status(409).json({ error: "A user with this email already exists" });
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // POST /api/admin/agents/:agentId/groups — add group access to agent
  const AddAgentGroupSchema = z.object({
    group_id: z.string().min(1),
  });

  router.post("/agents/:agentId/groups", async (req, res): Promise<void> => {
    const { agentId } = req.params;
    const parsed = AddAgentGroupSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Validation failed", details: parsed.error.flatten() });
      return;
    }
    const { group_id } = parsed.data;

    const agents = getAgents();
    if (!agents.some((a) => a.id === agentId)) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    try {
      await addAgentGroupAccess(pool, agentId, group_id);
      res.status(201).json({ agent_id: agentId, group_id });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  // DELETE /api/admin/agents/:agentId/groups/:groupId — remove group access from agent
  router.delete("/agents/:agentId/groups/:groupId", async (req, res): Promise<void> => {
    const { agentId, groupId } = req.params;

    const agents = getAgents();
    const agent = agents.find((a) => a.id === agentId);
    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }
    if (agent.access.groups.includes(groupId)) {
      res.status(400).json({ error: "Cannot remove YAML-defined group access" });
      return;
    }

    try {
      const removed = await removeAgentGroupAccess(pool, agentId, groupId);
      if (!removed) {
        res.status(404).json({ error: "Agent group access not found" });
        return;
      }
      res.status(204).send();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: msg });
    }
  });

  return router;
}
