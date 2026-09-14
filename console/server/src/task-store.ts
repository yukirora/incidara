import type pg from "pg";

export type TaskStatus =
  | "pending"
  | "running"
  | "busy"
  | "waiting_input"
  | "pending_approval"
  | "approved"
  | "completed"
  | "error"
  | "interrupted"
  | "cancelled";

export const TERMINAL_STATUSES: TaskStatus[] = [
  "completed",
  "error",
  "interrupted",
  "cancelled",
];

export const ACTIVE_STATUSES: TaskStatus[] = [
  "running",
  "busy",
  "waiting_input",
  "pending_approval",
];

export interface Task {
  id: number;
  session_id: number;
  prompt: string;
  submitter_email: string;
  status: TaskStatus;
  created_at: Date;
  started_at: Date | null;
  ended_at: Date | null;
  output_preview: string | null;
  output: string | null;
  gateway_seq_start: number | null;
  from_schedule_id: number | null;
  title: string | null;
  completion_mode: 'auto' | 'manual';
  parent_task_id: number | null;
  // Feature 1 (Autonomy Graduation) — new columns
  ticket_id: string | null;
  model_version: string | null;
  human_decision: 'approved' | 'overridden' | 'declined' | null;
  trace_path: string | null;
  // Feature 2 (Memory) — ground truth + matching
  ground_truth: string | null;
  signature_hash: string | null;
}

export interface CreateTaskInput {
  sessionId: number;
  prompt: string;
  submitterEmail: string;
  status: "pending" | "running";
  fromScheduleId?: number;
  title?: string;
  completionMode?: 'auto' | 'manual';
  parentTaskId?: number | null;
}

export interface ListTasksInput {
  email: string;
  groups: string[];
  isAdmin?: boolean;
  accessibleAgentIds?: string[];
  status?: TaskStatus;
  statuses?: string[];
  agentId?: string;
  sessionId?: number;
  scope?: "mine" | "shared" | "all";
  titleSearch?: string;
  limit: number;
  offset: number;
}

export interface ListTasksResult {
  tasks: (Task & { agent_id: string; gateway_session_id: string })[];
  total: number;
}

export interface CountByStatusResult {
  pending: number;
  running: number;
  waiting_input: number;
  completed_today: number;
}

export class TaskStore {
  constructor(private readonly pool: pg.Pool) {}

  async create(input: CreateTaskInput): Promise<Task> {
    const startedAt = input.status === "running" ? new Date() : null;
    const completionMode = input.completionMode ?? 'auto';
    const { rows } = await this.pool.query<Task>(
      `INSERT INTO tasks (session_id, prompt, submitter_email, status, started_at, from_schedule_id, title, completion_mode, parent_task_id)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
       RETURNING *`,
      [
        input.sessionId,
        input.prompt,
        input.submitterEmail,
        input.status,
        startedAt,
        input.fromScheduleId ?? null,
        input.title ?? null,
        completionMode,
        input.parentTaskId ?? null,
      ]
    );
    return rows[0];
  }

  async completeManually(id: number): Promise<Task | null> {
    const { rows } = await this.pool.query<Task>(
      `UPDATE tasks SET status = 'completed', ended_at = now()
       WHERE id = $1 AND status IN ('running', 'busy', 'waiting_input', 'interrupted')
       RETURNING *`,
      [id]
    );
    return rows[0] ?? null;
  }

  async getById(id: number): Promise<Task | null> {
    const { rows } = await this.pool.query<Task>(
      `SELECT * FROM tasks WHERE id = $1`,
      [id]
    );
    return rows[0] ?? null;
  }

  async latestForSession(sessionId: number): Promise<Task | null> {
    const { rows } = await this.pool.query<Task>(
      `SELECT * FROM tasks WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1`,
      [sessionId]
    );
    return rows[0] ?? null;
  }

  async allForSession(sessionId: number): Promise<Task[]> {
    const { rows } = await this.pool.query<Task>(
      `SELECT * FROM tasks WHERE session_id = $1 ORDER BY created_at ASC`,
      [sessionId]
    );
    return rows;
  }

  async latestActiveForSession(sessionId: number): Promise<Task | null> {
    const { rows } = await this.pool.query<Task>(
      `SELECT * FROM tasks WHERE session_id = $1 AND status IN ('running','busy','waiting_input') ORDER BY created_at DESC LIMIT 1`,
      [sessionId]
    );
    return rows[0] ?? null;
  }

  async reopenLatestForSession(sessionId: number): Promise<Task | null> {
    const latest = await this.latestForSession(sessionId);
    if (!latest) return null;
    if (ACTIVE_STATUSES.includes(latest.status)) return latest;

    const { rows } = await this.pool.query<Task>(
      `UPDATE tasks
       SET status = 'running',
           started_at = COALESCE(started_at, now()),
           ended_at = NULL
       WHERE id = $1 AND status IN ('completed', 'error', 'interrupted', 'cancelled')
       RETURNING *`,
      [latest.id]
    );
    return rows[0] ?? latest;
  }

  async list(input: ListTasksInput): Promise<ListTasksResult> {
    const { email, groups, isAdmin, accessibleAgentIds, status, agentId, sessionId, scope, limit, offset } = input;

    const conditions: string[] = [];
    const params: unknown[] = [];
    let paramIdx = 1;

    // Visibility: agent access gates session/task visibility
    if (isAdmin) {
      // Admins see all tasks — only filter by scope if explicitly "mine"
      if (scope === "mine") {
        params.push(email);
        conditions.push(`t.submitter_email = $${paramIdx}`);
        paramIdx++;
      }
    } else if (scope === "mine") {
      params.push(email);
      conditions.push(`t.submitter_email = $${paramIdx}`);
      paramIdx++;
    } else if (scope === "shared") {
      // "shared" = tasks in sessions on agents I can access (but I don't own)
      const ownConds: string[] = [];
      params.push(email);
      ownConds.push(`t.submitter_email != $${paramIdx}`);
      paramIdx++;

      if (accessibleAgentIds && accessibleAgentIds.length > 0) {
        params.push(accessibleAgentIds);
        ownConds.push(`s.agent_id = ANY($${paramIdx}::text[])`);
        paramIdx++;
      }
      conditions.push(`(${ownConds.join(" AND ")})`);
    } else {
      // "all" or default: own tasks OR tasks on agents I can access
      const orParts: string[] = [];

      // Own tasks
      params.push(email);
      orParts.push(`s.owner_email = $${paramIdx}`);
      paramIdx++;

      // Session shared with my groups
      if (groups.length > 0) {
        params.push(groups);
        orParts.push(`s.shared_with_groups && $${paramIdx}::text[]`);
        paramIdx++;
      }

      // Agent access: can see tasks on agents I can access
      if (accessibleAgentIds && accessibleAgentIds.length > 0) {
        params.push(accessibleAgentIds);
        orParts.push(`s.agent_id = ANY($${paramIdx}::text[])`);
        paramIdx++;
      }

      conditions.push(`(${orParts.join(" OR ")})`);
    }

    if (status) {
      conditions.push(`t.status = $${paramIdx}`);
      params.push(status);
      paramIdx++;
    }

    if (input.statuses && input.statuses.length > 0) {
      conditions.push(`t.status = ANY($${paramIdx}::text[])`);
      params.push(input.statuses);
      paramIdx++;
    }

    if (agentId) {
      conditions.push(`s.agent_id = $${paramIdx}`);
      params.push(agentId);
      paramIdx++;
    }

    if (sessionId !== undefined) {
      conditions.push(`t.session_id = $${paramIdx}`);
      params.push(sessionId);
      paramIdx++;
    }

    if (input.titleSearch) {
      conditions.push(`(t.title ILIKE $${paramIdx} OR t.prompt ILIKE $${paramIdx})`);
      params.push(`%${input.titleSearch}%`);
      paramIdx++;
    }

    const where = conditions.length > 0 ? conditions.join(" AND ") : "1=1";

    // Count query
    const countResult = await this.pool.query<{ count: string }>(
      `SELECT COUNT(*) as count
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       WHERE ${where}`,
      params
    );
    const total = parseInt(countResult.rows[0].count, 10);

    // Data query
    params.push(limit, offset);
    const { rows } = await this.pool.query<Task & { agent_id: string; gateway_session_id: string }>(
      `SELECT t.*, s.agent_id, s.gateway_session_id
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       WHERE ${where}
       ORDER BY t.created_at DESC
       LIMIT $${paramIdx} OFFSET $${paramIdx + 1}`,
      params
    );

    return { tasks: rows, total };
  }

  async updateStatus(id: number, status: TaskStatus, endedAt?: Date): Promise<Task | null> {
    // Protect against late SSE events: if current status is terminal and new status
    // is running/waiting_input, skip — UNLESS the new status is an explicit recovery.
    // Valid recovery paths:
    //   error → running/waiting_input  (adapter auto-compacted and resumed)
    //   completed → running            (session resumed after prior session.completed)
    const current = await this.getById(id);
    if (current && TERMINAL_STATUSES.includes(current.status)) {
      if (status === "running" || status === "waiting_input") {
        if (current.status === "error" || current.status === "completed") {
          // Clear ended_at since the task is no longer in a terminal state
          const { rows } = await this.pool.query<Task>(
            `UPDATE tasks SET status = $1, ended_at = NULL WHERE id = $2 RETURNING *`,
            [status, id]
          );
          return rows[0] ?? null;
        }
        return current;
      }
    }

    const { rows } = await this.pool.query<Task>(
      `UPDATE tasks SET status = $1, ended_at = $2 WHERE id = $3 RETURNING *`,
      [status, endedAt ?? null, id]
    );
    return rows[0] ?? null;
  }

  async setGatewaySeqStart(id: number, seq: number): Promise<void> {
    await this.pool.query(
      "UPDATE tasks SET gateway_seq_start = $1 WHERE id = $2 AND gateway_seq_start IS NULL",
      [seq, id],
    );
  }

  async updateOutputPreview(id: number, text: string): Promise<Task | null> {
    // Skip if already in a truly terminal state (completed/interrupted/cancelled),
    // but allow updates when in "error" state since the adapter may auto-recover
    // (e.g. compact+resume) and we want to capture the successful response.
    const current = await this.getById(id);
    if (current && current.status !== "error" && TERMINAL_STATUSES.includes(current.status)) {
      return current;
    }

    const truncated = text.slice(0, 200);
    const { rows } = await this.pool.query<Task>(
      `UPDATE tasks SET output_preview = $1 WHERE id = $2 RETURNING *`,
      [truncated, id]
    );
    return rows[0] ?? null;
  }

  // ── Feature 1: Autonomy Graduation ──────────────────────────

  /** Record human approval/override decision on a pending_approval task.
   *  First decision wins — once overridden, stays overridden regardless of final outcome. */
  async recordHumanDecision(
    id: number,
    decision: "approved" | "overridden" | "declined"
  ): Promise<Task | null> {
    const newStatus = decision === "approved" ? "approved" : (decision === "declined" ? "cancelled" : "waiting_input");
    const { rows } = await this.pool.query<Task>(
      `UPDATE tasks
       SET human_decision = COALESCE(human_decision, $1),
           status = $2,
           ended_at = CASE WHEN $2 IN ('approved','cancelled') THEN now() ELSE ended_at END
       WHERE id = $3 AND status = 'pending_approval'
       RETURNING *`,
      [decision, newStatus, id]
    );
    return rows[0] ?? null;
  }

  /** Store full agent output (separate from 200-char preview). */
  async updateFullOutput(id: number, fullText: string): Promise<void> {
    await this.pool.query(
      `UPDATE tasks SET output = $1 WHERE id = $2`,
      [fullText, id]
    );
  }

  /** Store model version for this task (used by L-level metrics). */
  async setModelVersion(id: number, version: string): Promise<void> {
    await this.pool.query(
      `UPDATE tasks SET model_version = $1 WHERE id = $2 AND model_version IS NULL`,
      [version, id]
    );
  }

  /** Auto-detect human decision from transcript content (non-C2 tasks).
   *  Only sets if human_decision is NULL — first decision wins.
   *  Works regardless of current task status (unlike recordHumanDecision). */
  async autoSetHumanDecision(
    id: number,
    decision: "approved" | "overridden" | "declined"
  ): Promise<void> {
    await this.pool.query(
      `UPDATE tasks SET human_decision = $1 WHERE id = $2 AND human_decision IS NULL`,
      [decision, id]
    );
  }

  /** Store alert signature hash for memory matching (Feature 2 B6). */
  async setSignatureHash(id: number, hash: string): Promise<void> {
    await this.pool.query(
      `UPDATE tasks SET signature_hash = $1 WHERE id = $2`,
      [hash, id]
    );
  }

  /** Increment tool_error_count for a task (tool call failed during execution). */
  async incrementToolError(id: number): Promise<void> {
    await this.pool.query(
      `UPDATE tasks SET tool_error_count = tool_error_count + 1 WHERE id = $1`,
      [id]
    );
  }

  /** Increment llm_error_count for a task (LLM API error during execution). */
  async incrementLlmError(id: number): Promise<void> {
    await this.pool.query(
      `UPDATE tasks SET llm_error_count = llm_error_count + 1 WHERE id = $1`,
      [id]
    );
  }

  async cancelPending(id: number): Promise<number> {
    const { rowCount } = await this.pool.query(
      `UPDATE tasks SET status = 'cancelled', ended_at = now() WHERE id = $1 AND status = 'pending'`,
      [id]
    );
    return rowCount ?? 0;
  }

  async promoteNextPending(sessionId: number): Promise<Task | null> {
    const { rows } = await this.pool.query<Task>(
      `UPDATE tasks
       SET status = 'running', started_at = now()
       WHERE id = (
         SELECT id FROM tasks
         WHERE session_id = $1 AND status = 'pending'
         ORDER BY id ASC LIMIT 1
         FOR UPDATE SKIP LOCKED
       )
       RETURNING *`,
      [sessionId]
    );
    return rows[0] ?? null;
  }

  async sessionsWithPendingAndNoActive(): Promise<number[]> {
    const { rows } = await this.pool.query<{ session_id: number }>(
      `SELECT DISTINCT session_id
       FROM tasks t
       WHERE status = 'pending'
         AND NOT EXISTS (
           SELECT 1 FROM tasks
           WHERE session_id = t.session_id
             AND status IN ('running', 'busy', 'waiting_input')
         )`
    );
    return rows.map((r) => r.session_id);
  }

  async countByStatus(input: {
    email: string;
    groups: string[];
    isAdmin?: boolean;
    accessibleAgentIds?: string[];
    agentId?: string;
  }): Promise<CountByStatusResult> {
    const { email, groups, isAdmin, accessibleAgentIds, agentId } = input;

    const conditions: string[] = [];
    const params: unknown[] = [];
    let paramIdx = 1;

    if (isAdmin) {
      // No owner filter for admins
    } else {
      const orParts: string[] = [];
      params.push(email);
      orParts.push(`s.owner_email = $${paramIdx}`);
      paramIdx++;

      if (groups.length > 0) {
        params.push(groups);
        orParts.push(`s.shared_with_groups && $${paramIdx}::text[]`);
        paramIdx++;
      }

      if (accessibleAgentIds && accessibleAgentIds.length > 0) {
        params.push(accessibleAgentIds);
        orParts.push(`s.agent_id = ANY($${paramIdx}::text[])`);
        paramIdx++;
      }

      conditions.push(`(${orParts.join(" OR ")})`);
    }

    if (agentId) {
      conditions.push(`s.agent_id = $${paramIdx}`);
      params.push(agentId);
      paramIdx++;
    }

    const where = conditions.join(" AND ");

    const { rows } = await this.pool.query<{ status: string; count: string }>(
      `SELECT t.status, COUNT(*) as count
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       WHERE ${where}
         AND (
           t.status IN ('pending', 'running', 'waiting_input')
           OR (t.status = 'completed' AND t.ended_at >= now() - interval '1 day')
         )
       GROUP BY t.status`,
      params
    );

    const result: CountByStatusResult = {
      pending: 0,
      running: 0,
      waiting_input: 0,
      completed_today: 0,
    };

    for (const row of rows) {
      const count = parseInt(row.count, 10);
      if (row.status === "pending") result.pending = count;
      else if (row.status === "running") result.running = count;
      else if (row.status === "waiting_input") result.waiting_input = count;
      else if (row.status === "completed") result.completed_today = count;
    }

    return result;
  }
}
