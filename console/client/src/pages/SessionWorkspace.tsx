import { useState, useEffect, useCallback, useRef } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getSession,
  getState,
  getEventsHistory,
  getRounds,
  eventStreamUrl,
  type Session,
  type SessionState,
  type GatewayEvent,
  type Round,
} from "../api/sessions.ts";
import { listTasks } from "../api/tasks.ts";
import { useMe } from "../hooks/useMe.ts";
import { useAgent } from "../hooks/useAgents.ts";
import { useSse } from "../hooks/useSse.ts";
import { Timeline } from "../components/Timeline.tsx";
import { Composer } from "../components/Composer.tsx";
import { SessionHeader } from "../components/SessionHeader.tsx";
import { Inspector } from "../components/Inspector.tsx";

const INSPECTOR_KEY = "chatui.workspace.inspector";

function readInspectorPref(): boolean {
  try {
    return localStorage.getItem(INSPECTOR_KEY) === "true";
  } catch {
    return false;
  }
}

export function SessionWorkspace() {
  const { id: idStr } = useParams<{ id: string }>();
  const sessionId = parseInt(idStr ?? "", 10);
  const { user: me, is_admin, agent_levels } = useMe();
  const queryClient = useQueryClient();

  const [session, setSession] = useState<Session | null>(null);
  const [state, setState] = useState<SessionState | null>(null);
  const [events, setEvents] = useState<GatewayEvent[]>([]);
  const [sseUrl, setSseUrl] = useState<string | null>(null);
  const [showInspector, setShowInspector] = useState(readInspectorPref);
  const [everConnected, setEverConnected] = useState(false);
  const [composerPrefill, setComposerPrefill] = useState<string | null>(null);

  // Track the highest seq we've seen so we can use it for SSE reconnect
  const latestSeqRef = useRef<number>(0);

  // Load session metadata
  const { data: sessionData, error: sessionError, isLoading: sessionLoading } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId),
    enabled: !isNaN(sessionId),
    staleTime: 30_000,
  });

  // Load tasks for this session
  const { data: tasksData, refetch: refetchTasks } = useQuery({
    queryKey: ["tasks", { session_id: sessionId }],
    queryFn: () => listTasks({ session_id: sessionId, limit: 100 }),
    enabled: !isNaN(sessionId),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

  const tasks = tasksData?.tasks ?? [];

  // Load gateway state (poll every 10s as safety net)
  const { data: stateData } = useQuery({
    queryKey: ["session-state", sessionId],
    queryFn: () => getState(sessionId),
    enabled: !isNaN(sessionId),
    staleTime: 8_000,
    refetchInterval: 10_000,
  });

  useEffect(() => {
    if (stateData) setState(stateData);
  }, [stateData]);

  const ROUNDS_PER_PAGE = 10;
  const [rounds, setRounds] = useState<Round[]>([]);
  const [visibleRoundCount, setVisibleRoundCount] = useState(ROUNDS_PER_PAGE);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  // Step 1: Load round boundaries on mount
  useEffect(() => {
    if (isNaN(sessionId)) return;
    getRounds(sessionId).then((res) => {
      setRounds(res.rounds);
    }).catch(() => {});
  }, [sessionId]);

  // Step 2: Load events for the visible rounds
  useEffect(() => {
    if (isNaN(sessionId)) return;
    setLoadingEvents(true);

    // Determine which rounds to show (latest N)
    const visibleRounds = rounds.slice(-visibleRoundCount);
    const startSeq = visibleRounds.length > 0 ? visibleRounds[0].seq - 1 : 0;

    async function loadRange() {
      const allEvents: GatewayEvent[] = [];
      let cursor = Math.max(0, startSeq);

      // Paginate forward from startSeq.
      // Do NOT use before_seq — it creates small windows that bypass server-side
      // compaction (which only runs on pages > 50 events). Without before_seq,
      // each 500-event page from the gateway gets compacted (merging tool.stdout
      // into tool.started input, dropping redundant deltas, etc).
      // Use next_after_seq to detect the last page, NOT events.length —
      // server-side compaction may strip all events from a page.
      for (;;) {
        try {
          const res = await getEventsHistory(sessionId, cursor);
          if (res.events.length > 0) {
            allEvents.push(...res.events);
          }
          if (!res.next_after_seq || res.next_after_seq <= cursor) break;
          cursor = res.next_after_seq;
        } catch { break; }
      }

      setEvents(allEvents);
      if (allEvents.length > 0) {
        latestSeqRef.current = Math.max(...allEvents.map((e) => e.seq));
      } else if (cursor > 0) {
        // Even with 0 compacted events, we know the session has content
        latestSeqRef.current = cursor;
      }
      setSseUrl(eventStreamUrl(sessionId, latestSeqRef.current));
      setLoadingEvents(false);
    }

    void loadRange();
  }, [sessionId, rounds, visibleRoundCount]);

  // Load more older rounds
  function loadMoreRounds() {
    setLoadingMore(true);
    setVisibleRoundCount((prev) => prev + ROUNDS_PER_PAGE);
    // The useEffect above will re-fire and load the expanded range
    setLoadingMore(false);
  }

  const hasMoreRounds = visibleRoundCount < rounds.length;

  // Sync session data from query
  useEffect(() => {
    if (sessionData?.session) {
      setSession(sessionData.session);
    }
  }, [sessionData]);

  // Persist inspector toggle preference
  function toggleInspector() {
    setShowInspector((prev) => {
      const next = !prev;
      try { localStorage.setItem(INSPECTOR_KEY, String(next)); } catch { /* ignore */ }
      return next;
    });
  }

  // SSE event handler
  const handleSseEvent = useCallback((evt: MessageEvent) => {
    try {
      const gwEvent = JSON.parse(evt.data as string) as GatewayEvent;
      setEvents((prev) => {
        // Deduplicate by seq
        if (prev.some((e) => e.seq === gwEvent.seq)) return prev;
        const next = [...prev, gwEvent].sort((a, b) => a.seq - b.seq);
        return next;
      });
      if (gwEvent.seq > latestSeqRef.current) {
        latestSeqRef.current = gwEvent.seq;
      }

      // When a new user message arrives via SSE, add it to the rounds array too
      if (gwEvent.event_type === "message.user") {
        const preview = String((gwEvent.payload as Record<string, unknown>).content ?? "").slice(0, 80);
        setRounds((prev) => {
          if (prev.some((r) => r.seq === gwEvent.seq)) return prev;
          return [...prev, { seq: gwEvent.seq, preview }];
        });
      }

      // Update state from state_changed events
      if (gwEvent.event_type === "session.state_changed") {
        const payload = gwEvent.payload as Partial<SessionState>;
        setState((prev) =>
          prev
            ? { ...prev, ...payload, latest_seq: gwEvent.seq }
            : { session_id: gwEvent.session_id, status: "running", waiting_for_input: false, latest_seq: gwEvent.seq, ...payload }
        );
      }
      if (gwEvent.event_type === "session.completed") {
        setState((prev) => prev ? { ...prev, status: "completed" } : null);
        void refetchTasks();
      }
      if (gwEvent.event_type === "session.error") {
        setState((prev) => prev ? { ...prev, status: "error" } : null);
        void refetchTasks();
      }
      if (gwEvent.event_type === "session.interrupted") {
        setState((prev) => prev ? { ...prev, status: "interrupted" } : null);
        void refetchTasks();
      }
      if (gwEvent.event_type === "session.waiting_input") {
        setState((prev) => prev ? { ...prev, status: "waiting_input", waiting_for_input: true } : null);
        void refetchTasks();
      }
    } catch {
      // ignore parse errors
    }
  }, [refetchTasks]);

  const { connected } = useSse(sseUrl, handleSseEvent);

  // Track when we first connect so we can distinguish "never connected" vs "disconnected"
  useEffect(() => {
    if (connected) setEverConnected(true);
  }, [connected]);

  const { agent: sessionAgent } = useAgent(session?.agent_id ?? "");
  const isSessionOwner = me ? session?.owner_email === me.email : false;
  // Admins can always interact; group users with agent access can interact too
  const isOwner = is_admin || isSessionOwner || !!sessionAgent;
  // Can send messages only if agent level is interactive
  const agentLevel = session?.agent_id ? agent_levels[session.agent_id] : undefined;
  const canSend = agentLevel === "interactive";

  // Task-level pagination: default show 1 task (or hash-targeted), "load more" for older
  const TASKS_PER_PAGE = 1;
  const [visibleTaskCount, setVisibleTaskCount] = useState(TASKS_PER_PAGE);

  // Sort tasks newest-first for picking which to show
  const sortedTasks = [...tasks].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );
  const latestTask = sortedTasks[0];
  const pendingCount = tasks.filter((t) => t.status === "pending").length;
  const currentTaskStatus = latestTask?.status ?? "none";

  // If URL has #task-N, make sure that task is visible
  const hash = typeof window !== "undefined" ? window.location.hash : "";
  const hashTaskId = hash.startsWith("#task-") ? parseInt(hash.slice(6), 10) : null;

  // Determine visible tasks: latest N, plus the hash-targeted task if outside that range
  const visibleTasks = (() => {
    const latest = sortedTasks.slice(0, visibleTaskCount);
    if (hashTaskId && !latest.some((t) => t.id === hashTaskId)) {
      const target = tasks.find((t) => t.id === hashTaskId);
      if (target) latest.push(target);
    }
    // Sort oldest-first for display
    return latest.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  })();

  const hasMoreTasks = visibleTaskCount < tasks.length;

  function loadMoreTasks() {
    setVisibleTaskCount((prev) => prev + 5);
  }

  function handleTaskCancelled() {
    void refetchTasks();
  }

  function handleComposerSent() {
    void refetchTasks();
    // Invalidate tasks query to get fresh state
    void queryClient.invalidateQueries({ queryKey: ["tasks", { session_id: sessionId }] });
  }

  if (isNaN(sessionId)) {
    return <div className="p-6 text-sm text-red-500">Invalid session ID</div>;
  }

  if (sessionLoading) {
    return <div className="p-6 text-sm text-zinc-400">Loading session…</div>;
  }

  if (sessionError || !session) {
    return (
      <div className="p-6 text-sm text-red-500">
        {sessionError instanceof Error ? sessionError.message : "Session not found"}
      </div>
    );
  }

  const showDisconnectBanner = everConnected && !connected;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <SessionHeader
        session={session}
        state={state}
        isOwner={isOwner}
        currentTask={latestTask}
        onSessionUpdated={setSession}
        onTaskUpdated={() => { void refetchTasks(); }}
        showInspector={showInspector}
        onToggleInspector={toggleInspector}
      />

      {/* Disconnection banner */}
      {showDisconnectBanner && (
        <div className="flex items-center gap-2 px-4 py-1.5 bg-amber-50 border-b border-amber-200 text-xs text-amber-700">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
          Disconnected · Reconnecting…
        </div>
      )}

      {/* SSE connection indicator */}
      <div className="flex items-center gap-2 px-4 py-1 bg-zinc-50 border-b border-zinc-100 text-xs text-zinc-400">
        <span
          className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-zinc-300 animate-pulse"}`}
        />
        {connected ? "Live" : "Connecting…"}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Main timeline */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto px-4 py-4">
            {/* Load more tasks button */}
            {hasMoreTasks && !loadingEvents && (
              <div className="flex justify-center mb-3">
                <button
                  onClick={loadMoreTasks}
                  className="text-xs px-3 py-1.5 bg-zinc-100 text-zinc-600 rounded-full hover:bg-zinc-200 transition-colors"
                >
                  ↑ Load earlier tasks ({tasks.length - visibleTaskCount} more)
                </button>
              </div>
            )}
            {/* Load more rounds within tasks */}
            {hasMoreRounds && !loadingEvents && (
              <div className="flex justify-center mb-3">
                <button
                  onClick={loadMoreRounds}
                  disabled={loadingMore}
                  className="text-xs px-3 py-1.5 bg-violet-50 text-violet-600 rounded-full hover:bg-violet-100 disabled:opacity-50 transition-colors"
                >
                  {loadingMore ? "Loading…" : `↑ Load earlier rounds (${rounds.length - visibleRoundCount} more)`}
                </button>
              </div>
            )}
            {loadingEvents && (
              <div className="flex justify-center py-4">
                <span className="text-xs text-zinc-400 animate-pulse">Loading events…</span>
              </div>
            )}
            {!loadingEvents && events.length === 0 && tasks.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <p className="text-sm text-zinc-400">Waiting for the agent to start…</p>
              </div>
            ) : (
              <Timeline
                tasks={visibleTasks}
                events={events}
                sessionId={sessionId}
                canSend={canSend}
                onTaskCancelled={handleTaskCancelled}
                onAutoFillComposer={(text) => setComposerPrefill(text)}
              />
            )}
          </div>

          {/* Composer — only show if user can send */}
          {canSend && (
          <Composer
            sessionId={sessionId}
            currentTaskStatus={currentTaskStatus as Parameters<typeof Composer>[0]["currentTaskStatus"]}
            currentTaskId={latestTask?.id}
            currentTaskCompletionMode={latestTask?.completion_mode}
            pendingCount={pendingCount}
            isOwner={isOwner}
            onSent={handleComposerSent}
            prefillText={composerPrefill}
            onPrefillConsumed={() => setComposerPrefill(null)}
          />
          )}
        </div>

        {/* Right inspector panel */}
        {showInspector && (
          <Inspector state={state} events={events} />
        )}
      </div>
    </div>
  );
}
