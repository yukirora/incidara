import { describe, it, expect, beforeEach, afterAll, beforeAll } from "vitest";
import { createPool } from "./db/client.js";
import { SessionStore } from "./session-store.js";
import { TaskStore } from "./task-store.js";
import { signup } from "./auth.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;
let gwIdCounter = 0;

describe.skipIf(!DB_URL)("TaskStore", () => {
  let pool: pg.Pool;
  let sessionStore: SessionStore;
  let taskStore: TaskStore;
  let sessionId: number;
  const ownerEmail = `task_owner_${Date.now()}@example.com`;
  const otherEmail = `task_other_${Date.now()}@example.com`;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    sessionStore = new SessionStore(pool);
    taskStore = new TaskStore(pool);
    await signup(pool, { email: ownerEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: otherEmail, password: "testpass1234" }).catch(() => {});
  });

  afterAll(async () => {
    await pool.query("DELETE FROM tasks WHERE submitter_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM users WHERE email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.end();
  });

  beforeEach(async () => {
    // Scoped deletes to avoid cross-test interference
    await pool.query("DELETE FROM tasks WHERE submitter_email = ANY($1)", [[ownerEmail, otherEmail]]);
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]);
    // Create a fresh session for each test
    const session = await sessionStore.create({
      gatewaySessionId: `gw-task-test-${Date.now()}-${++gwIdCounter}`,
      agentId: "agent-a",
      ownerEmail,
    });
    sessionId = session.id;
  });

  it("creates a task with status=running and sets started_at", async () => {
    const task = await taskStore.create({
      sessionId,
      prompt: "hello world",
      submitterEmail: ownerEmail,
      status: "running",
    });

    expect(task.id).toBeTypeOf("number");
    expect(task.session_id).toBe(sessionId);
    expect(task.prompt).toBe("hello world");
    expect(task.status).toBe("running");
    expect(task.started_at).not.toBeNull();
    expect(task.ended_at).toBeNull();
  });

  it("creates a task with status=pending and no started_at", async () => {
    const task = await taskStore.create({
      sessionId,
      prompt: "pending task",
      submitterEmail: ownerEmail,
      status: "pending",
    });

    expect(task.status).toBe("pending");
    expect(task.started_at).toBeNull();
  });

  it("getById returns task", async () => {
    const created = await taskStore.create({
      sessionId,
      prompt: "get by id",
      submitterEmail: ownerEmail,
      status: "pending",
    });

    const fetched = await taskStore.getById(created.id);
    expect(fetched).not.toBeNull();
    expect(fetched!.id).toBe(created.id);
  });

  it("getById returns null for missing id", async () => {
    const result = await taskStore.getById(999999);
    expect(result).toBeNull();
  });

  it("latestForSession returns latest task regardless of status", async () => {
    const t1 = await taskStore.create({ sessionId, prompt: "first", submitterEmail: ownerEmail, status: "running" });
    await new Promise((r) => setTimeout(r, 10));
    const t2 = await taskStore.create({ sessionId, prompt: "second", submitterEmail: ownerEmail, status: "pending" });

    const latest = await taskStore.latestForSession(sessionId);
    expect(latest!.id).toBe(t2.id);
  });

  it("latestForSession returns null when no tasks", async () => {
    const result = await taskStore.latestForSession(sessionId);
    expect(result).toBeNull();
  });

  it("latestActiveForSession returns only active tasks", async () => {
    const t1 = await taskStore.create({ sessionId, prompt: "running task", submitterEmail: ownerEmail, status: "running" });
    const t2 = await taskStore.create({ sessionId, prompt: "pending task", submitterEmail: ownerEmail, status: "pending" });

    const active = await taskStore.latestActiveForSession(sessionId);
    expect(active!.id).toBe(t1.id);
  });

  it("latestActiveForSession returns null when no active tasks", async () => {
    await taskStore.create({ sessionId, prompt: "pending task", submitterEmail: ownerEmail, status: "pending" });

    const active = await taskStore.latestActiveForSession(sessionId);
    expect(active).toBeNull();
  });

  it("reopenLatestForSession reopens a terminal latest task as running", async () => {
    const t = await taskStore.create({ sessionId, prompt: "done", submitterEmail: ownerEmail, status: "running" });
    await taskStore.updateStatus(t.id, "interrupted", new Date());

    const reopened = await taskStore.reopenLatestForSession(sessionId);
    expect(reopened).not.toBeNull();
    expect(reopened!.id).toBe(t.id);
    expect(reopened!.status).toBe("running");
    expect(reopened!.ended_at).toBeNull();
  });

  it("list with status filter", async () => {
    await taskStore.create({ sessionId, prompt: "running", submitterEmail: ownerEmail, status: "running" });
    await taskStore.create({ sessionId, prompt: "pending", submitterEmail: ownerEmail, status: "pending" });

    const result = await taskStore.list({
      email: ownerEmail,
      groups: [],
      status: "running",
      limit: 10,
      offset: 0,
    });

    expect(result.total).toBe(1);
    expect(result.tasks[0].status).toBe("running");
  });

  it("list with agentId filter", async () => {
    // Create session on agent-b
    const sessionB = await sessionStore.create({
      gatewaySessionId: `gw-agent-b-${Date.now()}`,
      agentId: "agent-b",
      ownerEmail,
    });
    await taskStore.create({ sessionId, prompt: "agent-a task", submitterEmail: ownerEmail, status: "running" });
    await taskStore.create({ sessionId: sessionB.id, prompt: "agent-b task", submitterEmail: ownerEmail, status: "running" });

    const result = await taskStore.list({
      email: ownerEmail,
      groups: [],
      agentId: "agent-a",
      limit: 10,
      offset: 0,
    });

    expect(result.total).toBe(1);
    expect(result.tasks[0].agent_id).toBe("agent-a");
  });

  it("list respects visibility - other user cannot see tasks", async () => {
    await taskStore.create({ sessionId, prompt: "my task", submitterEmail: ownerEmail, status: "running" });

    const result = await taskStore.list({
      email: otherEmail,
      groups: [],
      limit: 10,
      offset: 0,
    });

    expect(result.tasks.some((t) => t.session_id === sessionId)).toBe(false);
  });

  it("list returns correct total for pagination", async () => {
    for (let i = 0; i < 5; i++) {
      await taskStore.create({ sessionId, prompt: `task ${i}`, submitterEmail: ownerEmail, status: "pending" });
    }

    const page1 = await taskStore.list({ email: ownerEmail, groups: [], limit: 2, offset: 0 });
    expect(page1.total).toBe(5);
    expect(page1.tasks).toHaveLength(2);

    const page2 = await taskStore.list({ email: ownerEmail, groups: [], limit: 2, offset: 2 });
    expect(page2.total).toBe(5);
    expect(page2.tasks).toHaveLength(2);
  });

  it("updateStatus transitions status", async () => {
    const task = await taskStore.create({ sessionId, prompt: "test", submitterEmail: ownerEmail, status: "running" });
    const updated = await taskStore.updateStatus(task.id, "completed", new Date());
    expect(updated!.status).toBe("completed");
    expect(updated!.ended_at).not.toBeNull();
  });

  it("updateStatus skips if terminal and new status is running", async () => {
    const task = await taskStore.create({ sessionId, prompt: "test", submitterEmail: ownerEmail, status: "running" });
    await taskStore.updateStatus(task.id, "completed", new Date());
    // Try to set back to running (late SSE)
    const result = await taskStore.updateStatus(task.id, "running");
    expect(result!.status).toBe("completed"); // unchanged
  });

  it("updateOutputPreview truncates to 200 chars", async () => {
    const task = await taskStore.create({ sessionId, prompt: "test", submitterEmail: ownerEmail, status: "running" });
    const longText = "x".repeat(300);
    const updated = await taskStore.updateOutputPreview(task.id, longText);
    expect(updated!.output_preview).toHaveLength(200);
  });

  it("updateOutputPreview skips if task is terminal", async () => {
    const task = await taskStore.create({ sessionId, prompt: "test", submitterEmail: ownerEmail, status: "running" });
    await taskStore.updateOutputPreview(task.id, "final preview");
    await taskStore.updateStatus(task.id, "completed", new Date());
    // Try to update after terminal
    await taskStore.updateOutputPreview(task.id, "new content should not appear");
    const fetched = await taskStore.getById(task.id);
    expect(fetched!.output_preview).toBe("final preview");
  });

  it("cancelPending cancels a pending task and returns rowcount 1", async () => {
    const task = await taskStore.create({ sessionId, prompt: "test", submitterEmail: ownerEmail, status: "pending" });
    const count = await taskStore.cancelPending(task.id);
    expect(count).toBe(1);
    const fetched = await taskStore.getById(task.id);
    expect(fetched!.status).toBe("cancelled");
  });

  it("cancelPending returns 0 for running task", async () => {
    const task = await taskStore.create({ sessionId, prompt: "test", submitterEmail: ownerEmail, status: "running" });
    const count = await taskStore.cancelPending(task.id);
    expect(count).toBe(0);
    const fetched = await taskStore.getById(task.id);
    expect(fetched!.status).toBe("running"); // unchanged
  });

  it("promoteNextPending promotes the oldest pending task", async () => {
    const t1 = await taskStore.create({ sessionId, prompt: "first", submitterEmail: ownerEmail, status: "pending" });
    await new Promise((r) => setTimeout(r, 10));
    const t2 = await taskStore.create({ sessionId, prompt: "second", submitterEmail: ownerEmail, status: "pending" });

    const promoted = await taskStore.promoteNextPending(sessionId);
    expect(promoted).not.toBeNull();
    expect(promoted!.id).toBe(t1.id); // oldest first
    expect(promoted!.status).toBe("running");
    expect(promoted!.started_at).not.toBeNull();
  });

  it("promoteNextPending returns null when no pending tasks", async () => {
    const result = await taskStore.promoteNextPending(sessionId);
    expect(result).toBeNull();
  });

  it("promoteNextPending atomicity - concurrent calls, only one wins", async () => {
    await taskStore.create({ sessionId, prompt: "atomic test", submitterEmail: ownerEmail, status: "pending" });

    // Two concurrent promotions
    const [r1, r2] = await Promise.all([
      taskStore.promoteNextPending(sessionId),
      taskStore.promoteNextPending(sessionId),
    ]);

    // One should win, one should get null
    const results = [r1, r2];
    const promoted = results.filter((r) => r !== null);
    const skipped = results.filter((r) => r === null);
    expect(promoted).toHaveLength(1);
    expect(skipped).toHaveLength(1);
  });

  it("sessionsWithPendingAndNoActive returns session ids", async () => {
    // sessionId has pending task and no active task
    await taskStore.create({ sessionId, prompt: "pending", submitterEmail: ownerEmail, status: "pending" });

    const ids = await taskStore.sessionsWithPendingAndNoActive();
    expect(ids).toContain(sessionId);
  });

  it("sessionsWithPendingAndNoActive excludes sessions with active tasks", async () => {
    await taskStore.create({ sessionId, prompt: "pending", submitterEmail: ownerEmail, status: "pending" });
    await taskStore.create({ sessionId, prompt: "running", submitterEmail: ownerEmail, status: "running" });

    const ids = await taskStore.sessionsWithPendingAndNoActive();
    expect(ids).not.toContain(sessionId);
  });

  it("countByStatus returns correct counts", async () => {
    await taskStore.create({ sessionId, prompt: "running task", submitterEmail: ownerEmail, status: "running" });
    await taskStore.create({ sessionId, prompt: "pending task", submitterEmail: ownerEmail, status: "pending" });

    const counts = await taskStore.countByStatus({ email: ownerEmail, groups: [] });
    expect(counts.running).toBeGreaterThanOrEqual(1);
    expect(counts.pending).toBeGreaterThanOrEqual(1);
  });
});
