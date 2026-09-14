interface ConfirmModalProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  destructive = false,
  onConfirm,
  onClose,
}: ConfirmModalProps) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-sm mx-4 p-6">
        <h2 className="text-base font-semibold text-zinc-900 mb-2">{title}</h2>
        <p className="text-sm text-zinc-600 mb-5">{message}</p>
        <div className="flex gap-3 justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-zinc-600 border border-zinc-300 rounded-md hover:bg-zinc-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 text-sm rounded-md text-white font-medium ${
              destructive
                ? "bg-red-600 hover:bg-red-700"
                : "bg-zinc-900 hover:bg-zinc-700"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
