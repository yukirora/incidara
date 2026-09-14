import { api, ApiError } from "../lib/api.ts";

export type AccessLevel = "interactive" | "readonly" | "readonly_input" | "readonly_summary";

export interface User {
  id: number;
  email: string;
  name?: string | null;
  created_at?: string;
}

export interface MeResponse {
  user: User;
  groups: string[];
  is_admin: boolean;
  agent_levels: Record<string, AccessLevel>;
  dashboard_access: Record<string, boolean>;
}

export interface SignupInput {
  email: string;
  password: string;
  name?: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

export async function signup(input: SignupInput): Promise<User> {
  const res = await api<{ user: User }>("POST", "/api/auth/signup", input);
  return res.user;
}

export async function login(input: LoginInput): Promise<MeResponse["user"] & Pick<MeResponse, "groups" | "is_admin" | "agent_levels" | "dashboard_access">> {
  const res = await api<MeResponse>("POST", "/api/auth/login", input);
  return { ...res.user, groups: res.groups, is_admin: res.is_admin, agent_levels: res.agent_levels, dashboard_access: res.dashboard_access };
}

export async function logout(): Promise<void> {
  return api<void>("POST", "/api/auth/logout");
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await api<void>("POST", "/api/auth/change-password", { currentPassword, newPassword });
}

export async function me(): Promise<MeResponse | null> {
  try {
    const res = await api<MeResponse>("GET", "/api/auth/me");
    return res;
  } catch (err: unknown) {
    if (err instanceof ApiError && err.status === 401) {
      return null;
    }
    throw err;
  }
}
