import { api } from "../lib/api.ts";

export interface GroupSummary {
  id: string;
  name: string;
}

export async function listGroups(): Promise<{ groups: GroupSummary[] }> {
  return api<{ groups: GroupSummary[] }>("GET", "/api/groups");
}
