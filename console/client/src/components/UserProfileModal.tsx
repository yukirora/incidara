import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, Link } from "react-router-dom";
import { useMe } from "../hooks/useMe.ts";
import { logout } from "../api/auth.ts";

interface UserProfileModalProps {
  onClose: () => void;
}

export function UserProfileModal({ onClose }: UserProfileModalProps) {
  const { user, groups, is_admin } = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  async function handleLogout() {
    await logout();
    await queryClient.invalidateQueries({ queryKey: ["me"] });
    onClose();
    navigate("/login");
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-start pb-16 pl-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-lg border border-zinc-200 w-72 p-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-zinc-900">Profile</h3>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-600 text-lg leading-none"
          >
            &times;
          </button>
        </div>

        {/* User info */}
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-zinc-200 flex items-center justify-center text-sm font-semibold text-zinc-600 flex-shrink-0">
            {user?.email?.[0]?.toUpperCase() ?? "?"}
          </div>
          <div className="min-w-0">
            {user?.name && (
              <p className="text-sm font-medium text-zinc-900 truncate">{user.name}</p>
            )}
            <p className="text-xs text-zinc-500 truncate">{user?.email}</p>
          </div>
        </div>

        {/* Groups */}
        {groups.length > 0 && (
          <div className="mb-4">
            <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
              Groups
            </p>
            <div className="flex flex-wrap gap-1">
              {groups.map((g) => (
                <span
                  key={g}
                  className="px-2 py-0.5 bg-zinc-100 text-zinc-600 text-xs rounded-full"
                >
                  {g}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Admin link */}
        {is_admin && (
          <div className="mb-3">
            <Link
              to="/admin"
              onClick={onClose}
              className="flex items-center gap-2 text-sm text-zinc-700 hover:text-zinc-900 py-1"
            >
              <span>&#9881;</span>
              <span>Admin panel</span>
            </Link>
          </div>
        )}

        {/* Logout */}
        <button
          onClick={() => { void handleLogout(); }}
          className="w-full text-sm text-left text-red-600 hover:text-red-700 py-1 border-t border-zinc-100 pt-3 mt-1"
        >
          Log out
        </button>
      </div>
    </div>
  );
}
