import { Pool } from "pg";
import type { PricingStore } from "./pricing.js";

interface ModelUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  cost_usd: number;
}

export interface TaskUsageRow {
  task_id: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_input_tokens: number;
  total_cache_read_input_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  turns: number;
  models: string[];
  calculated_at: string;
  model_usage?: ModelUsage[];
}

export interface AgentUsageSummary {
  agent_id: string;
  total_tasks: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_input_tokens: number;
  total_cache_read_input_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  total_turns: number;
  models: string[];
  model_usage: { model: string; input_tokens: number; output_tokens: number; cache_creation_input_tokens: number; cache_read_input_tokens: number; tokens: number; cost_usd: number }[];
  daily_spend: { date: string; cost_usd: number; input_tokens: number; output_tokens: number; cache_creation_input_tokens: number; cache_read_input_tokens: number; by_model: { model: string; cost_usd: number }[] }[];
}

import type { GatewayClient } from "./gateway-client.js";

export class UsageStore {
  constructor(
    private pool: Pool,
    private pricingStore: PricingStore,
    private _gatewayClients?: Map<string, GatewayClient>,
  ) {}

  setGatewayClients(clients: Map<string, GatewayClient>) {
    this._gatewayClients = clients;
  }

  /**
   * Upsert usage data for a task from gateway usage response.
   */
  async upsertTaskUsage(
    taskId: number,
    usage: {
      turns: number;
      total_input_tokens: number;
      total_output_tokens: number;
      total_cache_creation_input_tokens: number;
      total_cache_read_input_tokens: number;
      total_tokens: number;
      models: Record<string, {
        model: string;
        input_tokens: number;
        output_tokens: number;
        cache_creation_input_tokens: number;
        cache_read_input_tokens: number;
      }>;
    },
  ): Promise<TaskUsageRow> {
    const modelNames = Object.keys(usage.models);
    let totalCost = 0;

    // Calculate total cost
    for (const m of Object.values(usage.models)) {
      totalCost += await this.pricingStore.calculateCost(m.model, m);
    }

    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");

      // Upsert main row
      await client.query(
        `INSERT INTO task_usage (task_id, total_input_tokens, total_output_tokens, total_cache_creation_input_tokens, total_cache_read_input_tokens, total_tokens, total_cost_usd, turns, models, updated_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
         ON CONFLICT (task_id) DO UPDATE SET
           total_input_tokens = EXCLUDED.total_input_tokens,
           total_output_tokens = EXCLUDED.total_output_tokens,
           total_cache_creation_input_tokens = EXCLUDED.total_cache_creation_input_tokens,
           total_cache_read_input_tokens = EXCLUDED.total_cache_read_input_tokens,
           total_tokens = EXCLUDED.total_tokens,
           total_cost_usd = EXCLUDED.total_cost_usd,
           turns = EXCLUDED.turns,
           models = EXCLUDED.models,
           updated_at = NOW()`,
        [taskId, usage.total_input_tokens, usage.total_output_tokens,
         usage.total_cache_creation_input_tokens, usage.total_cache_read_input_tokens,
         usage.total_tokens, totalCost, usage.turns, modelNames],
      );

      // Upsert per-model rows
      for (const m of Object.values(usage.models)) {
        const modelCost = await this.pricingStore.calculateCost(m.model, m);
        await client.query(
          `INSERT INTO task_model_usage (task_id, model, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, cost_usd)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (task_id, model) DO UPDATE SET
             input_tokens = EXCLUDED.input_tokens,
             output_tokens = EXCLUDED.output_tokens,
             cache_creation_input_tokens = EXCLUDED.cache_creation_input_tokens,
             cache_read_input_tokens = EXCLUDED.cache_read_input_tokens,
             cost_usd = EXCLUDED.cost_usd`,
          [taskId, m.model, m.input_tokens, m.output_tokens,
           m.cache_creation_input_tokens, m.cache_read_input_tokens, modelCost],
        );
      }

      // Delete stale model rows not in current models set
      if (modelNames.length > 0) {
        const placeholders = modelNames.map((_, i) => `$${i + 2}`).join(", ");
        await client.query(
          `DELETE FROM task_model_usage WHERE task_id = $1 AND model NOT IN (${placeholders})`,
          [taskId, ...modelNames],
        );
      } else {
        // No models — delete all model rows for this task
        await client.query(
          `DELETE FROM task_model_usage WHERE task_id = $1`,
          [taskId],
        );
      }

      await client.query("COMMIT");
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }

    return this.getTaskUsage(taskId) as Promise<TaskUsageRow>;
  }

  /**
   * Get usage for a single task.
   */
  async getTaskUsage(taskId: number): Promise<TaskUsageRow | null> {
    const { rows } = await this.pool.query(
      `SELECT tu.*, tm.model, tm.input_tokens as model_input, tm.output_tokens as model_output,
              tm.cache_creation_input_tokens as model_cc, tm.cache_read_input_tokens as model_cr, tm.cost_usd as model_cost
       FROM task_usage tu
       LEFT JOIN task_model_usage tm ON tm.task_id = tu.task_id
       WHERE tu.task_id = $1`,
      [taskId],
    );

    if (rows.length === 0) return null;

    const row = rows[0];
    const modelUsage: ModelUsage[] = rows
      .filter(r => r.model)
      .map(r => ({
        model: r.model,
        input_tokens: Number(r.model_input),
        output_tokens: Number(r.model_output),
        cache_creation_input_tokens: Number(r.model_cc),
        cache_read_input_tokens: Number(r.model_cr),
        cost_usd: Number(r.model_cost),
      }));

    return {
      task_id: row.task_id,
      total_input_tokens: Number(row.total_input_tokens),
      total_output_tokens: Number(row.total_output_tokens),
      total_cache_creation_input_tokens: Number(row.total_cache_creation_input_tokens),
      total_cache_read_input_tokens: Number(row.total_cache_read_input_tokens),
      total_tokens: Number(row.total_tokens),
      total_cost_usd: Number(row.total_cost_usd),
      turns: row.turns,
      models: row.models || [],
      calculated_at: row.calculated_at,
      model_usage: modelUsage,
    };
  }

  /**
   * Get usage summary for an agent in a time range.
   * Returns per-task breakdown with pagination.
   */
  async getAgentUsage(
    agentId: string,
    options: {
      startDate?: string;
      endDate?: string;
      page?: number;
      pageSize?: number;
      search?: string;
      sortBy?: string;
      sortDir?: "asc" | "desc";
    } = {},
  ): Promise<{
    tasks: (TaskUsageRow & { task_id: number; title: string | null; prompt: string; status: string; created_at: string })[];
    summary: AgentUsageSummary;
    total: number;
    page: number;
    pageSize: number;
  }> {
    const {
      startDate,
      endDate,
      page = 1,
      pageSize = 20,
      search,
      sortBy = "total_cost_usd",
      sortDir = "desc",
    } = options;

    // Build WHERE clause
    const conditions: string[] = ["s.agent_id = $1"];
    const params: any[] = [agentId];
    let paramIdx = 2;

    if (startDate) {
      conditions.push(`t.created_at >= $${paramIdx}`);
      params.push(startDate);
      paramIdx++;
    }
    if (endDate) {
      conditions.push(`t.created_at < $${paramIdx}`);
      params.push(endDate + "T23:59:59");
      paramIdx++;
    }
    if (search) {
      conditions.push(`(t.title ILIKE $${paramIdx} OR t.prompt ILIKE $${paramIdx})`);
      params.push(`%${search}%`);
      paramIdx++;
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

    // Validate sort column
    const allowedSorts = ["total_cost_usd", "total_tokens", "turns", "created_at", "total_input_tokens", "total_output_tokens"];
    const safeSortBy = allowedSorts.includes(sortBy) ? sortBy : "total_cost_usd";
    const safeSortDir = sortDir === "asc" ? "ASC" : "DESC";

    // Count total
    const countResult = await this.pool.query(
      `SELECT COUNT(DISTINCT t.id) as cnt FROM tasks t JOIN sessions s ON t.session_id = s.id ${whereClause}`,
      params,
    );
    const total = Number(countResult.rows[0]?.cnt ?? 0);

    // Fetch tasks with usage
    const offset = (page - 1) * pageSize;
    const { rows } = await this.pool.query(
      `SELECT t.id as task_id, t.session_id, t.title, t.prompt, t.status, t.created_at,
              tu.total_input_tokens, tu.total_output_tokens,
              tu.total_cache_creation_input_tokens, tu.total_cache_read_input_tokens,
              tu.total_tokens, tu.total_cost_usd, tu.turns, tu.models,
              tm.model, tm.input_tokens as model_input, tm.output_tokens as model_output,
              tm.cache_creation_input_tokens as model_cc, tm.cache_read_input_tokens as model_cr, tm.cost_usd as model_cost
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       LEFT JOIN task_usage tu ON tu.task_id = t.id
       LEFT JOIN task_model_usage tm ON tm.task_id = t.id
       ${whereClause}
       ORDER BY ${safeSortBy === "created_at" ? "t.created_at" : `COALESCE(tu.${safeSortBy}, 0)`} ${safeSortDir}
       LIMIT $${paramIdx} OFFSET $${paramIdx + 1}`,
      [...params, pageSize, offset],
    );

    // Group model usage per task
    const taskMap = new Map<number, any>();
    for (const row of rows) {
      if (!taskMap.has(row.task_id)) {
        taskMap.set(row.task_id, {
          task_id: row.task_id,
          session_id: row.session_id,
          title: row.title,
          prompt: row.prompt,
          status: row.status,
          created_at: row.created_at,
          total_input_tokens: Number(row.total_input_tokens || 0),
          total_output_tokens: Number(row.total_output_tokens || 0),
          total_cache_creation_input_tokens: Number(row.total_cache_creation_input_tokens || 0),
          total_cache_read_input_tokens: Number(row.total_cache_read_input_tokens || 0),
          total_tokens: Number(row.total_tokens || 0),
          total_cost_usd: Number(row.total_cost_usd || 0),
          turns: row.turns || 0,
          models: row.models || [],
          model_usage: [],
        });
      }
      if (row.model) {
        taskMap.get(row.task_id).model_usage.push({
          model: row.model,
          input_tokens: Number(row.model_input),
          output_tokens: Number(row.model_output),
          cache_creation_input_tokens: Number(row.model_cc),
          cache_read_input_tokens: Number(row.model_cr),
          cost_usd: Number(row.model_cost),
        });
      }
    }

    const tasks = Array.from(taskMap.values());

    // Summary
    const summaryResult = await this.pool.query(
      `SELECT
         COUNT(DISTINCT t.id) as total_tasks,
         COALESCE(SUM(tu.total_input_tokens), 0) as total_input_tokens,
         COALESCE(SUM(tu.total_output_tokens), 0) as total_output_tokens,
         COALESCE(SUM(tu.total_cache_creation_input_tokens), 0) as total_cache_creation_input_tokens,
         COALESCE(SUM(tu.total_cache_read_input_tokens), 0) as total_cache_read_input_tokens,
         COALESCE(SUM(tu.total_tokens), 0) as total_tokens,
         COALESCE(SUM(tu.total_cost_usd), 0) as total_cost_usd,
         COALESCE(SUM(tu.turns), 0) as total_turns
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       LEFT JOIN task_usage tu ON tu.task_id = t.id
       ${whereClause}`,
      params,
    );

    const sr = summaryResult.rows[0] || {};
    const summary: AgentUsageSummary = {
      agent_id: agentId,
      total_tasks: Number(sr.total_tasks || 0),
      total_input_tokens: Number(sr.total_input_tokens || 0),
      total_output_tokens: Number(sr.total_output_tokens || 0),
      total_cache_creation_input_tokens: Number(sr.total_cache_creation_input_tokens || 0),
      total_cache_read_input_tokens: Number(sr.total_cache_read_input_tokens || 0),
      total_tokens: Number(sr.total_tokens || 0),
      total_cost_usd: Number(sr.total_cost_usd || 0),
      total_turns: Number(sr.total_turns || 0),
      models: sr.models || [],
      model_usage: [],  // filled below
      daily_spend: [],  // filled below
    };

    // Per-model summary with full token breakdown
    const modelSummary = await this.pool.query(
      `SELECT tm.model,
              SUM(tm.input_tokens) as input_tokens,
              SUM(tm.output_tokens) as output_tokens,
              SUM(tm.cache_creation_input_tokens) as cache_creation_input_tokens,
              SUM(tm.cache_read_input_tokens) as cache_read_input_tokens,
              SUM(tm.input_tokens + tm.output_tokens + tm.cache_creation_input_tokens + tm.cache_read_input_tokens) as tokens,
              SUM(tm.cost_usd) as cost_usd
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       JOIN task_model_usage tm ON tm.task_id = t.id
       ${whereClause}
       GROUP BY tm.model
       ORDER BY cost_usd DESC`,
      params,
    );

    summary.model_usage = modelSummary.rows.map(r => ({
      model: r.model,
      input_tokens: Number(r.input_tokens || 0),
      output_tokens: Number(r.output_tokens || 0),
      cache_creation_input_tokens: Number(r.cache_creation_input_tokens || 0),
      cache_read_input_tokens: Number(r.cache_read_input_tokens || 0),
      tokens: Number(r.tokens || 0),
      cost_usd: Number(r.cost_usd || 0),
    }));

    // Daily spend for chart (total per day)
    const dailyResult = await this.pool.query(
      `SELECT DATE(t.created_at)::text as date,
              COALESCE(SUM(tu.total_cost_usd), 0) as cost_usd,
              COALESCE(SUM(tu.total_input_tokens), 0) as input_tokens,
              COALESCE(SUM(tu.total_output_tokens), 0) as output_tokens,
              COALESCE(SUM(tu.total_cache_creation_input_tokens), 0) as cache_creation_input_tokens,
              COALESCE(SUM(tu.total_cache_read_input_tokens), 0) as cache_read_input_tokens
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       LEFT JOIN task_usage tu ON tu.task_id = t.id
       ${whereClause}
       GROUP BY DATE(t.created_at)
       ORDER BY DATE(t.created_at) ASC`,
      params,
    );

    // Daily spend by model (for stacked chart)
    const dailyModelResult = await this.pool.query(
      `SELECT DATE(t.created_at)::text as date,
              tm.model,
              COALESCE(SUM(tm.cost_usd), 0) as cost_usd
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       JOIN task_model_usage tm ON tm.task_id = t.id
       ${whereClause}
       GROUP BY DATE(t.created_at), tm.model
       ORDER BY DATE(t.created_at) ASC, tm.model`,
      params,
    );

    // Build per-date model map
    const dailyModelMap = new Map<string, { model: string; cost_usd: number }[]>();
    for (const row of dailyModelResult.rows) {
      const key = row.date;
      if (!dailyModelMap.has(key)) dailyModelMap.set(key, []);
      dailyModelMap.get(key)!.push({ model: row.model, cost_usd: Number(row.cost_usd || 0) });
    }

    summary.daily_spend = dailyResult.rows.map(r => ({
      date: r.date,
      cost_usd: Number(r.cost_usd || 0),
      input_tokens: Number(r.input_tokens || 0),
      output_tokens: Number(r.output_tokens || 0),
      cache_creation_input_tokens: Number(r.cache_creation_input_tokens || 0),
      cache_read_input_tokens: Number(r.cache_read_input_tokens || 0),
      by_model: dailyModelMap.get(r.date) || [],
    }));

    return { tasks, summary, total, page, pageSize };
  }

  /**
   * Get usage overview across all agents.
   */
  async getOverview(options: {
    startDate?: string;
    endDate?: string;
  } = {}): Promise<{
    agents: AgentUsageSummary[];
    models: { model: string; input_tokens: number; output_tokens: number; cache_creation_input_tokens: number; cache_read_input_tokens: number; tokens: number; cost_usd: number }[];
    daily_spend: { date: string; cost_usd: number; by_model: { model: string; cost_usd: number }[] }[];
    total_cost_usd: number;
    total_tokens: number;
    total_tasks: number;
    total_turns: number;
  }> {
    const { startDate, endDate } = options;
    const conditions: string[] = [];
    const params: any[] = [];
    let paramIdx = 1;

    if (startDate) {
      conditions.push(`t.created_at >= $${paramIdx}`);
      params.push(startDate);
      paramIdx++;
    }
    if (endDate) {
      conditions.push(`t.created_at < $${paramIdx}`);
      params.push(endDate + "T23:59:59");
      paramIdx++;
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

    const { rows } = await this.pool.query(
      `SELECT s.agent_id,
              COUNT(DISTINCT t.id) as total_tasks,
              COALESCE(SUM(tu.total_input_tokens), 0) as total_input_tokens,
              COALESCE(SUM(tu.total_output_tokens), 0) as total_output_tokens,
              COALESCE(SUM(tu.total_cache_creation_input_tokens), 0) as total_cache_creation_input_tokens,
              COALESCE(SUM(tu.total_cache_read_input_tokens), 0) as total_cache_read_input_tokens,
              COALESCE(SUM(tu.total_tokens), 0) as total_tokens,
              COALESCE(SUM(tu.total_cost_usd), 0) as total_cost_usd,
              COALESCE(SUM(tu.turns), 0) as total_turns
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       LEFT JOIN task_usage tu ON tu.task_id = t.id
       ${whereClause}
       GROUP BY s.agent_id
       ORDER BY total_cost_usd DESC`,
      params,
    );

    const agents = rows.map(r => ({
      agent_id: r.agent_id,
      total_tasks: Number(r.total_tasks || 0),
      total_input_tokens: Number(r.total_input_tokens || 0),
      total_output_tokens: Number(r.total_output_tokens || 0),
      total_cache_creation_input_tokens: Number(r.total_cache_creation_input_tokens || 0),
      total_cache_read_input_tokens: Number(r.total_cache_read_input_tokens || 0),
      total_tokens: Number(r.total_tokens || 0),
      total_cost_usd: Number(r.total_cost_usd || 0),
      total_turns: Number(r.total_turns || 0),
      models: [],
      model_usage: [],
      daily_spend: [],
    }));

    // Cross-agent model breakdown
    const modelResult = await this.pool.query(
      `SELECT tm.model,
              SUM(tm.input_tokens) as input_tokens,
              SUM(tm.output_tokens) as output_tokens,
              SUM(tm.cache_creation_input_tokens) as cache_creation_input_tokens,
              SUM(tm.cache_read_input_tokens) as cache_read_input_tokens,
              SUM(tm.input_tokens + tm.output_tokens + tm.cache_creation_input_tokens + tm.cache_read_input_tokens) as tokens,
              SUM(tm.cost_usd) as cost_usd
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       JOIN task_model_usage tm ON tm.task_id = t.id
       ${whereClause}
       GROUP BY tm.model
       ORDER BY cost_usd DESC`,
      params,
    );

    const models = modelResult.rows.map(r => ({
      model: r.model,
      input_tokens: Number(r.input_tokens || 0),
      output_tokens: Number(r.output_tokens || 0),
      cache_creation_input_tokens: Number(r.cache_creation_input_tokens || 0),
      cache_read_input_tokens: Number(r.cache_read_input_tokens || 0),
      tokens: Number(r.tokens || 0),
      cost_usd: Number(r.cost_usd || 0),
    }));

    // Daily spend
    const dailyResult = await this.pool.query(
      `SELECT DATE(t.created_at)::text as date,
              COALESCE(SUM(tu.total_cost_usd), 0) as cost_usd,
              COALESCE(SUM(tu.total_input_tokens), 0) as input_tokens,
              COALESCE(SUM(tu.total_output_tokens), 0) as output_tokens,
              COALESCE(SUM(tu.total_cache_creation_input_tokens), 0) as cache_creation_input_tokens,
              COALESCE(SUM(tu.total_cache_read_input_tokens), 0) as cache_read_input_tokens
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       LEFT JOIN task_usage tu ON tu.task_id = t.id
       ${whereClause}
       GROUP BY DATE(t.created_at)
       ORDER BY DATE(t.created_at) ASC`,
      params,
    );

    // Daily spend by model (for stacked chart)
    const dailyModelResult = await this.pool.query(
      `SELECT DATE(t.created_at)::text as date,
              tm.model,
              COALESCE(SUM(tm.cost_usd), 0) as cost_usd
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       JOIN task_model_usage tm ON tm.task_id = t.id
       ${whereClause}
       GROUP BY DATE(t.created_at), tm.model
       ORDER BY DATE(t.created_at) ASC, tm.model`,
      params,
    );

    const dailyModelMap = new Map<string, { model: string; cost_usd: number }[]>();
    for (const row of dailyModelResult.rows) {
      const key = row.date;
      if (!dailyModelMap.has(key)) dailyModelMap.set(key, []);
      dailyModelMap.get(key)!.push({ model: row.model, cost_usd: Number(row.cost_usd || 0) });
    }

    const daily_spend = dailyResult.rows.map(r => ({
      date: r.date,
      cost_usd: Number(r.cost_usd || 0),
      input_tokens: Number(r.input_tokens || 0),
      output_tokens: Number(r.output_tokens || 0),
      cache_creation_input_tokens: Number(r.cache_creation_input_tokens || 0),
      cache_read_input_tokens: Number(r.cache_read_input_tokens || 0),
      by_model: dailyModelMap.get(r.date) || [],
    }));

    const total_cost_usd = agents.reduce((s, a) => s + a.total_cost_usd, 0);
    const total_tokens = agents.reduce((s, a) => s + a.total_tokens, 0);
    const total_tasks = agents.reduce((s, a) => s + a.total_tasks, 0);
    const total_turns = agents.reduce((s, a) => s + a.total_turns, 0);

    return { agents, models, daily_spend, total_cost_usd, total_tokens, total_tasks, total_turns };
  }

  /**
   * Backfill usage for all agents by fetching from each gateway.
   * Skips sessions that already have usage data.
   */
  async backfillAllAgents(): Promise<{ agentId: string; sessions: number; errors: number }[]> {
    if (!this._gatewayClients || this._gatewayClients.size === 0) return [];

    const results: { agentId: string; sessions: number; errors: number }[] = [];

    for (const [agentId, client] of this._gatewayClients) {
      let sessionsBackfilled = 0;
      let errorCount = 0;

      try {
        const gwUsage = await client.getAllUsage();
        const sessionUsages = gwUsage.sessions || [];

        for (const sessionUsage of sessionUsages) {
          try {
            const { rows: taskRows } = await this.pool.query(
              `SELECT t.id FROM tasks t JOIN sessions s ON t.session_id = s.id
               WHERE s.gateway_session_id = $1 ORDER BY t.created_at ASC`,
              [sessionUsage.gateway_session_id],
            );

            if (taskRows.length === 0) continue;

            if (taskRows.length === 1) {
              const existing = await this.getTaskUsage(taskRows[0].id);
              if (existing) continue;
              await this.upsertTaskUsage(taskRows[0].id, sessionUsage);
              sessionsBackfilled++;
            } else {
              try {
                const turnsData = await client.getUsageTurns(sessionUsage.gateway_session_id);
                const turns = turnsData.turns || [];

                if (turns.length === 0) {
                  const existing = await this.getTaskUsage(taskRows[0].id);
                  if (existing) continue;
                  await this.upsertTaskUsage(taskRows[0].id, sessionUsage);
                  sessionsBackfilled++;
                  continue;
                }

                const taskUsages = new Map<number, {
                  models: Record<string, { model: string; input_tokens: number; output_tokens: number; cache_creation_input_tokens: number; cache_read_input_tokens: number }>;
                  turns: number;
                }>();

                for (const turn of turns) {
                  const turnTime = new Date(turn.timestamp);
                  let ownerTaskId = taskRows[0].id;
                  for (const row of taskRows) {
                    if (new Date(row.created_at) <= turnTime) {
                      ownerTaskId = row.id;
                    } else break;
                  }
                  if (!taskUsages.has(ownerTaskId)) {
                    taskUsages.set(ownerTaskId, { models: {}, turns: 0 });
                  }
                  const tu = taskUsages.get(ownerTaskId)!;
                  tu.turns++;
                  const m = turn.model;
                  if (!tu.models[m]) {
                    tu.models[m] = { model: m, input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 };
                  }
                  tu.models[m].input_tokens += turn.input_tokens;
                  tu.models[m].output_tokens += turn.output_tokens;
                  tu.models[m].cache_creation_input_tokens += turn.cache_creation_input_tokens;
                  tu.models[m].cache_read_input_tokens += turn.cache_read_input_tokens;
                }

                for (const row of taskRows) {
                  const tu = taskUsages.get(row.id);
                  let ti = 0, to = 0, tcc = 0, tcr = 0;
                  if (tu) {
                    for (const m of Object.values(tu.models)) {
                      ti += m.input_tokens;
                      to += m.output_tokens;
                      tcc += m.cache_creation_input_tokens;
                      tcr += m.cache_read_input_tokens;
                    }
                  }
                  await this.upsertTaskUsage(row.id, {
                    turns: tu?.turns ?? 0,
                    total_input_tokens: ti,
                    total_output_tokens: to,
                    total_cache_creation_input_tokens: tcc,
                    total_cache_read_input_tokens: tcr,
                    total_tokens: ti + to + tcc + tcr,
                    models: tu?.models ?? {},
                  });
                }
                sessionsBackfilled++;
              } catch {
                // fallback: assign to first task
                const existing = await this.getTaskUsage(taskRows[0].id);
                if (existing) continue;
                await this.upsertTaskUsage(taskRows[0].id, sessionUsage);
                sessionsBackfilled++;
              }
            }
          } catch {
            errorCount++;
          }
        }
      } catch {
        // agent unreachable, skip
      }

      results.push({ agentId, sessions: sessionsBackfilled, errors: errorCount });
    }

    return results;
  }
}
