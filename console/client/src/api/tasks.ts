import { api } from "../lib/api.ts";

export type TaskStatus =
  | "pending"
  | "running"
  | "busy"
  | "waiting_input"
  | "completed"
  | "error"
  | "interrupted"
  | "cancelled";

export interface Task {
  id: number;
  session_id: number;
  prompt: string;
  submitter_email: string;
  status: TaskStatus;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  output_preview: string | null;
  gateway_seq_start: number | null;
  from_schedule_id: number | null;
  title: string | null;
  completion_mode: 'auto' | 'manual';
  // Joined fields from sessions table
  agent_id?: string;
  gateway_session_id?: string;
}

export interface ListTasksParams {
  status?: TaskStatus;
  statuses?: TaskStatus[];
  agent_id?: string;
  session_id?: number;
  scope?: "mine" | "shared" | "all";
  title_search?: string;
  limit?: number;
  offset?: number;
}

export interface ListTasksResult {
  tasks: Task[];
  total: number;
}

export async function listTasks(params: ListTasksParams = {}): Promise<ListTasksResult> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.statuses?.length) query.set("statuses", params.statuses.join(","));
  if (params.agent_id) query.set("agent_id", params.agent_id);
  if (params.session_id !== undefined) query.set("session_id", String(params.session_id));
  if (params.scope) query.set("scope", params.scope);
  if (params.title_search) query.set("title_search", params.title_search);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  return api<ListTasksResult>("GET", `/api/tasks${qs ? `?${qs}` : ""}`);
}

export async function getTask(id: number): Promise<Task> {
  const res = await api<{ task: Task }>("GET", `/api/tasks/${id}`);
  return res.task;
}

export async function cancelPendingTask(id: number): Promise<void> {
  return api<void>("DELETE", `/api/tasks/${id}`);
}

export async function completeTask(id: number): Promise<Task> {
  const res = await api<{ task: Task }>("POST", `/api/tasks/${id}/complete`);
  return res.task;
}
