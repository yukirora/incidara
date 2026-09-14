import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { logout } from "../api/auth.ts";
import { useMe } from "../hooks/useMe.ts";
import { queryClient } from "../main.tsx";
import { ChangePasswordModal, ChangePasswordSuccess } from "./ChangePasswordModal.tsx";
import type { AccessLevel } from "../api/auth.ts";

const ACCESS_LABELS: Record<AccessLevel, string> = {
  interactive: "● interactive",
  readonly: "○ readonly",
  readonly_summary: "◐ readonly_summary",
  readonly_input: "◑ readonly_input",
};

const ACCESS_COLORS: Record<AccessLevel, string> = {
  interactive: "bg-emerald-50 text-emerald-700",
  readonly: "bg-blue-50 text-blue-700",
  readonly_summary: "bg-violet-50 text-violet-700",
  readonly_input: "bg-orange-50 text-orange-700",
};

export function Header() {
  const navigate = useNavigate();
  const { user, agent_levels } = useMe();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const avatarBtnRef = useRef<HTMLButtonElement>(null);

  const initial = user?.email?.[0]?.toUpperCase() ?? "?";
  const displayName = user?.email?.split("@")[0] ?? "";

  const effectiveLevel = effectiveAccessLevel(agent_levels);

  function effectiveAccessLevel(levels: Record<string, AccessLevel>): AccessLevel | null {
    const order: AccessLevel[] = ["interactive", "readonly", "readonly_input", "readonly_summary"];
    let best: AccessLevel | null = null;
    for (const [, level] of Object.entries(levels)) {
      if (!best || order.indexOf(level) > order.indexOf(best)) {
        best = level;
      }
    }
    return best;
  }

  useEffect(() => {
    if (!dropdownOpen) return;
    function handler(e: MouseEvent) {
      if (
        dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
        avatarBtnRef.current && !avatarBtnRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [dropdownOpen]);

  function handleChangePassword() {
    setDropdownOpen(false);
    setShowChangePassword(true);
  }

  function handlePasswordSuccess() {
    setShowChangePassword(false);
    setShowSuccess(true);
  }

  function handleSuccessClose() {
    setShowSuccess(false);
  }

  function handleAccount() {
    setDropdownOpen(false);
    navigate("/admin");
  }

  async function handleSignOut() {
    setDropdownOpen(false);
    try {
      await logout();
    } finally {
      queryClient.clear();
      navigate("/login");
    }
  }

  return (
    <>
      <header className="bg-white border-b border-zinc-200 px-4 py-2.5 flex items-center justify-between flex-shrink-0 z-30">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center shadow-sm shadow-indigo-200">
            <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <h1 className="text-sm font-bold tracking-tight text-zinc-900">Incidara</h1>
        </div>

        <div className="flex items-center gap-2">
          <div className="w-px h-5 bg-zinc-200 mx-0.5" />

          {/* User dropdown */}
          <div className="relative" ref={dropdownRef}>
            <button
              ref={avatarBtnRef}
              onClick={() => setDropdownOpen((v) => !v)}
              className={`flex items-center gap-2 pl-1 pr-2 py-1 rounded-lg text-xs transition-colors ${
                dropdownOpen
                  ? "bg-zinc-100 border border-zinc-300"
                  : "hover:bg-zinc-50 border border-transparent"
              }`}
              title={user?.email ?? ""}
            >
              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-violet-400 to-indigo-500 flex items-center justify-center text-[10px] font-bold text-white flex-shrink-0">
                {initial}
              </div>
              <span className="text-zinc-600 font-medium hidden sm:block max-w-[100px] truncate">
                {displayName}
              </span>
              <svg
                className={`w-3.5 h-3.5 text-zinc-400 flex-shrink-0 transition-transform ${dropdownOpen ? "rotate-180" : ""}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {dropdownOpen && (
              <div className="absolute top-full right-0 mt-1.5 bg-white border border-zinc-200 rounded-xl shadow-lg shadow-zinc-200/80 z-50 w-56 overflow-hidden">
                <div className="px-3.5 py-2.5 border-b border-zinc-100">
                  <p className="text-xs font-semibold text-zinc-900 truncate">{user?.email}</p>
                  {user?.name && (
                    <p className="text-[11px] text-zinc-500 mt-0.5 truncate">{user.name}</p>
                  )}
                  {effectiveLevel && (
                    <div className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold mt-1.5 ${ACCESS_COLORS[effectiveLevel]}`}>
                      {ACCESS_LABELS[effectiveLevel]}
                    </div>
                  )}
                </div>

                <div className="py-1">
                  <button
                    onClick={handleChangePassword}
                    className="w-full flex items-center gap-2.5 px-3.5 py-2 text-xs text-zinc-700 hover:bg-zinc-50 transition-colors"
                  >
                    <svg className="w-3.5 h-3.5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                    </svg>
                    Change password
                  </button>
                  <button
                    onClick={handleAccount}
                    className="w-full flex items-center gap-2.5 px-3.5 py-2 text-xs text-zinc-700 hover:bg-zinc-50 transition-colors"
                  >
                    <svg className="w-3.5 h-3.5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    Account settings
                  </button>
                </div>

                <div className="border-t border-zinc-100 py-1">
                  <button
                    onClick={handleSignOut}
                    className="w-full flex items-center gap-2.5 px-3.5 py-2 text-xs text-red-600 hover:bg-red-50 transition-colors"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                    </svg>
                    Sign out
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      {showChangePassword && (
        <ChangePasswordModal
          onClose={() => setShowChangePassword(false)}
          onSuccess={handlePasswordSuccess}
        />
      )}
      {showSuccess && (
        <ChangePasswordSuccess onClose={handleSuccessClose} />
      )}
    </>
  );
}
