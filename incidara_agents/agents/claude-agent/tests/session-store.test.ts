import { describe, it, expect, beforeEach } from "vitest";
import { SessionStore } from "../src/session-store.js";

describe("SessionStore", () => {
  let store: SessionStore;
  beforeEach(() => { store = new SessionStore({ maxEventsPerSession: 5 }); });

  it("creates a session with generated id", () => {
    const s = store.createSession({ workspacePath: "/w", title: "t" });
    expect(s.id).toMatch(/^sess_/);
    expect(s.status).toBe("starting");
    expect(s.workspacePath).toBe("/w");
  });

  it("retrieves a session by id", () => {
    const s = store.createSession({ workspacePath: "/w" });
    expect(store.getSession(s.id)).toBe(s);
    expect(store.getSession("nope")).toBeUndefined();
  });

  it("lists all sessions", () => {
    store.createSession({ workspacePath: "/w" });
    store.createSession({ workspacePath: "/w" });
    expect(store.listSessions()).toHaveLength(2);
  });

  it("updates status and emits state_changed event", () => {
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "running");
    const events = store.getEventsSince(s.id, 0);
    const stateChanged = events.find(e => e.event_type === "session.state_changed");
    expect(stateChanged?.payload).toEqual({ from: "starting", to: "running" });
    expect(store.getSession(s.id)!.status).toBe("running");
  });

  it("appends events with monotonic seq", () => {
    const s = store.createSession({ workspacePath: "/w" });
    const e1 = store.appendEvent(s.id, "message.delta", { text: "a" });
    const e2 = store.appendEvent(s.id, "message.delta", { text: "b" });
    expect(e1.seq).toBe(1);
    expect(e2.seq).toBe(2);
  });

  it("ring buffer caps events per session", () => {
    const s = store.createSession({ workspacePath: "/w" });
    for (let i = 0; i < 10; i++) store.appendEvent(s.id, "message.delta", { text: String(i) });
    const events = store.getEventsSince(s.id, 0);
    expect(events).toHaveLength(5);
    expect(events[0].payload.text).toBe("5");
  });

  it("getEventsSince filters by after_seq", () => {
    const s = store.createSession({ workspacePath: "/w" });
    for (let i = 0; i < 3; i++) store.appendEvent(s.id, "message.delta", { text: String(i) });
    const events = store.getEventsSince(s.id, 1);
    expect(events).toHaveLength(2);
    expect(events[0].seq).toBe(2);
  });

  it("deletes a session", () => {
    const s = store.createSession({ workspacePath: "/w" });
    expect(store.deleteSession(s.id)).toBe(true);
    expect(store.getSession(s.id)).toBeUndefined();
    expect(store.deleteSession(s.id)).toBe(false);
  });
});
