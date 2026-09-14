import { api } from "../lib/api";

// Task-level usage (from DB / lazy-load from gateway)
export interface TaskModelUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  cost_usd: number;
}

export interface TaskUsage {
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
  model_usage: TaskModelUsage[];
}

// Agent-level usage (paginated task list + summary)
export interface AgentUsageTask {
  task_id: number;
  session_id: number;
  title: string | null;
  prompt: string;
  status: string;
  created_at: string;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_input_tokens: number;
  total_cache_read_input_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  turns: number;
  models: string[];
  model_usage: TaskModelUsage[];
}

export interface AgentModelSummary {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  tokens: number;
  cost_usd: number;
}

export interface DailySpend {
  date: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  by_model: { model: string; cost_usd: number }[];
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
  model_usage: AgentModelSummary[];
  daily_spend: DailySpend[];
}

export interface AgentUsageResponse {
  tasks: AgentUsageTask[];
  summary: AgentUsageSummary;
  total: number;
  page: number;
  pageSize: number;
}

export interface OverviewAgentSummary {
  agent_id: string;
  total_tasks: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_input_tokens: number;
  total_cache_read_input_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  total_turns: number;
}

export interface OverviewModelSummary {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  tokens: number;
  cost_usd: number;
}

export interface OverviewResponse {
  agents: OverviewAgentSummary[];
  models: OverviewModelSummary[];
  daily_spend: { date: string; cost_usd: number; input_tokens: number; output_tokens: number; cache_creation_input_tokens: number; cache_read_input_tokens: number; by_model: { model: string; cost_usd: number }[] }[];
  total_cost_usd: number;
  total_tokens: number;
  total_tasks: number;
  total_turns: number;
}

export async function fetchTaskUsage(taskId: number): Promise<TaskUsage> {
  return api<TaskUsage>("GET", `/api/usage/tasks/${taskId}`);
}

export async function fetchAgentUsage(
  agentId: string,
  options: {
    startDate?: string;
    endDate?: string;
    page?: number;
    pageSize?: number;
    search?: string;
    sortBy?: string;
    sortDir?: "asc" | "desc";
  } = {}
): Promise<AgentUsageResponse> {
  const params = new URLSearchParams();
  if (options.startDate) params.set("startDate", options.startDate);
  if (options.endDate) params.set("endDate", options.endDate);
  if (options.page) params.set("page", String(options.page));
  if (options.pageSize) params.set("pageSize", String(options.pageSize));
  if (options.search) params.set("search", options.search);
  if (options.sortBy) params.set("sortBy", options.sortBy);
  if (options.sortDir) params.set("sortDir", options.sortDir);
  const qs = params.toString();
  return api<AgentUsageResponse>("GET", `/api/usage/agents/${agentId}${qs ? "?" + qs : ""}`);
}

export async function fetchUsageOverview(
  options: { startDate?: string; endDate?: string } = {}
): Promise<OverviewResponse> {
  const params = new URLSearchParams();
  if (options.startDate) params.set("startDate", options.startDate);
  if (options.endDate) params.set("endDate", options.endDate);
  const qs = params.toString();
  return api<OverviewResponse>("GET", `/api/usage/overview${qs ? "?" + qs : ""}`);
}

export async function backfillUsage(
  options: { agentId?: string; force?: boolean } = {}
): Promise<{ results: { agentId: string; sessions: number; errors: number }[] }> {
  return api("POST", "/api/usage/backfill", options);
}
