import { useState } from "react";
import { NavLink } from "react-router-dom";
import { useMe } from "../hooks/useMe.ts";
import { useAgents } from "../hooks/useAgents.ts";
import { useSidebarState } from "../hooks/useSidebarState.ts";
import { UserProfileModal } from "./UserProfileModal.tsx";

export function Sidebar() {
  const { collapsed, toggle } = useSidebarState();
  const { user, is_admin, dashboard_access, agent_levels } = useMe();
  const { agents } = useAgents();
  const [showProfile, setShowProfile] = useState(false);

  // User has interactive access if they're admin or have interactive on any agent
  const hasInteractive = is_admin || Object.values(agent_levels).some((v) => v === "interactive");
  // User has any agent access (any level)
  const hasAnyAgent = is_admin || Object.keys(agent_levels).length > 0;

  const allAgents = agents;

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
      isActive
        ? "bg-zinc-100 text-zinc-900 font-medium"
        : "text-zinc-500 hover:bg-zinc-50 hover:text-zinc-700"
    }`;

  if (collapsed) {
    return (
      <aside className="flex flex-col w-14 h-full border-r border-zinc-200 bg-white flex-shrink-0">
        <div className="p-2 pt-3">
          <button
            onClick={toggle}
            className="w-10 h-10 flex items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100"
            title="Expand sidebar"
          >
            <span className="text-lg">&#9776;</span>
          </button>
        </div>
        <nav className="flex-1 p-2 space-y-1 overflow-y-auto">
          {hasAnyAgent && (
          <NavLink to="/" title="Overview" className={({ isActive }) =>
            `w-10 h-10 flex items-center justify-center rounded-lg text-lg ${isActive ? "bg-zinc-100" : "hover:bg-zinc-50"}`
          }>&#127968;</NavLink>
          )}
          {hasAnyAgent && (
          <NavLink to="/tasks" title="Tasks" className={({ isActive }) =>
            `w-10 h-10 flex items-center justify-center rounded-lg text-lg ${isActive ? "bg-zinc-100" : "hover:bg-zinc-50"}`
          }>&#10003;</NavLink>
          )}
          {is_admin && (
            <NavLink to="/admin" title="Admin" className={({ isActive }) =>
              `w-10 h-10 flex items-center justify-center rounded-lg text-lg ${isActive ? "bg-zinc-100" : "hover:bg-zinc-50"}`
            }>&#9881;</NavLink>
          )}
        </nav>
      </aside>
    );
  }

  return (
    <>
      <aside className="flex flex-col w-56 h-full border-r border-zinc-200 bg-white flex-shrink-0">
        {/* Collapse toggle */}
        <div className="px-3 pt-3 pb-1 flex-shrink-0">
          <button
            onClick={toggle}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 transition-colors"
            title="Collapse sidebar"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
        </div>

        {/* Scrollable middle section */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden">
          {/* Main Navigation — hidden if no agent access */}
          {hasAnyAgent && (
          <nav className="px-3 space-y-0.5">
            <NavLink to="/" end className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
              </svg>
              Overview
            </NavLink>
            <NavLink to="/tasks" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
              </svg>
              Tasks
            </NavLink>
            {hasInteractive && (
            <NavLink to="/schedules" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Schedules
            </NavLink>
            )}
          </nav>
          )}

          {/* Agents */}
          {allAgents.length > 0 && (
            <div className="px-3 pt-5">
              <p className="px-3 mb-2 text-[10px] font-semibold text-zinc-400 uppercase tracking-widest">Agents</p>
              <nav className="space-y-0.5">
                {allAgents.map((agent) => {
                  const hasActive = agent.counters.running > 0;
                  const hasWaiting = agent.counters.waiting > 0;
                  const dotColor = hasActive
                    ? "bg-emerald-500"
                    : hasWaiting
                    ? "bg-amber-500"
                    : "bg-stone-400";

                  return (
                    <NavLink
                      key={agent.id}
                      to={`/agents/${agent.id}`}
                      className={navLinkClass}
                    >
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor} ${hasActive || hasWaiting ? "animate-pulse" : ""}`} />
                      <span className="truncate">{agent.name}</span>
                      {agent.counters.running > 0 && (
                        <span className="ml-auto text-[10px] font-semibold text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded-full">
                          {agent.counters.running}
                        </span>
                      )}
                      {!hasActive && agent.counters.waiting > 0 && (
                        <span className="ml-auto text-[10px] font-semibold text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full">
                          {agent.counters.waiting}
                        </span>
                      )}
                    </NavLink>
                  );
                })}
              </nav>
            </div>
          )}

          {/* Dashboard */}
          <div className="px-3 pt-5 pb-2">
            <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">Dashboard</p>

            {/* Cluster — single section */}
            <p className="px-2 pb-0.5 text-[10px] font-medium text-zinc-400 mt-1">Cluster</p>
            <NavLink to="/reports/availability" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
              </svg>
              Availability
            </NavLink>
            {(is_admin || dashboard_access["reliability"]) && (
            <NavLink to="/reports/reliability" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
              Reliability
            </NavLink>
            )}
            {(is_admin || dashboard_access["jobs"]) && (
            <NavLink to="/reports/jobs" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M8.25 3v2.25M9.75 3v2.25M5.25 7.5h6M5.25 12h6m-6 4.5h6M3 5.625v12.75A1.125 1.125 0 004.125 19.5h15.75A1.125 1.125 0 0021 18.375V5.625A1.125 1.125 0 0019.875 4.5H4.125A1.125 1.125 0 003 5.625z" />
              </svg>
              Job Metrics
            </NavLink>
            )}
            {(is_admin || dashboard_access["utilization"]) && (
            <NavLink to="/reports/utilization" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5" />
              </svg>
              Utilization
            </NavLink>
            )}

            {/* Agent */}
            {(is_admin || dashboard_access["usage"]) && (
            <>
            <p className="px-2 pb-0.5 text-[10px] font-medium text-zinc-400 mt-2">Agent</p>
            <NavLink to="/reports/usage" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18.75a60.07 60.07 0 0115.797 2.101c.727.198 1.453-.342 1.453-1.096V18.75M3.75 4.5v.75A.75.75 0 013 6h-.75m0 0v-.375c0-.621.504-1.125 1.125-1.125H20.25M2.25 6v9m18-10.5v.75c0 .414.336.75.75.75h.75m-1.5-1.5h.375c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-.375m1.5-1.5H21a.75.75 0 00-.75.75v.75m0 0H3.75m0 0h-.375a1.125 1.125 0 01-1.125-1.125V15m1.5 1.5v-.75A.75.75 0 003 15h-.75M15 10.5a3 3 0 11-6 0 3 3 0 016 0zm3 0h.008v.008H18V10.5zm-12 0h.008v.008H6V10.5z" />
              </svg>
              Usage & Cost
            </NavLink>
            {is_admin && (
            <NavLink to="/reports/agent-metrics" className={navLinkClass}>
              <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
              Agent Metrics
            </NavLink>
            )}
            </>
            )}
          </div>

          {/* Admin */}
          {is_admin && (
            <div className="px-3 pb-2">
              <NavLink to="/admin" className={navLinkClass}>
                <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                Admin
              </NavLink>
            </div>
          )}
        </div>

        {/* User — pinned at bottom */}
        <div className="px-3 pb-3 border-t border-zinc-100 pt-2 flex-shrink-0">
          <button
            onClick={() => setShowProfile((v) => !v)}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-zinc-50 transition-colors"
          >
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-violet-400 to-indigo-500 flex items-center justify-center text-[10px] font-semibold text-white flex-shrink-0">
              {user?.email?.[0]?.toUpperCase() ?? "?"}
            </div>
            <div className="min-w-0 flex-1 text-left">
              <p className="text-xs font-medium text-zinc-700 truncate">
                {user?.email?.split("@")[0] ?? "User"}
              </p>
            </div>
            <svg className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 9l4-4 4 4m0 6l-4 4-4-4" />
            </svg>
          </button>
        </div>
      </aside>

      {showProfile && (
        <UserProfileModal onClose={() => setShowProfile(false)} />
      )}
    </>
  );
}
