import { api } from "../lib/api.ts";

export interface Session {
  id: number;
  gateway_session_id: string;
  agent_id: string;
  owner_email: string;
  title: string | null;
  shared_with_groups: string[];
  created_at: string;
}

export interface ListSessionsParams {
  agent_id?: string;
  scope?: "mine" | "shared" | "all";
}

export async function listSessions(params: ListSessionsParams = {}): Promise<Session[]> {
  const query = new URLSearchParams();
  if (params.agent_id) query.set("agent_id", params.agent_id);
  if (params.scope) query.set("scope", params.scope);
  const qs = query.toString();
  const res = await api<{ sessions: Session[] }>("GET", `/api/sessions${qs ? `?${qs}` : ""}`);
  return res.sessions;
}

export async function getSession(id: number): Promise<{ session: Session }> {
  return api<{ session: Session }>("GET", `/api/sessions/${id}`);
}

export async function patchSession(
  id: number,
  patch: { title?: string; shared_with_groups?: string[] }
): Promise<{ session: Session }> {
  return api<{ session: Session }>("PATCH", `/api/sessions/${id}`, patch);
}

export async function deleteSession(id: number): Promise<void> {
  return api<void>("DELETE", `/api/sessions/${id}`);
}

export interface SessionState {
  session_id: string;
  status: string;
  current_tool?: string;
  last_message_preview?: string;
  waiting_for_input: boolean;
  claude_session_id?: string;
  latest_seq: number;
}

export async function getState(id: number): Promise<SessionState> {
  return api<SessionState>("GET", `/api/sessions/${id}/state`);
}

export type GatewayEventType =
  | "session.started"
  | "session.state_changed"
  | "session.completed"
  | "session.error"
  | "session.interrupted"
  | "session.waiting_input"
  | "message.user"
  | "message.agent"
  | "message.delta"
  | "tool.started"
  | "tool.stdout"
  | "tool.finished"
  | "permission.requested"
  | "permission.resolved"
  | "question.requested"
  | "question.answered"
  | "artifact.created"
  | "subagent.started"
  | "subagent.progress"
  | "subagent.updated"
  | "subagent.finished"
  | "subagent.delta"
  | "subagent.tool.started"
  | "subagent.tool.finished"
  | "subagent.tool.progress"
  | "mcp.progress";

export interface GatewayEvent {
  seq: number;
  session_id: string;
  event_type: GatewayEventType;
  timestamp: string;
  payload: Record<string, unknown>;
}

export type Round = { seq: number; preview: string };

export async function getRounds(sessionId: number): Promise<{ rounds: Round[]; total: number }> {
  return api<{ rounds: Round[]; total: number }>("GET", `/api/sessions/${sessionId}/rounds`);
}

export async function getEventsHistory(
  id: number,
  afterSeq = 0,
  beforeSeq?: number,
): Promise<{ events: GatewayEvent[]; next_after_seq: number }> {
  let url = `/api/sessions/${id}/events?after_seq=${afterSeq}`;
  if (beforeSeq != null) url += `&before_seq=${beforeSeq}`;
  return api<{ events: GatewayEvent[]; next_after_seq: number }>("GET", url);
}

export async function sendGuidance(
  id: number,
  content: string
): Promise<{ accepted: true }> {
  return api<{ accepted: true }>("POST", `/api/sessions/${id}/messages`, { content });
}

export async function startNewTask(
  id: number,
  prompt: string
): Promise<{ task: { id: number; status: string } }> {
  return api<{ task: { id: number; status: string } }>(
    "POST",
    `/api/sessions/${id}/tasks`,
    { prompt }
  );
}

export async function interrupt(id: number): Promise<{ ok: true }> {
  return api<{ ok: true }>("POST", `/api/sessions/${id}/interrupt`);
}

export async function resume(id: number, content?: string): Promise<{ accepted: true }> {
  return api<{ accepted: true }>("POST", `/api/sessions/${id}/resume`, content ? { content } : undefined);
}

export function eventStreamUrl(id: number, afterSeq?: number): string {
  const qs = afterSeq !== undefined ? `?after_seq=${afterSeq}` : "";
  return `/api/sessions/${id}/events/stream${qs}`;
}

export interface CreateSessionInput {
  prompt: string;
  title?: string;
  completion_mode?: 'auto' | 'manual';
}

export interface CreateSessionResult {
  session: Session;
  task: { id: number; status: string };
}

export async function createSession(
  agentId: string,
  input: CreateSessionInput
): Promise<CreateSessionResult> {
  return api<CreateSessionResult>(
    "POST",
    `/api/agents/${encodeURIComponent(agentId)}/sessions`,
    input
  );
}

export async function createTask(
  sessionId: number,
  input: { prompt: string; title?: string; completion_mode?: 'auto' | 'manual' }
): Promise<{ task: { id: number; status: string } }> {
  return api<{ task: { id: number; status: string } }>(
    "POST",
    `/api/sessions/${sessionId}/tasks`,
    input
  );
}

export async function approvePermission(sessionId: number, permId: string): Promise<{ ok: true; decision: string }> {
  return api<{ ok: true; decision: string }>("POST", `/api/sessions/${sessionId}/permissions/${permId}/approve`);
}

export async function denyPermission(sessionId: number, permId: string, reason?: string): Promise<{ ok: true; decision: string }> {
  return api<{ ok: true; decision: string }>("POST", `/api/sessions/${sessionId}/permissions/${permId}/deny`, reason ? { reason } : undefined);
}

export async function answerQuestion(
  sessionId: number,
  questionId: string,
  answers: Record<string, string>,
): Promise<{ ok: true }> {
  return api<{ ok: true }>("POST", `/api/sessions/${sessionId}/questions/${questionId}/answer`, { answers });
}

export interface SubagentTranscript {
  task_id: string;
  toolCalls: Array<{
    name: string;
    input: any;
    result: string;
    isError: boolean;
  }>;
  textSnippets: string[];
  thinkingSnippets: string[];
  finalResult: string;
  totalMessages: number;
}

export async function getSubagentTranscript(sessionId: number, taskId: string): Promise<SubagentTranscript> {
  return api<SubagentTranscript>("GET", `/api/sessions/${sessionId}/subagents/${taskId}/transcript`);
}
