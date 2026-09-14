import { api } from "../lib/api.ts";

export interface AgentCounters {
  running: number;
  waiting: number;
  completed_today: number;
}

export interface AgentAccess {
  owners: string[];
  groups: string[];
}

export type AccessLevel = "admin" | "owner" | "group";
export type AgentBackend = "claude_code" | "pi_agent";

export interface AgentSummary {
  id: string;
  name: string;
  description?: string;
  backend: AgentBackend;
  access: AgentAccess;
  access_level: AccessLevel;
  counters: AgentCounters;
}

export async function listAgents(): Promise<AgentSummary[]> {
  const res = await api<{ agents: AgentSummary[] }>("GET", "/api/agents");
  return res.agents;
}

export async function getAgent(id: string): Promise<AgentSummary> {
  const res = await api<{ agent: AgentSummary }>("GET", `/api/agents/${encodeURIComponent(id)}`);
  return res.agent;
}
