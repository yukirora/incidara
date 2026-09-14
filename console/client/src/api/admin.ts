import { api } from "../lib/api.ts";

export interface AdminUser {
  id: number;
  email: string;
  name: string | null;
  created_at: string;
}

export interface GroupMember {
  email: string;
  source: "yaml" | "db";
  added_by: string | null;
  added_at: string | null;
}

export interface AdminGroup {
  id: string;
  name: string;
  source: "yaml" | "db";
  can_delete: boolean;
  members: GroupMember[];
}

export interface AdminAgentAccess {
  owners: string[];
  groups: string[];
  yaml_groups: string[];
  db_groups: string[];
}

export interface AdminAgent {
  id: string;
  name: string;
  description?: string;
  backend: string;
  access: AdminAgentAccess;
  /** Per-group access level: { [groupId]: AccessLevel } */
  group_levels: Record<string, string>;
}

export async function listAdminUsers(): Promise<AdminUser[]> {
  const res = await api<{ users: AdminUser[] }>("GET", "/api/admin/users");
  return res.users;
}

export async function deleteAdminUser(email: string): Promise<void> {
  await api<void>("DELETE", `/api/admin/users/${encodeURIComponent(email)}`);
}

export async function listAdminGroups(): Promise<AdminGroup[]> {
  const res = await api<{ groups: AdminGroup[] }>("GET", "/api/admin/groups");
  return res.groups;
}

export async function createAdminGroup(id: string, name: string): Promise<AdminGroup> {
  const res = await api<{ group: AdminGroup }>("POST", "/api/admin/groups", { id, name });
  return res.group;
}

export async function deleteAdminGroup(groupId: string): Promise<void> {
  await api<void>("DELETE", `/api/admin/groups/${encodeURIComponent(groupId)}`);
}

export async function addGroupMember(groupId: string, email: string): Promise<void> {
  await api<unknown>("POST", `/api/admin/groups/${encodeURIComponent(groupId)}/members`, { email });
}

export async function removeGroupMember(groupId: string, email: string): Promise<void> {
  await api<void>(
    "DELETE",
    `/api/admin/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(email)}`
  );
}

export async function listAdminAgents(): Promise<AdminAgent[]> {
  const res = await api<{ agents: AdminAgent[] }>("GET", "/api/admin/agents");
  return res.agents;
}

export async function addAgentGroupAccess(agentId: string, groupId: string): Promise<void> {
  await api<unknown>("POST", `/api/admin/agents/${encodeURIComponent(agentId)}/groups`, { group_id: groupId });
}

export async function removeAgentGroupAccess(agentId: string, groupId: string): Promise<void> {
  await api<void>(
    "DELETE",
    `/api/admin/agents/${encodeURIComponent(agentId)}/groups/${encodeURIComponent(groupId)}`
  );
}

export async function updateAgentGroupLevel(
  agentId: string,
  groupId: string,
  accessLevel: string
): Promise<void> {
  await api<unknown>("PATCH", `/api/admin/agents/${encodeURIComponent(agentId)}/groups/${encodeURIComponent(groupId)}`, {
    access_level: accessLevel,
  });
}

export interface DashboardPermission {
  group_id: string;
  dashboard: string;
  allowed: boolean;
}

export interface DashboardDef {
  id: string;
  label: string;
  desc: string;
  section: "cluster" | "agent";
}

export async function getDashboardPermissions(): Promise<{ permissions: DashboardPermission[]; dashboards: DashboardDef[] }> {
  const res = await api<{ permissions: DashboardPermission[]; dashboards: DashboardDef[] }>("GET", "/api/admin/dashboards");
  return res;
}

export async function setDashboardPermission(
  groupId: string,
  dashboard: string,
  allowed: boolean
): Promise<void> {
  await api<void>(
    "PATCH",
    `/api/admin/groups/${encodeURIComponent(groupId)}/dashboards/${encodeURIComponent(dashboard)}`,
    { allowed }
  );
}

export async function createAdminUser(input: {
  email: string;
  password: string;
  name?: string;
  groups?: string[];
}): Promise<AdminUser> {
  const res = await api<{ user: AdminUser }>("POST", "/api/admin/users", input);
  return res.user;
}
