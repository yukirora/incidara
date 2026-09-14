import { api } from "../lib/api.ts";

export type TriggerType = "once" | "interval" | "cron";
export type SessionMode = "new" | "reuse";
export type CompletionMode = "auto" | "manual";

export interface Schedule {
  id: number;
  name: string | null;
  agent_id: string;
  owner_email: string;
  prompt: string;
  trigger_type: TriggerType;
  run_at: string | null;
  interval_seconds: number | null;
  cron_expr: string | null;
  timezone: string;
  session_mode: SessionMode;
  reuse_session_id: number | null;
  completion_mode: CompletionMode;
  enabled: boolean;
  last_fired_at: string | null;
  next_fire_at: string | null;
  created_at: string;
}

export interface ListSchedulesParams {
  agent_id?: string;
  enabled?: boolean;
}

export interface CreateScheduleInput {
  agent_id: string;
  name?: string;
  prompt: string;
  trigger_type: TriggerType;
  run_at?: string;
  interval_seconds?: number;
  cron_expr?: string;
  timezone?: string;
  session_mode?: SessionMode;
  reuse_session_id?: number;
  completion_mode?: CompletionMode;
}

export type PatchScheduleInput = Partial<
  Omit<CreateScheduleInput, "agent_id"> & { enabled: boolean }
>;

export async function listSchedules(
  params: ListSchedulesParams = {}
): Promise<Schedule[]> {
  const query = new URLSearchParams();
  if (params.agent_id) query.set("agent_id", params.agent_id);
  if (params.enabled !== undefined) query.set("enabled", String(params.enabled));
  const qs = query.toString();
  const res = await api<{ schedules: Schedule[] }>(
    "GET",
    `/api/schedules${qs ? `?${qs}` : ""}`
  );
  return res.schedules;
}

export async function getSchedule(id: number): Promise<Schedule> {
  const res = await api<{ schedule: Schedule }>("GET", `/api/schedules/${id}`);
  return res.schedule;
}

export async function createSchedule(input: CreateScheduleInput): Promise<Schedule> {
  const res = await api<{ schedule: Schedule }>("POST", "/api/schedules", input);
  return res.schedule;
}

export async function patchSchedule(
  id: number,
  input: PatchScheduleInput
): Promise<Schedule> {
  const res = await api<{ schedule: Schedule }>("PATCH", `/api/schedules/${id}`, input);
  return res.schedule;
}

export async function deleteSchedule(id: number): Promise<void> {
  return api<void>("DELETE", `/api/schedules/${id}`);
}

export async function runNow(id: number): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>("POST", `/api/schedules/${id}/run-now`, {});
}
