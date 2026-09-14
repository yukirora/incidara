import { useState, useEffect, useRef } from "react";
import { changePassword } from "../api/auth.ts";
import { ApiError } from "../lib/api.ts";

interface ChangePasswordModalProps {
  onClose: () => void;
  onSuccess: () => void;
}

function passwordStrength(pw: string): { level: "weak" | "fair" | "good" | "strong"; label: string } {
  let score = 0;
  if (pw.length >= 8) score++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) score++;
  if (/\d/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  const labels = ["Too short — add more characters", "Fair — add numbers or symbols", "Good — add special characters", "Strong password"];
  const levels: ("weak" | "fair" | "good" | "strong")[] = ["weak", "fair", "good", "strong"];
  return { level: levels[score - 1] || "weak", label: labels[score - 1] || "Too short" };
}

export function ChangePasswordModal({ onClose, onSuccess }: ChangePasswordModalProps) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [apiError, setApiError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const strength = passwordStrength(newPassword);
  const strengthColor = { weak: "bg-red-500", fair: "bg-amber-400", good: "bg-green-500", strong: "bg-emerald-600" };
  const strengthWidth = { weak: "25%", fair: "50%", good: "75%", strong: "100%" };

  const currentRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    currentRef.current?.focus();
  }, []);

  const newErr = newPassword.length > 0 && newPassword.length < 8 ? "Password must be at least 8 characters" : null;
  const confirmErr = confirmPassword.length > 0 && confirmPassword !== newPassword ? "Passwords do not match" : null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setApiError(null);

    if (!currentPassword) { setApiError("Current password is required"); return; }
    if (newPassword.length < 8) { setApiError("New password must be at least 8 characters"); return; }
    if (newPassword !== confirmPassword) { setApiError("Passwords do not match"); return; }

    setSaving(true);
    try {
      await changePassword(currentPassword, newPassword);
      onSuccess();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setApiError(err.message);
      } else {
        setApiError("Failed to change password");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[2px]"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-xl w-[360px] max-w-[95vw] overflow-hidden">
        {/* Header */}
        <div className="px-5 pt-5 pb-4 border-b border-zinc-100">
          <h2 className="text-sm font-bold text-zinc-900">Change password</h2>
          <p className="text-xs text-zinc-500 mt-0.5">Update your account password</p>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit}>
          <div className="px-5 py-4 space-y-3">
            {apiError && (
              <div className="p-2.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700 flex items-start gap-1.5">
                <svg className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                </svg>
                {apiError}
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1">Current password</label>
              <input
                ref={currentRef}
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="Enter current password"
                className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-shadow"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1">New password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                placeholder="Min. 8 characters"
                className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-shadow"
              />
              {newPassword.length > 0 && (
                <div className="mt-1.5">
                  <div className="h-1 rounded-full bg-zinc-100 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${strengthColor[strength.level]}`}
                      style={{ width: strengthWidth[strength.level] }}
                    />
                  </div>
                  <p className={`text-[10px] mt-1 ${strength.level === "strong" ? "text-emerald-600" : strength.level === "good" ? "text-green-600" : "text-zinc-400"}`}>
                    {strength.label}
                  </p>
                </div>
              )}
              {newErr && newPassword.length > 0 && (
                <p className="text-[10px] text-red-500 mt-1">{newErr}</p>
              )}
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1">Confirm new password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
                placeholder="Repeat new password"
                className="w-full px-3 py-2 text-sm border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-shadow"
              />
              {confirmErr && (
                <p className="text-[10px] text-red-500 mt-1">{confirmErr}</p>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="px-5 pb-4 flex gap-2 justify-end">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-medium text-zinc-600 bg-zinc-100 hover:bg-zinc-200 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-3 py-1.5 text-xs font-semibold text-white bg-zinc-900 hover:bg-zinc-700 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1.5"
            >
              {saving ? (
                <>
                  <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Saving…
                </>
              ) : "Save password"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/** Success modal shown after password is changed */
export function ChangePasswordSuccess({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[2px]">
      <div className="bg-white rounded-xl shadow-xl w-[320px] max-w-[95vw] overflow-hidden text-center">
        <div className="px-6 pt-8 pb-4">
          <div className="w-12 h-12 rounded-full bg-emerald-50 border border-emerald-200 flex items-center justify-center mx-auto mb-3">
            <svg className="w-6 h-6 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-sm font-bold text-zinc-900">Password updated</h2>
          <p className="text-xs text-zinc-500 mt-1">Your password has been changed successfully.</p>
        </div>
        <div className="px-6 pb-6">
          <button
            onClick={onClose}
            className="w-full py-2 text-xs font-semibold text-white bg-zinc-900 hover:bg-zinc-700 rounded-lg transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
