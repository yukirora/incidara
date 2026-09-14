import type pg from "pg";

export type AccessLevel = "interactive" | "readonly" | "readonly_input" | "readonly_summary";

const ACCESS_RANK: Record<AccessLevel, number> = {
  readonly_summary: 1,
  readonly_input: 2,
  readonly: 3,
  interactive: 4,
};

export interface Group {
  id: string;
  name: string;
  active: boolean;
  created_by: string | null;
  created_at: Date;
  updated_at: Date;
}

export interface GroupMember {
  id: number;
  group_id: string;
  user_email: string;
  added_by: string | null;
  created_at: Date;
}

export interface AgentPermission {
  group_id: string;
  agent_id: string;
  access_level: AccessLevel;
  updated_at: Date;
}

export interface DashboardPermission {
  group_id: string;
  dashboard: string;
  allowed: boolean;
  updated_at: Date;
}

interface CacheEntry<T> {
  data: T;
  expires: number;
}

export class PermissionStore {
  private pool: pg.Pool;
  private groupsCache: CacheEntry<Group[]> | null = null;
  private userGroupsCache: CacheEntry<Map<string, string[]>> | null = null;
  private agentPermsCache: CacheEntry<Map<string, Map<string, AccessLevel>>> | null = null;
  private dashboardPermsCache: CacheEntry<Map<string, Map<string, boolean>>> | null = null;
  private readonly TTL_MS = 5 * 60 * 1000; // 5 minutes

  constructor(pool: pg.Pool) {
    this.pool = pool;
  }

  private isExpired(entry: CacheEntry<unknown>): boolean {
    return Date.now() > entry.expires;
  }

  private now(): number {
    return Date.now();
  }

  // ─── Groups ────────────────────────────────────────────────────────────────

  async getAllGroups(): Promise<Group[]> {
    if (this.groupsCache && !this.isExpired(this.groupsCache)) {
      return this.groupsCache.data;
    }
    const { rows } = await this.pool.query<Group>(
      `SELECT id, name, active, created_by, created_at, updated_at FROM custom_groups ORDER BY name`
    );
    this.groupsCache = { data: rows, expires: this.now() + this.TTL_MS };
    return rows;
  }

  async getGroup(id: string): Promise<Group | null> {
    const groups = await this.getAllGroups();
    return groups.find((g) => g.id === id) ?? null;
  }

  async createGroup(id: string, name: string, createdBy: string): Promise<Group> {
    const { rows } = await this.pool.query<Group>(
      `INSERT INTO custom_groups (id, name, created_by) VALUES ($1, $2, $3)
       RETURNING id, name, active, created_by, created_at, updated_at`,
      [id, name, createdBy]
    );
    this.invalidate();
    return rows[0];
  }

  async updateGroup(id: string, updates: { name?: string; active?: boolean }): Promise<void> {
    const sets: string[] = [];
    const vals: any[] = [];
    let idx = 1;
    if (updates.name !== undefined) { sets.push(`name = $${idx++}`); vals.push(updates.name); }
    if (updates.active !== undefined) { sets.push(`active = $${idx++}`); vals.push(updates.active); }
    sets.push(`updated_at = now()`);
    vals.push(id);
    await this.pool.query(
      `UPDATE custom_groups SET ${sets.join(", ")} WHERE id = $${idx}`,
      vals
    );
    this.invalidate();
  }

  async deleteGroup(id: string): Promise<void> {
    await this.pool.query(`DELETE FROM custom_groups WHERE id = $1`, [id]);
    this.invalidate();
  }

  // ─── Members ───────────────────────────────────────────────────────────────

  async getMembers(groupId: string): Promise<GroupMember[]> {
    const { rows } = await this.pool.query<GroupMember>(
      `SELECT id, group_id, user_email, added_by, created_at
       FROM group_memberships WHERE group_id = $1 ORDER BY created_at`,
      [groupId]
    );
    return rows;
  }

  async addMember(groupId: string, email: string, addedBy: string): Promise<void> {
    await this.pool.query(
      `INSERT INTO group_memberships (group_id, user_email, added_by)
       VALUES ($1, $2, $3) ON CONFLICT (group_id, user_email) DO NOTHING`,
      [groupId, email, addedBy]
    );
    this.invalidate();
  }

  async removeMember(groupId: string, email: string): Promise<void> {
    await this.pool.query(
      `DELETE FROM group_memberships WHERE group_id = $1 AND user_email = $2`,
      [groupId, email]
    );
    this.invalidate();
  }

  /** Returns map: userEmail → groupIds[] */
  async getUserGroupsMap(): Promise<Map<string, string[]>> {
    if (this.userGroupsCache && !this.isExpired(this.userGroupsCache)) {
      return this.userGroupsCache.data;
    }
    const { rows } = await this.pool.query<{ user_email: string; group_id: string }>(
      `SELECT gm.user_email, gm.group_id
       FROM group_memberships gm
       JOIN custom_groups cg ON cg.id = gm.group_id
       WHERE cg.active = true`
    );
    const map = new Map<string, string[]>();
    for (const r of rows) {
      if (!map.has(r.user_email)) map.set(r.user_email, []);
      map.get(r.user_email)!.push(r.group_id);
    }
    this.userGroupsCache = { data: map, expires: this.now() + this.TTL_MS };
    return map;
  }

  async getUserGroups(userEmail: string): Promise<string[]> {
    const map = await this.getUserGroupsMap();
    return map.get(userEmail) ?? [];
  }

  // ─── Agent Permissions ─────────────────────────────────────────────────────

  /**
   * Returns map: agentId → (groupId → accessLevel)
   * Used internally for resolving user-wide levels.
   */
  async getAllAgentPermissions(): Promise<Map<string, Map<string, AccessLevel>>> {
    if (this.agentPermsCache && !this.isExpired(this.agentPermsCache)) {
      return this.agentPermsCache.data;
    }
    const { rows } = await this.pool.query<{ agent_id: string; group_id: string; access_level: AccessLevel }>(
      `SELECT agent_id, group_id, access_level FROM agent_group_access`
    );
    const map = new Map<string, Map<string, AccessLevel>>();
    for (const r of rows) {
      if (!map.has(r.agent_id)) map.set(r.agent_id, new Map());
      map.get(r.agent_id)!.set(r.group_id, r.access_level);
    }
    this.agentPermsCache = { data: map, expires: this.now() + this.TTL_MS };
    return map;
  }

  /** Get user's highest access level for a specific agent. Returns null if no access. */
  async getUserAgentLevel(userEmail: string, agentId: string): Promise<AccessLevel | null> {
    const userGroups = await this.getUserGroups(userEmail);
    if (userGroups.length === 0) return null;

    const allPerms = await this.getAllAgentPermissions();
    const agentPerms = allPerms.get(agentId);
    if (!agentPerms) return null;

    let best: AccessLevel | null = null;
    for (const groupId of userGroups) {
      const level = agentPerms.get(groupId);
      if (!level) continue;
      if (!best || ACCESS_RANK[level] > ACCESS_RANK[best]) {
        best = level;
      }
    }
    return best;
  }

  /** Get user's access levels for all agents they can access (level > no_access). */
  async getUserAgentLevels(userEmail: string): Promise<Record<string, AccessLevel>> {
    const userGroups = await this.getUserGroups(userEmail);
    if (userGroups.length === 0) return {};

    const allPerms = await this.getAllAgentPermissions();
    const result: Record<string, AccessLevel> = {};

    for (const [agentId, groupPerms] of allPerms) {
      let best: AccessLevel | null = null;
      for (const groupId of userGroups) {
        const level = groupPerms.get(groupId);
        if (!level) continue;
        if (!best || ACCESS_RANK[level] > ACCESS_RANK[best]) {
          best = level;
        }
      }
      if (best) result[agentId] = best;
    }
    return result;
  }

  async setAgentPermission(groupId: string, agentId: string, level: AccessLevel): Promise<void> {
    await this.pool.query(
      `INSERT INTO agent_group_access (group_id, agent_id, access_level)
       VALUES ($1, $2, $3)
       ON CONFLICT (agent_id, group_id)
       DO UPDATE SET access_level = $3, updated_at = now()`,
      [groupId, agentId, level]
    );
    this.invalidate();
  }

  async removeAgentPermission(groupId: string, agentId: string): Promise<void> {
    await this.pool.query(
      `DELETE FROM agent_group_access WHERE group_id = $1 AND agent_id = $2`,
      [groupId, agentId]
    );
    this.invalidate();
  }

  // ─── Dashboard Permissions ─────────────────────────────────────────────────

  async getAllDashboardPermissions(): Promise<Map<string, Map<string, boolean>>> {
    if (this.dashboardPermsCache && !this.isExpired(this.dashboardPermsCache)) {
      return this.dashboardPermsCache.data;
    }
    const { rows } = await this.pool.query<{ group_id: string; dashboard: string; allowed: boolean }>(
      `SELECT group_id, dashboard, allowed FROM group_dashboard_permissions`
    );
    const map = new Map<string, Map<string, boolean>>();
    for (const r of rows) {
      if (!map.has(r.group_id)) map.set(r.group_id, new Map());
      map.get(r.group_id)!.set(r.dashboard, r.allowed);
    }
    this.dashboardPermsCache = { data: map, expires: this.now() + this.TTL_MS };
    return map;
  }

  /** Get dashboards user can access (allowed = true). */
  async getUserDashboardAccess(userEmail: string): Promise<Record<string, boolean>> {
    const userGroups = await this.getUserGroups(userEmail);
    const allPerms = await this.getAllDashboardPermissions();
    const result: Record<string, boolean> = {};

    for (const groupId of userGroups) {
      const groupPerms = allPerms.get(groupId);
      if (!groupPerms) continue;
      for (const [dashboard, allowed] of groupPerms) {
        // Once a group allows it, it stays allowed
        if (allowed) result[dashboard] = true;
      }
    }
    return result;
  }

  async setDashboardPermission(groupId: string, dashboard: string, allowed: boolean): Promise<void> {
    await this.pool.query(
      `INSERT INTO group_dashboard_permissions (group_id, dashboard, allowed)
       VALUES ($1, $2, $3)
       ON CONFLICT (group_id, dashboard)
       DO UPDATE SET allowed = $3, updated_at = now()`,
      [groupId, dashboard, allowed]
    );
    this.invalidate();
  }

  async removeDashboardPermission(groupId: string, dashboard: string): Promise<void> {
    await this.pool.query(
      `DELETE FROM group_dashboard_permissions WHERE group_id = $1 AND dashboard = $2`,
      [groupId, dashboard]
    );
    this.invalidate();
  }

  // ─── Invalidate ────────────────────────────────────────────────────────────

  invalidate(): void {
    this.groupsCache = null;
    this.userGroupsCache = null;
    this.agentPermsCache = null;
    this.dashboardPermsCache = null;
  }
}
