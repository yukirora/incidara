// Preserved scheduler API.
import { Router } from "express";
import { randomUUID } from "node:crypto";
import type { SessionStore } from "../session-store.js";
import type { ClaudeAdapter } from "../claude-adapter.js";

type Deps = { store: SessionStore; adapter: ClaudeAdapter };

export type Schedule = {
  prompt: string;
  sessionKey: string | null;
  type: "once" | "recurring";
  interval: number;
  nextRun: number;
  createdAt: number;
  lastRun: number | null;
  lastResult: string | null;
  running: boolean;
};

/** Runs once per tick; triggers any schedule whose nextRun <= now. Testable. */
export function scheduleTick(
  schedules: Map<string, Schedule>,
  store: SessionStore,
  adapter: ClaudeAdapter,
  now: number,
): void {
  for (const [id, sched] of schedules) {
    if (sched.running || now < sched.nextRun) continue;
    sched.running = true;
    const cwd = process.env.CLAUDE_CWD || "/app/workspace";
    const session = store.createSession({ workspacePath: cwd, title: `schedule:${id}` });
    adapter.startSession(session, sched.prompt);
    sched.lastRun = now;
    sched.lastResult = `started session ${session.id}`;
    sched.running = false;
    if (sched.type === "once") schedules.delete(id);
    else sched.nextRun = now + sched.interval;
  }
}

export function scheduleRouter({ store, adapter }: Deps): Router {
  const r = Router();
  const schedules = new Map<string, Schedule>();

  setInterval(() => scheduleTick(schedules, store, adapter, Date.now()), 30_000);

  r.post("/", (req, res) => {
    const { prompt, session_key, delay, interval } = req.body ?? {};
    if (!prompt) return res.status(400).json({ error: "prompt is required" });
    if (!delay && !interval)
      return res.status(400).json({ error: "delay or interval (seconds) required" });
    const id = `sched-${randomUUID().slice(0, 8)}`;
    const now = Date.now();
    const sched: Schedule = {
      prompt, sessionKey: session_key || null,
      type: interval ? "recurring" : "once",
      interval: (interval || delay) * 1000,
      nextRun: now + (delay || interval) * 1000,
      createdAt: now, lastRun: null, lastResult: null, running: false,
    };
    schedules.set(id, sched);
    res.json({ id, ...sched });
  });

  r.get("/", (_req, res) => {
    const list = [...schedules.entries()].map(([id, s]) => ({
      id, type: s.type, prompt: s.prompt, sessionKey: s.sessionKey,
      interval: s.interval / 1000, running: s.running,
      nextRun: new Date(s.nextRun).toISOString(),
      createdAt: new Date(s.createdAt).toISOString(),
      lastRun: s.lastRun ? new Date(s.lastRun).toISOString() : null,
      lastResult: s.lastResult,
    }));
    res.json({ schedules: list });
  });

  r.delete("/:id", (req, res) => {
    if (!schedules.has(req.params.id)) return res.status(404).json({ error: "not found" });
    schedules.delete(req.params.id);
    res.json({ deleted: req.params.id });
  });

  return r;
}
