import { useState } from "react";
import { useSchedules } from "../hooks/useSchedules.ts";
import { ScheduleRow } from "../components/ScheduleRow.tsx";
import { ScheduleFormModal } from "../components/ScheduleFormModal.tsx";
import type { Schedule } from "../api/schedules.ts";

export function Schedules() {
  const { schedules, isLoading, error } = useSchedules();
  const [modalOpen, setModalOpen] = useState(false);
  const [editSchedule, setEditSchedule] = useState<Schedule | undefined>(undefined);

  function handleNew() {
    setEditSchedule(undefined);
    setModalOpen(true);
  }

  function handleEdit(schedule: Schedule) {
    setEditSchedule(schedule);
    setModalOpen(true);
  }

  function handleClose() {
    setModalOpen(false);
    setEditSchedule(undefined);
  }

  return (
    <div className="max-w-3xl mx-auto py-8 px-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold text-zinc-900">Schedules</h1>
        <button
          onClick={handleNew}
          className="flex items-center gap-1.5 px-3 py-2 bg-zinc-900 text-white text-sm font-medium rounded-md hover:bg-zinc-700"
        >
          <span>+</span>
          <span>New schedule</span>
        </button>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="text-sm text-zinc-400 py-12 text-center">Loading schedules…</div>
      ) : error ? (
        <div className="text-sm text-red-600 py-12 text-center">
          Failed to load schedules: {error.message}
        </div>
      ) : schedules.length === 0 ? (
        <div className="py-16 text-center">
          <p className="text-zinc-500 text-sm mb-4">No schedules yet.</p>
          <button
            onClick={handleNew}
            className="px-4 py-2 bg-zinc-900 text-white text-sm font-medium rounded-md hover:bg-zinc-700"
          >
            Create your first schedule
          </button>
        </div>
      ) : (
        <div className="border border-zinc-200 rounded-lg overflow-hidden">
          {schedules.map((schedule) => (
            <ScheduleRow
              key={schedule.id}
              schedule={schedule}
              onEdit={handleEdit}
            />
          ))}
        </div>
      )}

      {/* Modal */}
      <ScheduleFormModal
        open={modalOpen}
        onClose={handleClose}
        editSchedule={editSchedule}
      />
    </div>
  );
}
