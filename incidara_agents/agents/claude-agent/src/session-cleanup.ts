import type { SessionStore } from "./session-store.js";
import type { ClaudeAdapter } from "./claude-adapter.js";

/**
 * Remove sessions in terminal states whose updatedAt is older than `ttlMs`.
 * Only prunes `error` sessions — completed sessions are kept indefinitely
 * so they can be resumed from the chat-ui.
 */
export function cleanupCompletedSessions(store: SessionStore, ttlMs: number): number {
  const now = Date.now();
  let removed = 0;
  for (const s of store.listSessions()) {
    // Keep completed sessions — they may be resumed later
    if (s.status !== "error") continue;
    if (now - s.updatedAt.getTime() > ttlMs) {
      store.deleteSession(s.id);
      removed++;
    }
  }
  return removed;
}

/**
 * Interrupt any non-terminal sessions in the store. Used on graceful
 * shutdown (SIGTERM/SIGINT) so in-flight queries abort cleanly.
 */
export function shutdownActiveSessions(
  store: SessionStore,
  adapter: ClaudeAdapter,
): void {
  for (const s of store.listSessions()) {
    if (!["completed", "error"].includes(s.status)) {
      adapter.interrupt(s);
    }
  }
}
