import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listSchedules,
  getSchedule,
  createSchedule,
  patchSchedule,
  deleteSchedule,
  runNow,
  type ListSchedulesParams,
  type CreateScheduleInput,
  type PatchScheduleInput,
  type Schedule,
} from "../api/schedules.ts";

export function useSchedules(params: ListSchedulesParams = {}): {
  schedules: Schedule[];
  isLoading: boolean;
  error: Error | null;
} {
  const { data, isLoading, error } = useQuery<Schedule[]>({
    queryKey: ["schedules", params],
    queryFn: () => listSchedules(params),
    staleTime: 15_000,
  });

  return {
    schedules: data ?? [],
    isLoading,
    error: error as Error | null,
  };
}

export function useSchedule(id: number | null): {
  schedule: Schedule | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useQuery<Schedule>({
    queryKey: ["schedules", id],
    queryFn: () => getSchedule(id!),
    enabled: id !== null,
    staleTime: 15_000,
  });

  return { schedule: data, isLoading };
}

export function useCreateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateScheduleInput) => createSchedule(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
}

export function usePatchSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: PatchScheduleInput }) =>
      patchSchedule(id, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
}

export function useDeleteSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteSchedule(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
}

export function useRunNow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => runNow(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
}
