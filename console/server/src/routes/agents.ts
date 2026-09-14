import { Router } from "express";
import type pg from "pg";
import type { Agent } from "../agent-registry.js";
import { enrichAgentsWithDbGroups } from "../agent-registry.js";

interface AgentWithAccess extends Omit<Agent, "gateway_url"> {
  access_level: "admin" | "owner" | "group";
  counters: {
    running: number;
    waiting: number;
    completed_today: number;
  };
}

async function getCounters(
  pool: pg.Pool,
  agentId: string
): Promise<{ running: number; waiting: number; completed_today: number }> {
  try {
    const { rows } = await pool.query<{ status: string; count: string }>(
      `SELECT t.status, COUNT(*) as count
       FROM tasks t
       JOIN sessions s ON t.session_id = s.id
       WHERE s.agent_id = $1
         AND (
           t.status IN ('running', 'waiting_input')
           OR (t.status = 'completed' AND t.ended_at >= now() - interval '1 day')
         )
       GROUP BY t.status`,
      [agentId]
    );
    let running = 0;
    let waiting = 0;
    let completed_today = 0;
    for (const row of rows) {
      if (row.status === "running") running = parseInt(row.count);
      else if (row.status === "waiting_input") waiting = parseInt(row.count);
      else if (row.status === "completed") completed_today = parseInt(row.count);
    }
    return { running, waiting, completed_today };
  } catch {
    return { running: 0, waiting: 0, completed_today: 0 };
  }
}

interface AgentsRouterDeps {
  pool: pg.Pool;
  getAgents: () => Agent[];
}

export function makeAgentsRouter(deps: AgentsRouterDeps): Router {
  const { pool, getAgents } = deps;
  const router = Router();

  // Get agents enriched with DB group access overrides
  async function getEffectiveAgents(): Promise<Agent[]> {
    return enrichAgentsWithDbGroups(getAgents(), pool);
  }

  // GET /
  router.get("/", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const agents = await getEffectiveAgents();

    const accessible = await Promise.all(agents.map(a => auth.canAccessAgent(a))).then(access => agents.filter((_, i) => access[i]));

    const results: AgentWithAccess[] = await Promise.all(
      accessible.map(async (agent) => {
        const counters = await getCounters(pool, agent.id);
        const access_level = await auth.agentAccessLevel(agent) as "admin" | "owner" | "group";
        const { gateway_url: _gw, ...rest } = agent;
        return { ...rest, access_level, counters };
      })
    );

    res.json({ agents: results });
  });

  // GET /:id
  router.get("/:id", async (req, res): Promise<void> => {
    const auth = req.auth!;
    const agents = await getEffectiveAgents();
    const agent = agents.find((a) => a.id === req.params.id);

    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    if (!await auth.canAccessAgent(agent)) {
      res.status(403).json({ error: "Forbidden" });
      return;
    }

    const counters = await getCounters(pool, agent.id);
    const access_level = await auth.agentAccessLevel(agent) as "admin" | "owner" | "group";
    const { gateway_url: _gw, ...rest } = agent;
    res.json({ agent: { ...rest, access_level, counters } });
  });

  return router;
}
