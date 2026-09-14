import express from "express";
import { SessionStore } from "./session-store.js";
import { EventBus } from "./event-bus.js";
import { ClaudeAdapter } from "./claude-adapter.js";
import { FsPersistence } from "./persistence.js";
import { cleanupCompletedSessions, shutdownActiveSessions } from "./session-cleanup.js";
import { sessionsRouter } from "./routes/sessions.js";
import { messagesRouter } from "./routes/messages.js";
import { eventsRouter } from "./routes/events.js";
import { stateRouter } from "./routes/state.js";
import { compatRouter } from "./routes/compat.js";
import { scheduleRouter } from "./routes/schedule.js";
import { healthRouter } from "./routes/health.js";
import { usageRouter } from "./routes/usage.js";
import { PermissionManager, type PermissionRule } from "./permission-manager.js";
import type { Session, SessionStatus } from "./types.js";

const PORT = Number(process.env.PORT ?? 8000);
const SESSION_TTL_MS = Number(process.env.SESSION_TTL_MS ?? 60 * 60 * 1000); // 1h
const CLEANUP_INTERVAL_MS = 5 * 60 * 1000;
const META_SNAPSHOT_INTERVAL_MS = 10 * 1000;
const SESSIONS_DIR = process.env.SESSIONS_DIR ?? "/app/workspace/sessions";

const app = express();
app.use(express.json({ limit: "50mb" }));

const persistence = new FsPersistence(SESSIONS_DIR);
const store = new SessionStore();
const bus = new EventBus(persistence);

const defaultRules: PermissionRule[] = [
  { pattern: "Read(*)", decision: "allow" },
  { pattern: "Grep(*)", decision: "allow" },
  { pattern: "Glob(*)", decision: "allow" },
  { pattern: "Bash(echo *)", decision: "allow" },
  { pattern: "Bash(git *)", decision: "allow" },
  { pattern: "Bash(ls *)", decision: "allow" },
  { pattern: "Bash(cat *)", decision: "allow" },
  { pattern: "Bash(*)", decision: "ask" },
  { pattern: "Write(*)", decision: "ask" },
  { pattern: "Edit(*)", decision: "ask" },
  { pattern: "*", decision: "allow" },
];

const rules: PermissionRule[] = process.env.PERMISSION_RULES
  ? JSON.parse(process.env.PERMISSION_RULES)
  : defaultRules;

// Always prepend AskUserQuestion → "ask" so the UI can intercept it
// and show a question card. Without this rule, AskUserQuestion is
// auto-allowed and the tool fails silently in headless mode.
if (!rules.some((r) => r.pattern === "AskUserQuestion")) {
  rules.unshift({ pattern: "AskUserQuestion", decision: "ask" });
}
const permissionManager = new PermissionManager(
  rules,
  store,
  bus,
  Number(process.env.PERMISSION_TIMEOUT_MS ?? 2_147_483_647),
);

const adapter = new ClaudeAdapter(store, bus, {}, permissionManager);

// Recovery: restore persisted sessions from disk. Non-terminal sessions
// become "interrupted" (safe default — we can't resume a dead query loop).
let restoredCount = 0;
for (const meta of persistence.listMetas()) {
  const terminal = new Set<SessionStatus>(["completed", "error"]);
  const status: SessionStatus = terminal.has(meta.status as SessionStatus)
    ? (meta.status as SessionStatus)
    : "interrupted";
  const session: Session = {
    id: meta.id,
    claudeSessionId: meta.claudeSessionId,
    status,
    title: meta.title,
    workspacePath: meta.workspacePath,
    createdAt: new Date(meta.createdAt),
    updatedAt: new Date(meta.updatedAt),
    abortController: new AbortController(),
  };
  store.restoreSession(session, persistence.readEvents(meta.id));
  restoredCount++;
}
if (restoredCount) console.log(`[recovery] restored ${restoredCount} sessions from ${SESSIONS_DIR}`);

app.use("/sessions", sessionsRouter({ store, adapter }));
app.use("/sessions", messagesRouter({ store, adapter, permissionManager }));
app.use("/sessions", eventsRouter({ store, bus, persistence }));
app.use("/sessions", stateRouter({ store }));
app.use("/usage", usageRouter({ store }));
app.use("/v1", compatRouter({ store, bus, adapter }));
app.use("/v1/schedule", scheduleRouter({ store, adapter }));
app.use("/", healthRouter({ store }));

// Periodic meta snapshot so session metadata survives restart.
setInterval(() => {
  for (const s of store.listSessions()) {
    try { persistence.upsertMeta(s); }
    catch (err) { console.error(`[snapshot] meta upsert failed for ${s.id}:`, err); }
  }
}, META_SNAPSHOT_INTERVAL_MS);

// Cleanup disabled — sessions are lightweight in memory and persisted to disk.
// Keeping all sessions allows the chat-ui to resume/view them at any time.
// Uncomment to re-enable if memory becomes a concern with many sessions:
// setInterval(() => {
//   const removed = cleanupCompletedSessions(store, SESSION_TTL_MS);
//   if (removed) console.log(`[cleanup] pruned ${removed} terminal sessions`);
// }, CLEANUP_INTERVAL_MS);

const HOST = process.env.HOST || "127.0.0.1";
const server = app.listen(PORT, HOST, () => {
  console.log(`[gateway] listening on ${HOST}:${PORT}`);
});

function shutdown() {
  console.log("[gateway] shutting down");
  shutdownActiveSessions(store, adapter);
  // Snapshot metadata one last time so in-flight interrupts are persisted.
  for (const s of store.listSessions()) {
    try { persistence.upsertMeta(s); } catch {}
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10_000).unref();
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
