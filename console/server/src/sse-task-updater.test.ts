import { describe, it, expect, beforeEach, afterAll, beforeAll, vi } from "vitest";
import { extractCompleteSseEvents, applyEventToTask, promoteNext } from "./sse-task-updater.js";
import type { SseTaskUpdaterDeps } from "./sse-task-updater.js";
import type { Task, TaskStatus } from "./task-store.js";
import type { Session } from "./session-store.js";
import type { GatewayClient } from "./gateway-client.js";
import type { Agent } from "./agent-registry.js";

// ---- Parser tests (no DB needed) ----

describe("extractCompleteSseEvents", () => {
  it("parses a single complete SSE event", () => {
    const buf = `data: {"seq":1,"session_id":"s1","event_type":"ping","timestamp":"t","payload":{}}\n\n`;
    const { parsed, remainder } = extractCompleteSseEvents(buf);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].seq).toBe(1);
    expect(parsed[0].event_type).toBe("ping");
    expect(remainder).toBe("");
  });

  it("returns remainder for incomplete event", () => {
    const buf = `data: {"seq":1,"session_id":"s1","event_type":"ping","timestamp":"t","payload":{}}\n`;
    const { parsed, remainder } = extractCompleteSseEvents(buf);
    expect(parsed).toHaveLength(0);
    expect(remainder).toBe(buf);
  });

  it("parses multiple events and leaves incomplete remainder", () => {
    const event1 = `data: {"seq":1,"session_id":"s1","event_type":"a","timestamp":"t","payload":{}}\n\n`;
    const event2 = `data: {"seq":2,"session_id":"s1","event_type":"b","timestamp":"t","payload":{}}\n\n`;
    const partial = `data: {"seq":3`;

    const { parsed, remainder } = extractCompleteSseEvents(event1 + event2 + partial);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].seq).toBe(1);
    expect(parsed[1].seq).toBe(2);
    expect(remainder).toBe(partial);
  });

  it("handles multi-line data fields", () => {
    const buf = `data: {"seq":1,"session_id":"s1","event_type":"test",\ndata: "timestamp":"t","payload":{}}\n\n`;
    const { parsed } = extractCompleteSseEvents(buf);
    // Multi-line concatenation
    expect(parsed).toHaveLength(1);
  });

  it("handles empty buffer", () => {
    const { parsed, remainder } = extractCompleteSseEvents("");
    expect(parsed).toHaveLength(0);
    expect(remainder).toBe("");
  });

  it("skips malformed JSON gracefully", () => {
    const buf = `data: not-valid-json\n\n`;
    const { parsed, remainder } = extractCompleteSseEvents(buf);
    expect(parsed).toHaveLength(0);
    expect(remainder).toBe("");
  });
});

// ---- applyEventToTask + promoteNext tests (with mock deps) ----

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
    gateway_session_id: "gw-session-1",
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

function makeDeps(overrides: Partial<SseTaskUpdaterDeps> = {}): SseTaskUpdaterDeps {
  const task = makeTask();
  const session = makeSession();
  const agent = makeAgent();

  const taskStore = {
    latestForSession: vi.fn(async (_id: number) => task),
    getById: vi.fn(async (_id: number) => task),
    updateStatus: vi.fn(async (_id: number, _status: TaskStatus, _endedAt?: Date) => task),
    updateOutputPreview: vi.fn(async (_id: number, _text: string) => task),
    updateFullOutput: vi.fn(async (_id: number, _text: string) => task),
    setModelVersion: vi.fn(async (_id: number, _model: string) => task),
    promoteNextPending: vi.fn(async (_sessionId: number) => null as Task | null),
    setGatewaySeqStart: vi.fn(async (_id: number, _seq: number) => {}),
  } as unknown as SseTaskUpdaterDeps["taskStore"];

  const sessionStore = {
    getById: vi.fn(async (_id: number) => session),
  } as unknown as SseTaskUpdaterDeps["sessionStore"];

  const mockClient: GatewayClient = {
    sendMessage: vi.fn(async (_id: string, _content: string) => ({ accepted: true })),
    resume: vi.fn(async (_id: string, _content?: string) => ({ accepted: true })),
  } as unknown as GatewayClient;

  const makeClient = vi.fn((_url: string) => mockClient);
  const getAgents = vi.fn(() => [agent]);

  return {
    taskStore,
    sessionStore,
    makeClient,
    getAgents,
    ...overrides,
  };
}

function makeMockTaskStore(task: Task) {
  return {
    latestForSession: vi.fn(async () => task),
    getById: vi.fn(async () => task),
    updateStatus: vi.fn(async () => task),
    updateOutputPreview: vi.fn(async () => task),
    updateFullOutput: vi.fn(async () => task),
    setModelVersion: vi.fn(async () => task),
    promoteNextPending: vi.fn(async () => null),
    setGatewaySeqStart: vi.fn(async () => {}),
  } as unknown as SseTaskUpdaterDeps["taskStore"];
}

describe("applyEventToTask", () => {
  it("message.agent updates output_preview", async () => {
    const deps = makeDeps();
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "message.agent",
      timestamp: "t",
      payload: { content: "Hello world!" },
    });
    expect(deps.taskStore.updateOutputPreview).toHaveBeenCalledWith(1, "Hello world!");
  });

  it("message.agent transitions task from error to running", async () => {
    const errorTask = makeTask({ status: "error", output_preview: "old output" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(errorTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "message.agent",
      timestamp: "t",
      payload: { content: "Agent recovered and responding!" },
    });
    expect(deps.taskStore.updateOutputPreview).toHaveBeenCalledWith(1, "Agent recovered and responding!");
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "running");
  });

  it("message.agent transitions task from interrupted to running", async () => {
    const interruptedTask = makeTask({ status: "interrupted" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(interruptedTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "message.agent",
      timestamp: "t",
      payload: { content: "Resuming!" },
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "running");
  });

  it("message.user recovers task from error to running", async () => {
    const errorTask = makeTask({ status: "error" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(errorTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "message.user",
      timestamp: "t",
      payload: { content: "follow-up message" },
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "running");
  });

  it("message.user recovers task from interrupted to running", async () => {
    const interruptedTask = makeTask({ status: "interrupted" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(interruptedTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "message.user",
      timestamp: "t",
      payload: { content: "follow-up message" },
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "running");
  });

  it("session.started recovers task from error to running", async () => {
    const errorTask = makeTask({ status: "error" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(errorTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.started",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "running");
  });

  it("session.started recovers task from interrupted to running", async () => {
    const interruptedTask = makeTask({ status: "interrupted" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(interruptedTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.started",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "running");
  });

  it("session.started is no-op when task is already running", async () => {
    const runningTask = makeTask({ status: "running" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(runningTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.started",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).not.toHaveBeenCalled();
  });

  it("session.waiting_input updates status to waiting_input", async () => {
    const deps = makeDeps();
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.waiting_input",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "waiting_input");
  });

  it("session.completed updates status and calls promoteNext", async () => {
    const deps = makeDeps();
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.completed",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "completed", expect.any(Date));
    expect(deps.taskStore.promoteNextPending).toHaveBeenCalledWith(10);
  });

  it("session.error finalizes non-running task and calls promoteNext", async () => {
    const waitingTask = makeTask({ status: "waiting_input" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(waitingTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.error",
      timestamp: "t",
      payload: { error: "something went wrong" },
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "error", expect.any(Date));
    expect(deps.taskStore.updateOutputPreview).toHaveBeenCalledWith(1, "Error: something went wrong");
    expect(deps.taskStore.promoteNextPending).toHaveBeenCalledWith(10);
  });

  it("session.error keeps task as running when actively running (adapter may recover)", async () => {
    const runningTask = makeTask({ status: "running" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(runningTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.error",
      timestamp: "t",
      payload: { error: "unknown error" },
    });
    // Should NOT finalize as "error" — adapter may recover
    expect(deps.taskStore.updateStatus).not.toHaveBeenCalledWith(1, "error", expect.any(Date));
    // Should NOT promote next — adapter is still handling this session
    expect(deps.taskStore.promoteNextPending).not.toHaveBeenCalled();
  });

  it("session.error does not overwrite existing output_preview", async () => {
    const taskWithPreview = makeTask({ status: "waiting_input", output_preview: "existing preview" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(taskWithPreview) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.error",
      timestamp: "t",
      payload: { error: "new error" },
    });
    expect(deps.taskStore.updateOutputPreview).not.toHaveBeenCalled();
  });

  it("session.error with recoverable context overflow does NOT mark task as error", async () => {
    const task = makeTask({ completion_mode: "manual" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(task) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.error",
      timestamp: "t",
      payload: { error: "Prompt is too long", recoverable: false },
    });
    // Should NOT mark as error — adapter will auto-compact and recover
    expect(deps.taskStore.updateStatus).not.toHaveBeenCalledWith(1, "error", expect.any(Date));
    // Should NOT promote next — adapter is still handling this session
    expect(deps.taskStore.promoteNextPending).not.toHaveBeenCalled();
  });

  it("session.error with 'No conversation found' while running keeps task running (adapter retries)", async () => {
    const task = makeTask({ completion_mode: "manual", status: "running" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(task) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.error",
      timestamp: "t",
      payload: { error: "No conversation found with session ID: abc-123", recoverable: false },
    });
    // Should NOT mark as error — adapter will retry fresh
    expect(deps.taskStore.updateStatus).not.toHaveBeenCalledWith(1, "error", expect.any(Date));
    expect(deps.taskStore.promoteNextPending).not.toHaveBeenCalled();
  });

  it("session.error with 'No conversation found' on non-running task marks as error", async () => {
    const task = makeTask({ completion_mode: "manual", status: "error" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(task) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.error",
      timestamp: "t",
      payload: { error: "No conversation found with session ID: abc-123", recoverable: false },
    });
    // Should mark as error — adapter already tried and failed
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "error", expect.any(Date));
    expect(deps.taskStore.promoteNextPending).toHaveBeenCalledWith(10);
  });

  it("session.interrupted updates status and sets partial_content if empty", async () => {
    const deps = makeDeps();
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.interrupted",
      timestamp: "t",
      payload: { partial_content: "partial work done" },
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "interrupted", expect.any(Date));
    expect(deps.taskStore.updateOutputPreview).toHaveBeenCalledWith(1, "partial work done");
    expect(deps.taskStore.promoteNextPending).toHaveBeenCalledWith(10);
  });

  it("session.completed with auto mode updates status to completed and calls promoteNext", async () => {
    const autoTask = makeTask({ completion_mode: "auto" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(autoTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.completed",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "completed", expect.any(Date));
    expect(deps.taskStore.promoteNextPending).toHaveBeenCalledWith(10);
  });

  it("session.completed with manual mode sets waiting_input and does NOT call promoteNext", async () => {
    const manualTask = makeTask({ completion_mode: "manual" });
    const deps = makeDeps({ taskStore: makeMockTaskStore(manualTask) });
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "session.completed",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "waiting_input");
    expect(deps.taskStore.promoteNextPending).not.toHaveBeenCalled();
  });

  it("unknown event type is a no-op", async () => {
    const deps = makeDeps();
    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "some.unknown.event",
      timestamp: "t",
      payload: {},
    });
    expect(deps.taskStore.updateStatus).not.toHaveBeenCalled();
    expect(deps.taskStore.updateOutputPreview).not.toHaveBeenCalled();
  });

  it("no-op when no task found for session", async () => {
    const deps = makeDeps({
      taskStore: {
        latestForSession: vi.fn(async () => null),
        updateStatus: vi.fn(),
        updateOutputPreview: vi.fn(),
        promoteNextPending: vi.fn(),
        setGatewaySeqStart: vi.fn(),
      } as unknown as SseTaskUpdaterDeps["taskStore"],
    });

    await applyEventToTask(deps, 10, {
      seq: 1,
      session_id: "gw-1",
      event_type: "message.agent",
      timestamp: "t",
      payload: { content: "hello" },
    });

    expect(deps.taskStore.updateOutputPreview).not.toHaveBeenCalled();
  });
});

describe("promoteNext", () => {
  it("promotes pending task and sends gateway message", async () => {
    const promotedTask = makeTask({ status: "running" });
    const deps = makeDeps({
      taskStore: {
        promoteNextPending: vi.fn(async () => promotedTask),
        updateStatus: vi.fn(async () => promotedTask),
        updateOutputPreview: vi.fn(async () => promotedTask),
        setGatewaySeqStart: vi.fn(async () => {}),
      } as unknown as SseTaskUpdaterDeps["taskStore"],
    });

    await promoteNext(deps, 10);

    expect(deps.taskStore.promoteNextPending).toHaveBeenCalledWith(10);
    expect(deps.makeClient).toHaveBeenCalledWith("http://127.0.0.1:9000");
    const clientInstance = (deps.makeClient as ReturnType<typeof vi.fn>).mock.results[0].value as GatewayClient;
    expect(clientInstance.sendMessage).toHaveBeenCalledWith("gw-session-1", "test prompt");
  });

  it("does nothing when no pending task", async () => {
    const deps = makeDeps({
      taskStore: {
        promoteNextPending: vi.fn(async () => null),
        setGatewaySeqStart: vi.fn(async () => {}),
      } as unknown as SseTaskUpdaterDeps["taskStore"],
    });

    await promoteNext(deps, 10);
    expect(deps.makeClient).not.toHaveBeenCalled();
  });

  it("marks task as error on gateway failure", async () => {
    const promotedTask = makeTask({ status: "running" });
    const failingClient: GatewayClient = {
      sendMessage: vi.fn(async () => { throw new Error("gateway down"); }),
      resume: vi.fn(async () => { throw new Error("gateway down"); }),
    } as unknown as GatewayClient;

    const deps = makeDeps({
      taskStore: {
        promoteNextPending: vi.fn(async () => promotedTask),
        updateStatus: vi.fn(async () => promotedTask),
        updateOutputPreview: vi.fn(async () => promotedTask),
        setGatewaySeqStart: vi.fn(async () => {}),
      } as unknown as SseTaskUpdaterDeps["taskStore"],
      makeClient: vi.fn(() => failingClient),
    });

    await promoteNext(deps, 10);

    expect(deps.taskStore.updateStatus).toHaveBeenCalledWith(1, "error", expect.any(Date));
    expect(deps.taskStore.updateOutputPreview).toHaveBeenCalledWith(1, "Error: gateway down");
  });
});
