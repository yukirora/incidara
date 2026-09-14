import { useState, useEffect, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { patchSession, type Session } from "../api/sessions.ts";
import { listGroups, type GroupSummary } from "../api/groups.ts";

interface ShareSessionModalProps {
  open: boolean;
  session: Session;
  isOwner: boolean;
  onClose: () => void;
}

export function ShareSessionModal({ open, session, isOwner, onClose }: ShareSessionModalProps) {
  const queryClient = useQueryClient();
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: groupsData } = useQuery({
    queryKey: ["groups"],
    queryFn: listGroups,
    staleTime: 60_000,
    enabled: open,
  });
  const groups: GroupSummary[] = groupsData?.groups ?? [];

  // Sync current session sharing state when modal opens
  useEffect(() => {
    if (open) {
      setSelectedGroups(session.shared_with_groups ?? []);
      setError(null);
    }
  }, [open, session]);

  function toggleGroup(groupId: string) {
    setSelectedGroups((prev) =>
      prev.includes(groupId) ? prev.filter((g) => g !== groupId) : [...prev, groupId]
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!isOwner) return;
    setError(null);
    setSubmitting(true);
    try {
      await patchSession(session.id, {
        shared_with_groups: selectedGroups,
      });
      await queryClient.invalidateQueries({ queryKey: ["session", session.id] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save sharing settings");
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-zinc-900">Share session</h2>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-600 text-lg leading-none"
          >
            &times;
          </button>
        </div>

        {!isOwner ? (
          <div className="py-4 text-sm text-zinc-500 text-center">
            Only the session owner can manage sharing.
          </div>
        ) : (
          <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4">
            {/* Groups */}
            {groups.length > 0 ? (
              <div>
                <p className="text-sm font-medium text-zinc-700 mb-2">Share with groups</p>
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {groups.map((g) => (
                    <label key={g.id} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedGroups.includes(g.id)}
                        onChange={() => toggleGroup(g.id)}
                        className="rounded border-zinc-300"
                      />
                      <span className="text-sm text-zinc-700">{g.name}</span>
                      <span className="text-xs text-zinc-400">({g.id})</span>
                    </label>
                  ))}
                </div>
              </div>
            ) : (
              <div className="py-4 text-sm text-zinc-500 text-center">
                No groups available.
              </div>
            )}

            {error && (
              <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                {error}
              </div>
            )}

            <div className="flex gap-3 justify-end pt-2">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-sm text-zinc-600 border border-zinc-300 rounded-md hover:bg-zinc-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="px-4 py-2 text-sm bg-zinc-900 text-white rounded-md hover:bg-zinc-700 disabled:opacity-50"
              >
                {submitting ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
