import { describe, it, expect, vi, beforeEach } from "vitest";
import { reconcileOnce } from "./task-reconciler.js";
import type { ReconcilerDeps } from "./task-reconciler.js";
import type { Task, TaskStore } from "./task-store.js";
import type { Session } from "./session-store.js";
import type { Agent } from "./agent-registry.js";
import type { GatewayClient } from "./gateway-client.js";
import type pg from "pg";

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 1,
    session_id: 10,
    prompt: "test prompt",
    submitter_email: "user@example.com",
    status: "running",
    created_at: new Date(),
    started_at: new Date(),
    ended_at: null,
    output_preview: null,
    gateway_seq_start: null,
    from_schedule_id: null,
    title: null,
    completion_mode: "auto",
    ...overrides,
  };
}

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 10,
    gateway_session_id: "gw-1",
    agent_id: "agent-a",
    owner_email: "user@example.com",
    title: null,
    shared_with_users: [],
    shared_with_groups: [],
    created_at: new Date(),
    ...overrides,
  };
}

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent-a",
    name: "Agent A",
    gateway_url: "http://127.0.0.1:9000",
    backend: "claude_code",
    access: { owners: [], shared_with: [], shared_with_groups: [] },
    ...overrides,
  };
}

function makePool(
  runningTasks: Task[] = [],
  pendingSessionIds: number[] = []
): pg.Pool {
  return {
    query: vi.fn(async (sql: string) => {
      // Pass A: select the latest active/interrupted task per session.
      if (sql.includes("SELECT DISTINCT ON (session_id)") && sql.includes("status IN ('running'")) {
        return { rows: runningTasks };
      }
      // Pass B: select sessions with pending tasks (WHERE status = 'pending')
      if (sql.includes("status = 'pending'")) {
        return { rows: pendingSessionIds.map((id) => ({ session_id: id })) };
      }
      // UPDATE tasks — status sync
      return { rows: [], rowCount: 1 };
    }),
  } as unknown as pg.Pool;
}

function makeTaskStore(promotedTask: Task | null = null): TaskStore {
  return {
    promoteNextPending: vi.fn(async (_sessionId: number) => promotedTask),
    updateStatus: vi.fn(async () => promotedTask ?? makeTask()),
    updateOutputPreview: vi.fn(async () => promotedTask ?? makeTask()),
  } as unknown as TaskStore;
}

function makeDeps(
  overrides: Partial<ReconcilerDeps> = {},
  options: {
    runningTasks?: Task[];
    pendingSessionIds?: number[];
    gatewayStatus?: string;
    gatewayError?: boolean;
    promotedTask?: Task | null;
  } = {}
): ReconcilerDeps {
  const session = makeSession();
  const agent = makeAgent();

  const pool = makePool(
    options.runningTasks ?? [makeTask()],
    options.pendingSessionIds ?? []
  );

  const taskStore = makeTaskStore(options.promotedTask ?? null);

  const sessionStore = {
    getById: vi.fn(async (_id: number) => session),
  } as unknown as ReconcilerDeps["sessionStore"];

  const getAgents = vi.fn(() => [agent]);

  const mockClient: GatewayClient = {
    getState: vi.fn(async (_id: string) => {
      if (options.gatewayError) throw new Error("gateway down");
      return { status: options.gatewayStatus ?? "running" };
    }),
    sendMessage: vi.fn(async () => ({ accepted: true })),
    resume: vi.fn(async () => ({ accepted: true })),
  } as unknown as GatewayClient;

  const makeClient = vi.fn(() => mockClient);

  return {
    pool,
    taskStore,
    sessionStore,
    getAgents,
    makeClient,
    ...overrides,
  };
}

describe("reconcileOnce", () => {
  it("syncs running task to completed when gateway reports completed", async () => {
    const deps = makeDeps({}, { gatewayStatus: "completed" });

    await reconcileOnce(deps);

    const pool = deps.pool as unknown as { query: ReturnType<typeof vi.fn> };
    // Should have called UPDATE with completed
    const calls = pool.query.mock.calls as [string, unknown[]][];
    const updateCall = calls.find(([sql]) => sql.includes("UPDATE tasks SET status"));
    expect(updateCall).toBeDefined();
    expect(updateCall![1]).toContain("completed");
  });

  it("leaves task alone when gateway reports running", async () => {
    const deps = makeDeps({}, { gatewayStatus: "running" });

    await reconcileOnce(deps);

    const pool = deps.pool as unknown as { query: ReturnType<typeof vi.fn> };
    const calls = pool.query.mock.calls as [string, unknown[]][];
    const updateCall = calls.find(([sql]) => sql.includes("UPDATE tasks SET status"));
    expect(updateCall).toBeUndefined();
  });

  it("handles gateway error without crashing", async () => {
    const deps = makeDeps({}, { gatewayError: true });

    // Should not throw
    await expect(reconcileOnce(deps)).resolves.not.toThrow();
  });

  it("promotes pending task when session has no active task", async () => {
    const pendingTask = makeTask({ id: 2, status: "pending" as const });

    const deps = makeDeps(
      {},
      {
        runningTasks: [], // No running tasks
        pendingSessionIds: [10], // Session 10 has pending tasks
        gatewayStatus: "running",
        promotedTask: pendingTask, // The task to be promoted
      }
    );

    await reconcileOnce(deps);

    // taskStore.promoteNextPending should have been called
    expect(deps.taskStore.promoteNextPending).toHaveBeenCalledWith(10);

    // Gateway sendMessage should be called for promoted task
    const client = (deps.makeClient as ReturnType<typeof vi.fn>).mock.results[0]?.value as GatewayClient;
    expect(client).toBeDefined();
    expect(client.sendMessage).toHaveBeenCalledWith("gw-1", "test prompt");
  });

  it("skips promotion when session already has active task", async () => {
    const runningTask = makeTask({ status: "running" });
    const deps = makeDeps(
      {},
      {
        runningTasks: [runningTask],
        pendingSessionIds: [], // No sessions need promotion
        gatewayStatus: "running",
      }
    );

    await reconcileOnce(deps);

    // promoteNextPending should NOT be called
    expect(deps.taskStore.promoteNextPending).not.toHaveBeenCalled();
  });

  it("handles error in gateway state check without stopping reconciliation", async () => {
    const task1 = makeTask({ id: 1, session_id: 10 });
    const task2 = makeTask({ id: 2, session_id: 11 });

    const pool = {
      query: vi.fn(async (sql: string) => {
        if (sql.includes("status IN ('running'")) {
          return { rows: [task1, task2] };
        }
        if (sql.includes("status = 'pending'")) {
          return { rows: [] };
        }
        return { rows: [], rowCount: 1 };
      }),
    } as unknown as pg.Pool;

    const session1 = makeSession({ id: 10, agent_id: "agent-a" });
    const session2 = makeSession({ id: 11, agent_id: "agent-b" });
    const sessionStore = {
      getById: vi.fn(async (id: number) => (id === 10 ? session1 : session2)),
    } as unknown as ReconcilerDeps["sessionStore"];

    const client1: GatewayClient = {
      getState: vi.fn(async () => { throw new Error("timeout"); }),
      sendMessage: vi.fn(),
      resume: vi.fn(),
    } as unknown as GatewayClient;

    const client2: GatewayClient = {
      getState: vi.fn(async () => ({ status: "completed" })),
      sendMessage: vi.fn(),
      resume: vi.fn(),
    } as unknown as GatewayClient;

    const makeClient = vi.fn((url: string) =>
      url.includes("9000") ? client1 : client2
    );

    const taskStore = makeTaskStore();

    const deps: ReconcilerDeps = {
      pool,
      taskStore,
      sessionStore,
      getAgents: vi.fn(() => [
        makeAgent({ id: "agent-a", gateway_url: "http://127.0.0.1:9000" }),
        makeAgent({ id: "agent-b", gateway_url: "http://127.0.0.1:9001" }),
      ]),
      makeClient,
    };

    // Should not throw despite task 1's gateway erroring
    await expect(reconcileOnce(deps)).resolves.not.toThrow();

    // task 2 should still be processed (UPDATE called with task2.id = 2)
    const queryCalls = (pool.query as ReturnType<typeof vi.fn>).mock.calls as [string, unknown[]][];
    const updateCall = queryCalls.find(
      ([sql, params]) => sql.includes("UPDATE tasks") && Array.isArray(params) && params.includes(2)
    );
    expect(updateCall).toBeDefined();
  });

  it("moves manual task to waiting_input when gateway reports completed", async () => {
    const manualTask = makeTask({ completion_mode: "manual", status: "running" });
    const deps = makeDeps(
      {},
      { runningTasks: [manualTask], gatewayStatus: "completed" }
    );

    await reconcileOnce(deps);

    const pool = deps.pool as unknown as { query: ReturnType<typeof vi.fn> };
    const calls = pool.query.mock.calls as [string, unknown[]][];
    const updateCall = calls.find(([sql]) => sql.includes("UPDATE tasks SET status"));
    // Should update to waiting_input (not completed) for manual mode
    expect(updateCall).toBeDefined();
    expect(updateCall![1]).toContain("waiting_input");
  });

  it("still marks manual task as error when gateway reports error", async () => {
    const manualTask = makeTask({ completion_mode: "manual", status: "running" });
    const mockClient: GatewayClient = {
      getState: vi.fn(async () => ({ status: "error" })),
      sendMessage: vi.fn(),
      resume: vi.fn(),
    } as unknown as GatewayClient;
    const deps = makeDeps(
      { makeClient: vi.fn(() => mockClient) },
      { runningTasks: [manualTask], gatewayStatus: "error" }
    );

    await reconcileOnce(deps);

    const pool = deps.pool as unknown as { query: ReturnType<typeof vi.fn> };
    const calls = pool.query.mock.calls as [string, unknown[]][];
    const updateCall = calls.find(([sql]) => sql.includes("UPDATE tasks SET status"));
    expect(updateCall).toBeDefined();
    expect(updateCall![1]).toContain("error");
  });

  it("moves manual task to interrupted when gateway reports interrupted", async () => {
    const manualTask = makeTask({ completion_mode: "manual", status: "running" });
    const mockClient: GatewayClient = {
      getState: vi.fn(async () => ({ status: "interrupted" })),
      sendMessage: vi.fn(),
      resume: vi.fn(),
    } as unknown as GatewayClient;
    const deps = makeDeps(
      { makeClient: vi.fn(() => mockClient) },
      { runningTasks: [manualTask], gatewayStatus: "interrupted" }
    );

    await reconcileOnce(deps);

    const pool = deps.pool as unknown as { query: ReturnType<typeof vi.fn> };
    const calls = pool.query.mock.calls as [string, unknown[]][];
    const updateCall = calls.find(([sql]) => sql.includes("UPDATE tasks SET status"));
    expect(updateCall).toBeDefined();
    expect(updateCall![1]).toContain("interrupted");
  });

  it("recovers error task when gateway is running (adapter auto-recovered)", async () => {
    const errorTask = makeTask({ status: "error" });
    const mockClient: GatewayClient = {
      getState: vi.fn(async () => ({ status: "running" })),
      sendMessage: vi.fn(),
      resume: vi.fn(),
    } as unknown as GatewayClient;
    const deps = makeDeps(
      { makeClient: vi.fn(() => mockClient) },
      { runningTasks: [errorTask], gatewayStatus: "running" }
    );

    await reconcileOnce(deps);

    const pool = deps.pool as unknown as { query: ReturnType<typeof vi.fn> };
    const calls = pool.query.mock.calls as [string, unknown[]][];
    const updateCall = calls.find(([sql]) => sql.includes("UPDATE tasks SET status"));
    expect(updateCall).toBeDefined();
    expect(updateCall![1]).toContain("running");
    // Should set ended_at = NULL in the SQL (literal, not parameter)
    expect(updateCall![0]).toContain("ended_at = NULL");
  });
});

describe("startReconciler", () => {
  it("returns a stop function that clears the interval", async () => {
    const { startReconciler } = await import("./task-reconciler.js");

    const deps = makeDeps({}, { runningTasks: [], pendingSessionIds: [] });
    const stop = startReconciler(deps, 50_000);
    expect(typeof stop).toBe("function");
    stop(); // Should not throw
  });
});
