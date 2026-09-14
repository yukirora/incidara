import type pg from "pg";
import { CronExpressionParser } from "cron-parser";

export type TriggerType = "once" | "interval" | "cron";
export type SessionMode = "new" | "reuse";

export type Schedule = {
  id: number;
  name: string | null;
  agent_id: string;
  owner_email: string;
  prompt: string;
  trigger_type: TriggerType;
  run_at: Date | null;
  interval_seconds: number | null;
  cron_expr: string | null;
  timezone: string;
  session_mode: SessionMode;
  reuse_session_id: number | null;
  completion_mode: "auto" | "manual";
  enabled: boolean;
  last_fired_at: Date | null;
  next_fire_at: Date | null;
  created_at: Date;
};

export type CreateScheduleInput = {
  name?: string;
  agentId: string;
  ownerEmail: string;
  prompt: string;
  triggerType: TriggerType;
  runAt?: Date;
  intervalSeconds?: number;
  cronExpr?: string;
  timezone?: string;
  sessionMode: SessionMode;
  reuseSessionId?: number;
  completionMode?: "auto" | "manual";
};

export type UpdateSchedulePatch = {
  name?: string | null;
  prompt?: string;
  triggerType?: TriggerType;
  runAt?: Date | null;
  intervalSeconds?: number | null;
  cronExpr?: string | null;
  timezone?: string;
  sessionMode?: SessionMode;
  reuseSessionId?: number | null;
  completionMode?: "auto" | "manual";
  enabled?: boolean;
};

export interface ComputeNextFireInput {
  triggerType: TriggerType;
  runAt?: Date | null;
  intervalSeconds?: number | null;
  cronExpr?: string | null;
  timezone?: string | null;
  from: Date;
}

/**
 * Compute the next fire date for a schedule trigger.
 * Returns null when the schedule is permanently done (e.g. 'once' with runAt in the past).
 */
export function computeNextFire(input: ComputeNextFireInput): Date | null {
  const { triggerType, runAt, intervalSeconds, cronExpr, timezone, from } = input;
  const tz = timezone ?? "UTC";

  switch (triggerType) {
    case "once": {
      if (!runAt) throw new Error("trigger_type='once' requires run_at");
      return runAt > from ? runAt : null;
    }
    case "interval": {
      if (!intervalSeconds || intervalSeconds < 1) {
        throw new Error("trigger_type='interval' requires interval_seconds >= 1");
      }
      return new Date(from.getTime() + intervalSeconds * 1000);
    }
    case "cron": {
      if (!cronExpr) throw new Error("trigger_type='cron' requires cron_expr");
      try {
        const expr = CronExpressionParser.parse(cronExpr, { tz, currentDate: from });
        return expr.next().toDate();
      } catch (err) {
        throw new Error(`Invalid cron expression '${cronExpr}': ${(err as Error).message}`);
      }
    }
    default:
      throw new Error(`Unknown trigger_type: ${String(triggerType)}`);
  }
}

export class ScheduleStore {
  constructor(private readonly pool: pg.Pool) {}

  async create(input: CreateScheduleInput): Promise<Schedule> {
    const now = new Date();
    const tz = input.timezone ?? "UTC";

    const nextFireAt = computeNextFire({
      triggerType: input.triggerType,
      runAt: input.runAt,
      intervalSeconds: input.intervalSeconds,
      cronExpr: input.cronExpr,
      timezone: tz,
      from: now,
    });

    const { rows } = await this.pool.query<Schedule>(
      `INSERT INTO schedules
         (name, agent_id, owner_email, prompt, trigger_type, run_at, interval_seconds,
          cron_expr, timezone, session_mode, reuse_session_id, completion_mode, enabled, next_fire_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, true, $13)
       RETURNING *`,
      [
        input.name ?? null,
        input.agentId,
        input.ownerEmail,
        input.prompt,
        input.triggerType,
        input.runAt ?? null,
        input.intervalSeconds ?? null,
        input.cronExpr ?? null,
        tz,
        input.sessionMode,
        input.reuseSessionId ?? null,
        input.completionMode ?? "auto",
        nextFireAt,
      ]
    );
    return rows[0];
  }

  async getById(id: number): Promise<Schedule | null> {
    const { rows } = await this.pool.query<Schedule>(
      `SELECT * FROM schedules WHERE id = $1`,
      [id]
    );
    return rows[0] ?? null;
  }

  async list(input: {
    ownerEmail?: string;
    agentId?: string;
    enabled?: boolean;
  }): Promise<Schedule[]> {
    const conditions: string[] = [];
    const params: unknown[] = [];
    let paramIdx = 1;

    if (input.ownerEmail) {
      params.push(input.ownerEmail);
      conditions.push(`owner_email = $${paramIdx}`);
      paramIdx++;
    }

    if (input.agentId !== undefined) {
      conditions.push(`agent_id = $${paramIdx}`);
      params.push(input.agentId);
      paramIdx++;
    }
    if (input.enabled !== undefined) {
      conditions.push(`enabled = $${paramIdx}`);
      params.push(input.enabled);
      paramIdx++;
    }

    const where = conditions.length > 0 ? conditions.join(" AND ") : "1=1";
    const { rows } = await this.pool.query<Schedule>(
      `SELECT * FROM schedules WHERE ${where} ORDER BY created_at DESC`,
      params
    );
    return rows;
  }

  async update(id: number, patch: UpdateSchedulePatch): Promise<Schedule | null> {
    const current = await this.getById(id);
    if (!current) return null;

    const setClauses: string[] = [];
    const params: unknown[] = [];
    let idx = 1;

    // Track whether trigger fields changed so we can recompute next_fire_at
    let triggerChanged = false;

    if (patch.name !== undefined) {
      setClauses.push(`name = $${idx}`);
      params.push(patch.name);
      idx++;
    }
    if (patch.prompt !== undefined) {
      setClauses.push(`prompt = $${idx}`);
      params.push(patch.prompt);
      idx++;
    }
    if (patch.triggerType !== undefined) {
      setClauses.push(`trigger_type = $${idx}`);
      params.push(patch.triggerType);
      idx++;
      triggerChanged = true;
    }
    if (patch.runAt !== undefined) {
      setClauses.push(`run_at = $${idx}`);
      params.push(patch.runAt);
      idx++;
      triggerChanged = true;
    }
    if (patch.intervalSeconds !== undefined) {
      setClauses.push(`interval_seconds = $${idx}`);
      params.push(patch.intervalSeconds);
      idx++;
      triggerChanged = true;
    }
    if (patch.cronExpr !== undefined) {
      setClauses.push(`cron_expr = $${idx}`);
      params.push(patch.cronExpr);
      idx++;
      triggerChanged = true;
    }
    if (patch.timezone !== undefined) {
      setClauses.push(`timezone = $${idx}`);
      params.push(patch.timezone);
      idx++;
      triggerChanged = true;
    }
    if (patch.sessionMode !== undefined) {
      setClauses.push(`session_mode = $${idx}`);
      params.push(patch.sessionMode);
      idx++;
    }
    if (patch.reuseSessionId !== undefined) {
      setClauses.push(`reuse_session_id = $${idx}`);
      params.push(patch.reuseSessionId);
      idx++;
    }
    if (patch.completionMode !== undefined) {
      setClauses.push(`completion_mode = $${idx}`);
      params.push(patch.completionMode);
      idx++;
    }
    if (patch.enabled !== undefined) {
      setClauses.push(`enabled = $${idx}`);
      params.push(patch.enabled);
      idx++;
    }

    if (triggerChanged) {
      // Recompute next_fire_at from now using merged trigger fields
      const merged = {
        triggerType: patch.triggerType ?? current.trigger_type,
        runAt: patch.runAt !== undefined ? patch.runAt : current.run_at,
        intervalSeconds: patch.intervalSeconds !== undefined ? patch.intervalSeconds : current.interval_seconds,
        cronExpr: patch.cronExpr !== undefined ? patch.cronExpr : current.cron_expr,
        timezone: patch.timezone ?? current.timezone,
        from: new Date(),
      };
      const nextFireAt = computeNextFire(merged);
      setClauses.push(`next_fire_at = $${idx}`);
      params.push(nextFireAt);
      idx++;
    }

    if (setClauses.length === 0) return current;

    params.push(id);
    const { rows } = await this.pool.query<Schedule>(
      `UPDATE schedules SET ${setClauses.join(", ")} WHERE id = $${idx} RETURNING *`,
      params
    );
    return rows[0] ?? null;
  }

  async delete(id: number): Promise<void> {
    await this.pool.query(`DELETE FROM schedules WHERE id = $1`, [id]);
  }

  /**
   * Return schedules that are due to fire (enabled AND next_fire_at <= now).
   * For MVP with a single server instance, a simple SELECT is sufficient.
   * TODO: For multi-instance deployments, use SELECT ... FOR UPDATE SKIP LOCKED
   * within a transaction to prevent double-firing.
   */
  async claimDue(input: { now: Date; limit?: number }): Promise<Schedule[]> {
    const { now, limit = 10 } = input;
    const { rows } = await this.pool.query<Schedule>(
      `SELECT * FROM schedules
       WHERE enabled = true AND next_fire_at <= $1
       ORDER BY next_fire_at ASC
       LIMIT $2`,
      [now, limit]
    );
    return rows;
  }

  async recordFiring(
    id: number,
    input: { firedAt: Date; nextFireAt: Date | null; disableIfDone?: boolean }
  ): Promise<Schedule | null> {
    const { firedAt, nextFireAt, disableIfDone } = input;

    let query: string;
    let params: unknown[];

    if (disableIfDone && nextFireAt === null) {
      query = `UPDATE schedules
               SET last_fired_at = $1, next_fire_at = NULL, enabled = false
               WHERE id = $2
               RETURNING *`;
      params = [firedAt, id];
    } else {
      query = `UPDATE schedules
               SET last_fired_at = $1, next_fire_at = $2
               WHERE id = $3
               RETURNING *`;
      params = [firedAt, nextFireAt, id];
    }

    const { rows } = await this.pool.query<Schedule>(query, params);
    return rows[0] ?? null;
  }
}
