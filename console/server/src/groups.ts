import fs from "fs";
import yaml from "js-yaml";
import { z } from "zod";
import type pg from "pg";

const GroupSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  members: z.array(z.string().email()),
});

export type Group = z.infer<typeof GroupSchema>;

const GroupsSchema = z.array(GroupSchema);

export function loadGroups(filePath: string): Group[] {
  const content = fs.readFileSync(filePath, "utf8");
  const raw = yaml.load(content);
  return GroupsSchema.parse(raw);
}

export function resolveUserGroups(groups: Group[], email: string): string[] {
  const normalizedEmail = email.toLowerCase().trim();
  return groups
    .filter((g) => g.members.some((m) => m.toLowerCase().trim() === normalizedEmail))
    .map((g) => g.id);
}

export async function resolveUserGroupsWithDb(
  yamlGroups: Group[],
  email: string,
  pool: pg.Pool
): Promise<string[]> {
  const yamlGroupIds = resolveUserGroups(yamlGroups, email);
  const { rows } = await pool.query<{ group_id: string }>(
    "SELECT group_id FROM group_memberships WHERE user_email = $1",
    [email]
  );
  const dbGroupIds = rows.map((r) => r.group_id);
  return [...new Set([...yamlGroupIds, ...dbGroupIds])];
}

export interface GroupMembership {
  id: number;
  group_id: string;
  user_email: string;
  added_by: string | null;
  created_at: string;
}

export async function getDbGroupMemberships(
  pool: pg.Pool,
  groupId?: string
): Promise<GroupMembership[]> {
  if (groupId) {
    const { rows } = await pool.query<GroupMembership>(
      "SELECT * FROM group_memberships WHERE group_id = $1 ORDER BY created_at",
      [groupId]
    );
    return rows;
  }
  const { rows } = await pool.query<GroupMembership>(
    "SELECT * FROM group_memberships ORDER BY group_id, created_at"
  );
  return rows;
}

export async function addGroupMember(
  pool: pg.Pool,
  groupId: string,
  email: string,
  addedBy: string
): Promise<GroupMembership> {
  const { rows } = await pool.query<GroupMembership>(
    `INSERT INTO group_memberships (group_id, user_email, added_by)
     VALUES ($1, $2, $3)
     RETURNING *`,
    [groupId, email, addedBy]
  );
  return rows[0];
}

export async function removeGroupMember(
  pool: pg.Pool,
  groupId: string,
  email: string
): Promise<boolean> {
  const { rowCount } = await pool.query(
    "DELETE FROM group_memberships WHERE group_id = $1 AND user_email = $2",
    [groupId, email]
  );
  return (rowCount ?? 0) > 0;
}

export interface DbUser {
  id: number;
  email: string;
  name: string | null;
  created_at: string;
}

export async function listAllUsers(pool: pg.Pool): Promise<DbUser[]> {
  const { rows } = await pool.query<DbUser>(
    "SELECT id, email, name, created_at FROM users ORDER BY created_at"
  );
  return rows;
}

export async function deleteUser(pool: pg.Pool, email: string): Promise<boolean> {
  const { rowCount } = await pool.query(
    "DELETE FROM users WHERE email = $1",
    [email]
  );
  return (rowCount ?? 0) > 0;
}

export interface CreateUserResult {
  id: number;
  email: string;
  name: string | null;
  created_at: string;
}

export async function createUser(
  pool: pg.Pool,
  email: string,
  passwordHash: string,
  name: string | null
): Promise<CreateUserResult> {
  const { rows } = await pool.query<CreateUserResult>(
    `INSERT INTO users (email, password_hash, name)
     VALUES ($1, $2, $3)
     RETURNING id, email, name, created_at`,
    [email, passwordHash, name]
  );
  return rows[0];
}

// ── Custom Groups ──────────────────────────────────────────────────────────

export interface CustomGroup {
  id: string;
  name: string;
  created_by: string | null;
  created_at: string;
}

export async function listCustomGroups(pool: pg.Pool): Promise<CustomGroup[]> {
  const { rows } = await pool.query<CustomGroup>(
    "SELECT id, name, created_by, created_at FROM custom_groups ORDER BY created_at"
  );
  return rows;
}

export async function createCustomGroup(
  pool: pg.Pool,
  id: string,
  name: string,
  createdBy: string
): Promise<CustomGroup> {
  const { rows } = await pool.query<CustomGroup>(
    `INSERT INTO custom_groups (id, name, created_by)
     VALUES ($1, $2, $3)
     RETURNING *`,
    [id, name, createdBy]
  );
  return rows[0];
}

export async function deleteCustomGroup(pool: pg.Pool, id: string): Promise<boolean> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query("DELETE FROM group_memberships WHERE group_id = $1", [id]);
    await client.query("DELETE FROM agent_group_access WHERE group_id = $1", [id]);
    const { rowCount } = await client.query("DELETE FROM custom_groups WHERE id = $1", [id]);
    await client.query("COMMIT");
    return (rowCount ?? 0) > 0;
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}

// ── Agent Group Access ─────────────────────────────────────────────────────

export async function getAgentGroupAccess(pool: pg.Pool, agentId: string): Promise<string[]> {
  const { rows } = await pool.query<{ group_id: string }>(
    "SELECT group_id FROM agent_group_access WHERE agent_id = $1",
    [agentId]
  );
  return rows.map((r) => r.group_id);
}

export async function addAgentGroupAccess(
  pool: pg.Pool,
  agentId: string,
  groupId: string
): Promise<void> {
  await pool.query(
    `INSERT INTO agent_group_access (agent_id, group_id)
     VALUES ($1, $2)
     ON CONFLICT DO NOTHING`,
    [agentId, groupId]
  );
}

export async function removeAgentGroupAccess(
  pool: pg.Pool,
  agentId: string,
  groupId: string
): Promise<boolean> {
  const { rowCount } = await pool.query(
    "DELETE FROM agent_group_access WHERE agent_id = $1 AND group_id = $2",
    [agentId, groupId]
  );
  return (rowCount ?? 0) > 0;
}

export interface AgentGroupAccessRow {
  agent_id: string;
  group_id: string;
  access_level: string;
  updated_at: string;
}

export async function getAllAgentGroupAccess(pool: pg.Pool): Promise<AgentGroupAccessRow[]> {
  const { rows } = await pool.query<AgentGroupAccessRow>(
    "SELECT agent_id, group_id, access_level, updated_at FROM agent_group_access"
  );
  return rows;
}

export async function updateAgentGroupAccessLevel(
  pool: pg.Pool,
  agentId: string,
  groupId: string,
  accessLevel: string
): Promise<void> {
  await pool.query(
    `UPDATE agent_group_access
     SET access_level = $3, updated_at = now()
     WHERE agent_id = $1 AND group_id = $2`,
    [agentId, groupId, accessLevel]
  );
}

export async function getAgentGroupAccessLevel(
  pool: pg.Pool,
  agentId: string,
  groupId: string
): Promise<string | null> {
  const { rows } = await pool.query<{ access_level: string }>(
    "SELECT access_level FROM agent_group_access WHERE agent_id = $1 AND group_id = $2",
    [agentId, groupId]
  );
  return rows[0]?.access_level ?? null;
}

// ── Dashboard Permissions ──────────────────────────────────────────────────

export interface GroupDashboardPermission {
  group_id: string;
  dashboard: string;
  allowed: boolean;
}

export async function getDashboardPermissions(pool: pg.Pool): Promise<GroupDashboardPermission[]> {
  const { rows } = await pool.query<GroupDashboardPermission>(
    "SELECT group_id, dashboard, allowed FROM group_dashboard_permissions ORDER BY group_id, dashboard"
  );
  return rows;
}

export async function upsertDashboardPermission(
  pool: pg.Pool,
  groupId: string,
  dashboard: string,
  allowed: boolean
): Promise<void> {
  await pool.query(
    `INSERT INTO group_dashboard_permissions (group_id, dashboard, allowed, updated_at)
     VALUES ($1, $2, $3, now())
     ON CONFLICT (group_id, dashboard)
     DO UPDATE SET allowed = $3, updated_at = now()`,
    [groupId, dashboard, allowed]
  );
}
