import { describe, it, expect } from "vitest";
import { SessionStore } from "../src/session-store.js";
import { cleanupCompletedSessions } from "../src/session-cleanup.js";

describe("cleanupCompletedSessions", () => {
  it("keeps completed sessions so they can be resumed", () => {
    const store = new SessionStore();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "completed");
    s.updatedAt = new Date(Date.now() - 2 * 60 * 60 * 1000);
    const removed = cleanupCompletedSessions(store, 60 * 60 * 1000);
    expect(removed).toBe(0);
    expect(store.getSession(s.id)).toBeDefined();
  });

  it("keeps running sessions", () => {
    const store = new SessionStore();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "running");
    s.updatedAt = new Date(Date.now() - 2 * 60 * 60 * 1000);
    expect(cleanupCompletedSessions(store, 60 * 60 * 1000)).toBe(0);
    expect(store.getSession(s.id)).toBeDefined();
  });

  it("removes error sessions older than ttl", () => {
    const store = new SessionStore();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "error");
    s.updatedAt = new Date(Date.now() - 2 * 60 * 60 * 1000);
    expect(cleanupCompletedSessions(store, 60 * 60 * 1000)).toBe(1);
  });

  it("keeps recently-completed sessions", () => {
    const store = new SessionStore();
    const s = store.createSession({ workspacePath: "/w" });
    store.setStatus(s.id, "completed");
    expect(cleanupCompletedSessions(store, 60 * 60 * 1000)).toBe(0);
    expect(store.getSession(s.id)).toBeDefined();
  });
});
