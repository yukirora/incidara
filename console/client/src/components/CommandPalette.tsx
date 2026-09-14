import { useState, useEffect, useRef, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAgents } from "../hooks/useAgents.ts";
import { useSessions } from "../hooks/useSessions.ts";
import { useUIState } from "../lib/store.ts";
import { interrupt } from "../api/sessions.ts";

interface PaletteItem {
  id: string;
  label: string;
  sublabel?: string;
  category: "session" | "agent" | "action";
  onSelect: () => void;
}

export function CommandPalette() {
  const { commandPaletteOpen, closeCommandPalette, openNewTaskModal } = useUIState();
  const navigate = useNavigate();
  const params = useParams<{ id?: string }>();
  const currentSessionId = params.id ? parseInt(params.id, 10) : null;

  const { agents } = useAgents();
  const { sessions } = useSessions({ scope: "mine" });

  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus input when opened
  useEffect(() => {
    if (commandPaletteOpen) {
      setQuery("");
      setSelectedIndex(0);
      // Small delay to ensure modal is mounted
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [commandPaletteOpen]);

  // Build items
  const items: PaletteItem[] = [];

  // Quick action: interrupt current session
  if (currentSessionId && !isNaN(currentSessionId)) {
    items.push({
      id: "action-interrupt",
      label: "Interrupt current session",
      category: "action",
      onSelect: () => {
        void interrupt(currentSessionId);
        closeCommandPalette();
      },
    });
  }

  // Agents
  for (const agent of agents) {
    items.push({
      id: `agent-${agent.id}`,
      label: agent.name,
      sublabel: "Agent",
      category: "agent",
      onSelect: () => {
        navigate(`/agents/${encodeURIComponent(agent.id)}`);
        closeCommandPalette();
      },
    });
    items.push({
      id: `action-newtask-${agent.id}`,
      label: `New task on ${agent.name}`,
      sublabel: "Action",
      category: "action",
      onSelect: () => {
        closeCommandPalette();
        openNewTaskModal(agent.id);
      },
    });
  }

  // Sessions
  for (const session of sessions) {
    items.push({
      id: `session-${session.id}`,
      label: session.title ?? `Session ${session.id}`,
      sublabel: `Session · ${session.agent_id}`,
      category: "session",
      onSelect: () => {
        navigate(`/sessions/${session.id}`);
        closeCommandPalette();
      },
    });
  }

  // Filter by query (case-insensitive substring)
  const lq = query.toLowerCase();
  const filtered = lq
    ? items.filter((item) =>
        item.label.toLowerCase().includes(lq) ||
        (item.sublabel?.toLowerCase().includes(lq) ?? false)
      )
    : items;

  // Clamp selectedIndex
  const safeIndex = Math.min(selectedIndex, Math.max(filtered.length - 1, 0));

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      closeCommandPalette();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = filtered[safeIndex];
      if (item) item.onSelect();
    }
  }

  // Reset selection when query changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  if (!commandPaletteOpen) return null;

  const categoryLabel: Record<string, string> = {
    action: "Actions",
    agent: "Agents",
    session: "Sessions",
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) closeCommandPalette(); }}
    >
      <div className="bg-white rounded-lg shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-200">
          <span className="text-zinc-400 text-sm">&#128269;</span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search agents, sessions, or actions…"
            className="flex-1 text-sm text-zinc-900 bg-transparent focus:outline-none placeholder-zinc-400"
          />
          <kbd className="text-xs text-zinc-400 border border-zinc-200 rounded px-1.5 py-0.5">
            Esc
          </kbd>
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-zinc-400">
              No results for "{query}"
            </div>
          ) : (
            (() => {
              const rendered: JSX.Element[] = [];
              let lastCategory = "";
              filtered.forEach((item, idx) => {
                if (item.category !== lastCategory) {
                  lastCategory = item.category;
                  rendered.push(
                    <div key={`cat-${item.category}`} className="px-4 py-1.5 text-xs font-semibold text-zinc-400 uppercase tracking-wide bg-zinc-50 border-b border-zinc-100">
                      {categoryLabel[item.category] ?? item.category}
                    </div>
                  );
                }
                rendered.push(
                  <button
                    key={item.id}
                    onClick={item.onSelect}
                    className={`w-full text-left px-4 py-2.5 flex items-center gap-3 transition-colors ${
                      idx === safeIndex ? "bg-zinc-100" : "hover:bg-zinc-50"
                    }`}
                    onMouseEnter={() => setSelectedIndex(idx)}
                  >
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-zinc-900 truncate">{item.label}</span>
                      {item.sublabel && (
                        <span className="block text-xs text-zinc-400">{item.sublabel}</span>
                      )}
                    </span>
                  </button>
                );
              });
              return rendered;
            })()
          )}
        </div>

        {/* Footer hint */}
        <div className="px-4 py-2 border-t border-zinc-100 flex items-center gap-3 text-xs text-zinc-400">
          <span><kbd className="border border-zinc-200 rounded px-1 py-0.5">↑↓</kbd> navigate</span>
          <span><kbd className="border border-zinc-200 rounded px-1 py-0.5">↵</kbd> select</span>
          <span><kbd className="border border-zinc-200 rounded px-1 py-0.5">Esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}
