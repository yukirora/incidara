import fs from "fs";
import yaml from "js-yaml";
import { z } from "zod";
import type pg from "pg";
import { getAllAgentGroupAccess } from "./groups.js";

const AgentSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  description: z.string().optional(),
  gateway_url: z.string().url(),
  backend: z.enum(["claude_code", "pi_agent"]),
  access: z.object({
    owners: z.array(z.string().email()),
    groups: z.array(z.string()),
  }),
});

export type Agent = z.infer<typeof AgentSchema>;

const AgentsSchema = z.array(AgentSchema);

export function loadAgents(filePath: string): Agent[] {
  const content = fs.readFileSync(filePath, "utf8");
  const raw = yaml.load(content);
  return AgentsSchema.parse(raw);
}

/**
 * Per-agent, per-group access level record.
 * Keyed by `agentId:groupId`.
 */
export type AgentGroupLevels = Record<string, string>;

/**
 * Returns agents with their groups enriched by DB overrides.
 * DB groups are additive; YAML groups are preserved as "yaml" source.
 * Also returns per-(agent, group) access levels.
 */
export async function enrichAgentsWithDbGroups(
  agents: Agent[],
  pool: pg.Pool
): Promise<EnrichedAgent[]> {
  const dbRows = await getAllAgentGroupAccess(pool);
  const dbMap = new Map<string, string[]>();
  const levelMap = new Map<string, string>();

  for (const row of dbRows) {
    const existing = dbMap.get(row.agent_id) ?? [];
    existing.push(row.group_id);
    dbMap.set(row.agent_id, existing);
    // Access level is stored per (agent, group) in DB
    levelMap.set(`${row.agent_id}:${row.group_id}`, row.access_level);
  }

  return agents.map((agent) => {
    const dbGroups = dbMap.get(agent.id) ?? [];
    // Build per-group levels for this agent
    const groupLevels: Record<string, string> = {};
    for (const g of agent.access.groups) {
      groupLevels[g] = levelMap.get(`${agent.id}:${g}`) ?? "interactive";
    }
    for (const g of dbGroups) {
      if (!groupLevels[g]) {
        groupLevels[g] = levelMap.get(`${agent.id}:${g}`) ?? "interactive";
      }
    }

    return {
      ...agent,
      access: {
        ...agent.access,
        groups: [...new Set([...agent.access.groups, ...dbGroups])],
      },
      yaml_groups: agent.access.groups,
      db_groups: dbGroups,
      group_levels: groupLevels,
    };
  });
}

export interface EnrichedAgent extends Agent {
  /** Original groups from YAML */
  yaml_groups: string[];
  /** Groups added via DB */
  db_groups: string[];
  /** Per-group access level: { [groupId]: "interactive" | "readonly" | ... } */
  group_levels: Record<string, string>;
}
