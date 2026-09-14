import { Router } from "express";
import { UsageStore } from "../usage-store.js";
import { GatewayClient } from "../gateway-client.js";

interface Deps {
  usageStore: UsageStore;
  gatewayClients: Map<string, GatewayClient>;
  pool: any;
}

export function usageRouter({ usageStore, gatewayClients, pool }: Deps): Router {
  const r = Router();

  /**
   * GET /api/usage/agents/:agentId
   *
   * Get usage summary and per-task breakdown for an agent.
   * Query params: startDate, endDate, page, pageSize, search, sortBy, sortDir
   */
  r.get("/agents/:agentId", async (req, res) => {
    try {
      const { agentId } = req.params;
      const startDate = req.query.startDate as string | undefined;
      const endDate = req.query.endDate as string | undefined;
      const page = Math.max(1, parseInt(req.query.page as string) || 1);
      const pageSize = Math.min(100, Math.max(1, parseInt(req.query.pageSize as string) || 20));
      const search = req.query.search as string | undefined;
      const sortBy = req.query.sortBy as string | undefined;
      const sortDir = req.query.sortDir as "asc" | "desc" | undefined;

      const result = await usageStore.getAgentUsage(agentId, {
        startDate,
        endDate,
        page,
        pageSize,
        search,
        sortBy,
        sortDir,
      });

      res.json(result);
    } catch (err: any) {
      console.error("[usage] agent usage error:", err);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /api/usage/tasks/:taskId
   *
   * Get usage for a specific task. If not in DB, fetches from gateway on demand.
   */
  r.get("/tasks/:taskId", async (req, res) => {
    try {
      const taskId = parseInt(req.params.taskId);

      // Check DB first
      let usage = await usageStore.getTaskUsage(taskId);
      if (usage) {
        res.json(usage);
        return;
      }

      // Not in DB — try fetching from gateway
      const { rows } = await pool.query(
        `SELECT t.id, t.session_id, s.agent_id, s.gateway_session_id
         FROM tasks t JOIN sessions s ON t.session_id = s.id
         WHERE t.id = $1`,
        [taskId],
      );

      if (rows.length === 0) {
        res.status(404).json({ error: "task not found" });
        return;
      }

      const { agent_id, gateway_session_id } = rows[0];
      const client = gatewayClients.get(agent_id);
      if (!client) {
        res.status(404).json({ error: "agent not found" });
        return;
      }

      // Fetch usage from gateway
      const gwUsage = await client.getUsage(gateway_session_id);
      if (!gwUsage || !gwUsage.turns) {
        res.json({ task_id: taskId, total_tokens: 0, total_cost_usd: 0, turns: 0, models: [], model_usage: [] });
        return;
      }

      // Check if session has multiple tasks — if so, split by turns
      const { rows: taskRows } = await pool.query(
        `SELECT t.id, t.created_at
         FROM tasks t JOIN sessions s ON t.session_id = s.id
         WHERE s.gateway_session_id = $1
         ORDER BY t.created_at ASC`,
        [gateway_session_id],
      );

      if (taskRows.length <= 1) {
        // Simple: one task owns the session
        usage = await usageStore.upsertTaskUsage(taskId, gwUsage);
      } else {
        // Split by turns using timestamps
        const task = taskRows.find((r: any) => r.id === taskId);
        if (!task) {
          res.status(404).json({ error: "task not in session" });
          return;
        }

        let turnsData: any = { turns: [] };
        try {
          turnsData = await client.getUsageTurns(gateway_session_id);
        } catch {
          // Fallback: assign full usage
          usage = await usageStore.upsertTaskUsage(taskId, gwUsage);
          res.json(usage);
          return;
        }

        const turns = turnsData.turns || [];
        const boundaries = taskRows.map((r: any) => ({
          taskId: r.id,
          startTime: new Date(r.created_at),
        }));

        const taskTime = new Date(task.created_at);
        const nextBoundary = boundaries.find((b: any) => b.startTime > taskTime && b.taskId !== taskId);
        const endTime = nextBoundary ? nextBoundary.startTime : new Date("2099-12-31");

        // Collect turns for this task
        let total_input = 0, total_output = 0, total_cc = 0, total_cr = 0, turnCount = 0;
        const models: Record<string, any> = {};
        for (const turn of turns) {
          const turnTime = new Date(turn.timestamp);
          if (turnTime >= taskTime && turnTime < endTime) {
            turnCount++;
            total_input += turn.input_tokens;
            total_output += turn.output_tokens;
            total_cc += turn.cache_creation_input_tokens;
            total_cr += turn.cache_read_input_tokens;
            const m = turn.model;
            if (!models[m]) models[m] = { model: m, input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 };
            models[m].input_tokens += turn.input_tokens;
            models[m].output_tokens += turn.output_tokens;
            models[m].cache_creation_input_tokens += turn.cache_creation_input_tokens;
            models[m].cache_read_input_tokens += turn.cache_read_input_tokens;
          }
        }

        usage = await usageStore.upsertTaskUsage(taskId, {
          turns: turnCount,
          total_input_tokens: total_input,
          total_output_tokens: total_output,
          total_cache_creation_input_tokens: total_cc,
          total_cache_read_input_tokens: total_cr,
          total_tokens: total_input + total_output + total_cc + total_cr,
          models,
        });
      }
      res.json(usage);
    } catch (err: any) {
      console.error("[usage] task usage error:", err);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /api/usage/overview
   *
   * Get usage overview across all agents.
   * Query params: startDate, endDate
   */
  r.get("/overview", async (req, res) => {
    if (!req.auth?.isAdmin) {
      res.status(403).json({ error: "Forbidden: admin access required" });
      return;
    }
    try {
      const startDate = req.query.startDate as string | undefined;
      const endDate = req.query.endDate as string | undefined;

      const result = await usageStore.getOverview({ startDate, endDate });
      res.json(result);
    } catch (err: any) {
      console.error("[usage] overview error:", err);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /api/usage/backfill
   *
   * Backfill usage data from gateway for all sessions of a specific agent,
   * or all agents if no agentId specified.
   * Splits usage per-task using timestamp boundaries.
   *
   * Body: { agentId?: string, force?: boolean }
   */
  r.post("/backfill", async (req, res) => {
    if (!req.auth?.isAdmin) {
      res.status(403).json({ error: "Forbidden: admin access required" });
      return;
    }
    try {
      const { agentId, force } = req.body;

      const agentIds = agentId ? [agentId] : Array.from(gatewayClients.keys());
      const results: { agentId: string; sessions: number; errors: number }[] = [];

      for (const aid of agentIds) {
        const client = gatewayClients.get(aid);
        if (!client) continue;

        const gwUsage = await client.getAllUsage();
        const sessionUsages = gwUsage.sessions || [];

        let sessionsBackfilled = 0;
        let errorCount = 0;

        for (const sessionUsage of sessionUsages) {
          try {
            // Get all tasks for this gateway session, ordered by gateway_seq_start
            const { rows: taskRows } = await pool.query(
              `SELECT t.id, t.gateway_seq_start, t.created_at
               FROM tasks t JOIN sessions s ON t.session_id = s.id
               WHERE s.gateway_session_id = $1
               ORDER BY t.created_at ASC`,
              [sessionUsage.gateway_session_id],
            );

            if (taskRows.length === 0) continue;

            if (taskRows.length > 1) {
              console.log(`[backfill] SPLIT: session ${sessionUsage.gateway_session_id} has ${taskRows.length} tasks`);
            }

            if (taskRows.length === 1) {
              // Simple case: one task owns the whole session
              const taskId = taskRows[0].id;
              if (!force) {
                const existing = await usageStore.getTaskUsage(taskId);
                if (existing) continue;
              }
              await usageStore.upsertTaskUsage(taskId, sessionUsage);
              sessionsBackfilled++;
            } else {
              // Multiple tasks: fetch per-turn usage with timestamps and split
              console.log(`[backfill] session ${sessionUsage.gateway_session_id} has ${taskRows.length} tasks, splitting by turns`);
              try {
                const turnsData = await client.getUsageTurns(sessionUsage.gateway_session_id);
                const turns = turnsData.turns || [];

                if (turns.length === 0) {
                  // No turn data, fall back to assigning full usage to first task
                  if (!force) {
                    const existing = await usageStore.getTaskUsage(taskRows[0].id);
                    if (existing) continue;
                  }
                  await usageStore.upsertTaskUsage(taskRows[0].id, sessionUsage);
                  sessionsBackfilled++;
                  continue;
                }

                // Build task boundaries using created_at timestamps
                interface TaskBoundary {
                  taskId: number;
                  startTime: Date;
                }
                const boundaries: TaskBoundary[] = taskRows.map((row: any) => ({
                  taskId: row.id,
                  startTime: new Date(row.created_at),
                }));

                // Assign each turn to the task whose start time is <= turn timestamp
                const taskUsages = new Map<number, {
                  models: Record<string, { model: string; input_tokens: number; output_tokens: number; cache_creation_input_tokens: number; cache_read_input_tokens: number }>;
                  turns: number;
                }>();

                for (const turn of turns) {
                  const turnTime = new Date(turn.timestamp);
                  // Find the latest task boundary that is <= turn time
                  let ownerTaskId = boundaries[0].taskId;
                  for (const b of boundaries) {
                    if (b.startTime <= turnTime) {
                      ownerTaskId = b.taskId;
                    } else {
                      break;
                    }
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

                // Upsert each task's split usage
                // First, write zero usage for ALL tasks to clear old inflated values
                for (const row of taskRows) {
                  const taskId = (row as any).id;
                  const tu = taskUsages.get(taskId);
                  let total_input = 0, total_output = 0, total_cc = 0, total_cr = 0;
                  if (tu) {
                    for (const m of Object.values(tu.models)) {
                      total_input += m.input_tokens;
                      total_output += m.output_tokens;
                      total_cc += m.cache_creation_input_tokens;
                      total_cr += m.cache_read_input_tokens;
                    }
                  }
                  await usageStore.upsertTaskUsage(taskId, {
                    turns: tu ? tu.turns : 0,
                    total_input_tokens: total_input,
                    total_output_tokens: total_output,
                    total_cache_creation_input_tokens: total_cc,
                    total_cache_read_input_tokens: total_cr,
                    total_tokens: total_input + total_output + total_cc + total_cr,
                    models: tu ? tu.models : {},
                  });
                }
                sessionsBackfilled++;
              } catch (turnErr) {
                // Fallback: if turns endpoint fails, assign full usage to first task
                console.error(`[backfill] turns split failed for ${sessionUsage.gateway_session_id}, falling back:`, turnErr);
                const taskId = taskRows[0].id;
                if (!force) {
                  const existing = await usageStore.getTaskUsage(taskId);
                  if (existing) continue;
                }
                await usageStore.upsertTaskUsage(taskId, sessionUsage);
                sessionsBackfilled++;
              }
            }
          } catch (err) {
            console.error(`[backfill] error for session ${sessionUsage.gateway_session_id}:`, err);
            errorCount++;
          }
        }

        results.push({ agentId: aid, sessions: sessionsBackfilled, errors: errorCount });
      }

      res.json({ results });
    } catch (err: any) {
      console.error("[usage] backfill error:", err);
      res.status(500).json({ error: err.message });
    }
  });

  return r;
}
