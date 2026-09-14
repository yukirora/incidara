import type pg from "pg";

export interface Session {
  id: number;
  gateway_session_id: string;
  agent_id: string;
  owner_email: string;
  title: string | null;
  shared_with_groups: string[];
  created_at: Date;
}

export interface CreateSessionInput {
  gatewaySessionId: string;
  agentId: string;
  ownerEmail: string;
  title?: string;
}

export interface UpdateSharingInput {
  shared_with_groups?: string[];
}

export interface ListVisibleInput {
  email: string;
  groups: string[];
  isAdmin?: boolean;
  accessibleAgentIds?: string[];
  agentId?: string;
  scope?: "mine" | "shared" | "all";
}

export class SessionStore {
  constructor(private readonly pool: pg.Pool) {}

  async create(input: CreateSessionInput): Promise<Session> {
    const { rows } = await this.pool.query<Session>(
      `INSERT INTO sessions (gateway_session_id, agent_id, owner_email, title)
       VALUES ($1, $2, $3, $4)
       RETURNING *`,
      [input.gatewaySessionId, input.agentId, input.ownerEmail, input.title ?? null]
    );
    return rows[0];
  }

  async getById(id: number): Promise<Session | null> {
    const { rows } = await this.pool.query<Session>(
      `SELECT * FROM sessions WHERE id = $1`,
      [id]
    );
    return rows[0] ?? null;
  }

  async getByGatewayId(gatewaySessionId: string): Promise<Session | null> {
    const { rows } = await this.pool.query<Session>(
      `SELECT * FROM sessions WHERE gateway_session_id = $1`,
      [gatewaySessionId]
    );
    return rows[0] ?? null;
  }

  async listVisible(input: ListVisibleInput): Promise<Session[]> {
    const { email, groups, isAdmin, accessibleAgentIds, agentId, scope } = input;

    const conditions: string[] = [];
    const params: unknown[] = [];
    let paramIdx = 1;

    if (isAdmin) {
      // Admins see everything — only filter by scope if explicitly "mine"
      if (scope === "mine") {
        params.push(email);
        conditions.push(`owner_email = $${paramIdx}`);
        paramIdx++;
      }
      // "all" or "shared" → no owner filter for admins
    } else {
      if (scope === "mine") {
        params.push(email);
        conditions.push(`owner_email = $${paramIdx}`);
        paramIdx++;
      } else if (scope === "shared") {
        // Sessions on agents I can access, but I don't own
        const sharedConds: string[] = [];
        params.push(email);
        sharedConds.push(`owner_email != $${paramIdx}`);
        paramIdx++;

        if (accessibleAgentIds && accessibleAgentIds.length > 0) {
          params.push(accessibleAgentIds);
          sharedConds.push(`agent_id = ANY($${paramIdx}::text[])`);
          paramIdx++;
        }
        conditions.push(`(${sharedConds.join(" AND ")})`);
      } else {
        // "all" or default: own sessions OR sessions on accessible agents
        const orParts: string[] = [];

        // Own sessions
        params.push(email);
        orParts.push(`owner_email = $${paramIdx}`);
        paramIdx++;

        // Session shared with my groups
        if (groups.length > 0) {
          params.push(groups);
          orParts.push(`shared_with_groups && $${paramIdx}::text[]`);
          paramIdx++;
        }

        // Agent access: sessions on agents I can access
        if (accessibleAgentIds && accessibleAgentIds.length > 0) {
          params.push(accessibleAgentIds);
          orParts.push(`agent_id = ANY($${paramIdx}::text[])`);
          paramIdx++;
        }

        conditions.push(`(${orParts.join(" OR ")})`);
      }
    }

    if (agentId) {
      params.push(agentId);
      conditions.push(`agent_id = $${paramIdx}`);
      paramIdx++;
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const { rows } = await this.pool.query<Session>(
      `SELECT * FROM sessions ${where} ORDER BY created_at DESC`,
      params
    );
    return rows;
  }

  async updateTitle(id: number, title: string): Promise<Session | null> {
    const { rows } = await this.pool.query<Session>(
      `UPDATE sessions SET title = $1 WHERE id = $2 RETURNING *`,
      [title, id]
    );
    return rows[0] ?? null;
  }

  async updateSharing(id: number, input: UpdateSharingInput): Promise<Session | null> {
    const setClauses: string[] = [];
    const params: unknown[] = [];
    let idx = 1;

    if (input.shared_with_groups !== undefined) {
      setClauses.push(`shared_with_groups = $${idx}`);
      params.push(input.shared_with_groups);
      idx++;
    }

    if (setClauses.length === 0) {
      return this.getById(id);
    }

    params.push(id);
    const { rows } = await this.pool.query<Session>(
      `UPDATE sessions SET ${setClauses.join(", ")} WHERE id = $${idx} RETURNING *`,
      params
    );
    return rows[0] ?? null;
  }

  async delete(id: number): Promise<void> {
    await this.pool.query(`DELETE FROM sessions WHERE id = $1`, [id]);
  }
}
