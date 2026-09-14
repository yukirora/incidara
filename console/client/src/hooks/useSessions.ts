import { useQuery } from "@tanstack/react-query";
import { listSessions, type Session, type ListSessionsParams } from "../api/sessions.ts";

export function useSessions(params: ListSessionsParams = {}): {
  sessions: Session[];
  isLoading: boolean;
  error: Error | null;
} {
  const { data, isLoading, error } = useQuery<Session[]>({
    queryKey: ["sessions", params],
    queryFn: () => listSessions(params),
    staleTime: 30_000,
  });

  return {
    sessions: data ?? [],
    isLoading,
    error: error as Error | null,
  };
}
