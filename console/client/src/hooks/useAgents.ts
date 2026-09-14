import { useQuery } from "@tanstack/react-query";
import { listAgents, getAgent, type AgentSummary } from "../api/agents.ts";
import { useMe } from "./useMe.ts";

export function useAgents(): {
  agents: AgentSummary[];
  isLoading: boolean;
  error: Error | null;
} {
  const { agent_levels } = useMe();
  const { data, isLoading, error } = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: listAgents,
    staleTime: 30_000,
  });

  // Filter to agents the user has access to (level > no_access)
  const filtered = data?.filter((a) => agent_levels[a.id] !== undefined) ?? [];

  return {
    agents: filtered,
    isLoading,
    error: error as Error | null,
  };
}

export function useAgent(id: string): {
  agent: AgentSummary | null;
  isLoading: boolean;
  error: Error | null;
} {
  const { agent_levels } = useMe();
  const { data, isLoading, error } = useQuery<AgentSummary>({
    queryKey: ["agents", id],
    queryFn: () => getAgent(id),
    staleTime: 30_000,
    enabled: !!id,
  });

  // Return null if user doesn't have access to this agent
  const agent = data && agent_levels[id] !== undefined ? data : null;

  return {
    agent,
    isLoading,
    error: error as Error | null,
  };
}
