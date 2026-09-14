import { useEffect, useRef } from "react";
import type { Task } from "../api/tasks.ts";
import type { GatewayEvent } from "../api/sessions.ts";
import { TaskCard } from "./TaskCard.tsx";

interface TimelineProps {
  tasks: Task[];
  events: GatewayEvent[];
  sessionId: number;
  canSend: boolean;
  onTaskCancelled?: () => void;
  onAutoFillComposer?: (text: string) => void;
}

/**
 * Groups events into per-task buckets.
 * A task's events are those with seq >= task.gateway_seq_start
 * and seq < nextTask.gateway_seq_start.
 * Tasks without a gateway_seq_start (recently-submitted pending) get an empty array.
 */
export function groupEventsByTask(
  tasks: Task[],
  events: GatewayEvent[]
): Map<number, GatewayEvent[]> {
  const result = new Map<number, GatewayEvent[]>();

  // Sort tasks by created_at ASC (oldest first)
  const sorted = [...tasks].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  );

  for (let i = 0; i < sorted.length; i++) {
    const task = sorted[i];
    let start = task.gateway_seq_start;

    // If gateway_seq_start is missing, infer from context
    if (start === null || start === undefined) {
      if (i === 0) {
        start = 0;
      } else {
        // Find the end of the previous task's assigned events
        const prevBucket = result.get(sorted[i - 1]?.id);
        if (prevBucket && prevBucket.length > 0) {
          start = prevBucket[prevBucket.length - 1].seq + 1;
        } else {
          // No prev events either — truly pending, no events to show
          result.set(task.id, []);
          continue;
        }
      }
    }

    // Find the start of the next task that has a seq_start
    let end: number | null = null;
    for (let j = i + 1; j < sorted.length; j++) {
      const next = sorted[j];
      if (next.gateway_seq_start !== null && next.gateway_seq_start !== undefined) {
        end = next.gateway_seq_start;
        break;
      }
    }

    const bucket = events.filter(
      (e) => e.seq >= start! && (end === null || e.seq < end)
    );
    result.set(task.id, bucket);
  }

  return result;
}

export function Timeline({ tasks, events, sessionId, canSend, onTaskCancelled, onAutoFillComposer }: TimelineProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Sort tasks oldest-first for display
  const sorted = [...tasks].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  );

  const eventsByTask = groupEventsByTask(tasks, events);

  // Scroll to bottom when new events arrive for active tasks,
  // but only if the user hasn't scrolled up to read earlier content.
  const hasActiveTask = tasks.some((t) =>
    ["running", "busy", "waiting_input"].includes(t.status)
  );

  // Track whether user is near the bottom of the scrollable area
  const nearBottom = useRef(true);

  useEffect(() => {
    // Find the scrollable ancestor (the overflow-y-auto container)
    const el = bottomRef.current?.parentElement;
    if (!el) return;
    // Walk up to find the scroll container
    let scroller: HTMLElement | null = el;
    while (scroller && getComputedStyle(scroller).overflowY !== "auto" && getComputedStyle(scroller).overflowY !== "scroll") {
      scroller = scroller.parentElement;
    }
    if (!scroller) return;

    const handleScroll = () => {
      const distFromBottom = scroller!.scrollHeight - scroller!.scrollTop - scroller!.clientHeight;
      nearBottom.current = distFromBottom < 150;
    };
    scroller.addEventListener("scroll", handleScroll, { passive: true });
    // Initialize
    handleScroll();
    return () => scroller!.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (hasActiveTask && nearBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events.length, hasActiveTask]);

  // Handle URL hash #task-N
  const hash = window.location.hash;
  const hashTaskId = hash.startsWith("#task-") ? parseInt(hash.slice(6), 10) : null;

  if (tasks.length === 0) {
    return (
      <div className="py-16 text-center text-sm text-zinc-400">
        No tasks yet
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {sorted.map((task, idx) => (
        <TaskCard
          key={task.id}
          task={task}
          taskIndex={idx}
          events={eventsByTask.get(task.id) ?? []}
          isLatest={idx === sorted.length - 1}
          sessionId={sessionId}
          canSend={canSend}
          onCancel={onTaskCancelled}
          highlight={task.id === hashTaskId}
          onAutoFillComposer={onAutoFillComposer}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
