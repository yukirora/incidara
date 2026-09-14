import { useQuery } from "@tanstack/react-query";
import { listTasks, type ListTasksParams, type ListTasksResult } from "../api/tasks.ts";

export function useTasks(params: ListTasksParams = {}): {
  tasks: ListTasksResult["tasks"];
  total: number;
  isLoading: boolean;
  isFetching: boolean;
  error: Error | null;
} {
  const { data, isLoading, isFetching, error } = useQuery<ListTasksResult>({
    queryKey: ["tasks", params],
    queryFn: () => listTasks(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });

  return {
    tasks: data?.tasks ?? [],
    total: data?.total ?? 0,
    isLoading,
    isFetching,
    error: error as Error | null,
  };
}
