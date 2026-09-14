import { useQuery } from "@tanstack/react-query";
import { me, type MeResponse } from "../api/auth.ts";

export function useMe() {
  const { data, isLoading } = useQuery<MeResponse | null>({
    queryKey: ["me"],
    queryFn: me,
    staleTime: 60_000,
    retry: false,
  });

  return {
    user: data?.user ?? null,
    groups: data?.groups ?? [],
    is_admin: data?.is_admin ?? false,
    agent_levels: data?.agent_levels ?? {},
    dashboard_access: data?.dashboard_access ?? {},
    isLoading,
  };
}
