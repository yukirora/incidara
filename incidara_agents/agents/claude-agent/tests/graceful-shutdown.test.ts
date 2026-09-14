import { describe, it, expect } from "vitest";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { shutdownActiveSessions } from "../src/session-cleanup.js";

describe("shutdownActiveSessions", () => {
  it("interrupts running sessions and leaves terminal ones alone", () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (async function* () {}) as any });
    const interrupted: string[] = [];
    adapter.interrupt = (s) => { interrupted.push(s.id); };

    const a = store.createSession({ workspacePath: "/w" });
    const b = store.createSession({ workspacePath: "/w" });
    const c = store.createSession({ workspacePath: "/w" });
    const d = store.createSession({ workspacePath: "/w" });
    store.setStatus(a.id, "running");
    store.setStatus(b.id, "busy");
    store.setStatus(c.id, "completed");
    store.setStatus(d.id, "error");

    shutdownActiveSessions(store, adapter);
    expect(new Set(interrupted)).toEqual(new Set([a.id, b.id]));
  });

  it("no-op when all sessions terminal", () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (async function* () {}) as any });
    const interrupted: string[] = [];
    adapter.interrupt = (s) => { interrupted.push(s.id); };

    const a = store.createSession({ workspacePath: "/w" });
    store.setStatus(a.id, "completed");
    shutdownActiveSessions(store, adapter);
    expect(interrupted).toHaveLength(0);
  });
});
