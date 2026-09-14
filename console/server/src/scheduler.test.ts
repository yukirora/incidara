import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireOnce, fireSchedule } from "./scheduler.js";
import type { Schedule } from "./schedule-store.js";
import type { SchedulerDeps } from "./scheduler.js";

// ── helpers ───────────────────────────────────────────────────────────────────

function makeSchedule(overrides: Partial<Schedule> = {}): Schedule {
  return {
    id: 1,
    name: "Test schedule",
    agent_id: "agent-a",
    owner_email: "user@example.com",
    prompt: "do the thing",
    trigger_type: "interval",
    run_at: null,
    interval_seconds: 3600,
    cron_expr: null,
    timezone: "UTC",
    session_mode: "new",
    reuse_session_id: null,
    enabled: true,
    last_fired_at: null,
    next_fire_at: new Date(Date.now() - 1000), // in the past = due
    created_at: new Date(),
    ...overrides,
  };
}

function makeDeps(overrides: Partial<SchedulerDeps> = {}): SchedulerDeps {
  const fakeGw = {
    createSession: vi.fn().mockResolvedValue({ id: "gw-session-1", status: "running" }),
    sendMessage: vi.fn().mockResolvedValue({}),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    interrupt: vi.fn().mockResolvedValue({}),
    resume: vi.fn().mockResolvedValue({}),
    getState: vi.fn().mockResolvedValue({ status: "running" }),
    getEvents: vi.fn().mockResolvedValue({ events: [] }),
    openEventStream: vi.fn(),
  };

  const fakeSessionStore = {
    create: vi.fn().mockResolvedValue({ id: 10, gateway_session_id: "gw-session-1", agent_id: "agent-a", owner_email: "user@example.com", title: null, shared_with_users: [], shared_with_groups: [], created_at: new Date() }),
    getById: vi.fn().mockResolvedValue({ id: 10, gateway_session_id: "gw-session-1", agent_id: "agent-a", owner_email: "user@example.com", title: null, shared_with_users: [], shared_with_groups: [], created_at: new Date() }),
    getByGatewayId: vi.fn(),
    listVisible: vi.fn().mockResolvedValue([]),
    updateTitle: vi.fn(),
    updateSharing: vi.fn(),
    delete: vi.fn(),
  };

  const fakeTaskStore = {
    create: vi.fn().mockResolvedValue({ id: 100, session_id: 10, prompt: "do the thing", submitter_email: "user@example.com", status: "running", created_at: new Date(), started_at: new Date(), ended_at: null, output_preview: null, gateway_seq_start: null, from_schedule_id: 1 }),
    getById: vi.fn(),
    latestForSession: vi.fn().mockResolvedValue(null),
    latestActiveForSession: vi.fn().mockResolvedValue(null),
    list: vi.fn(),
    updateStatus: vi.fn().mockResolvedValue(null),
    updateOutputPreview: vi.fn().mockResolvedValue(null),
    cancelPending: vi.fn(),
    promoteNextPending: vi.fn(),
    sessionsWithPendingAndNoActive: vi.fn(),
    countByStatus: vi.fn(),
  };

  const fakeScheduleStore = {
    create: vi.fn(),
    getById: vi.fn(),
    list: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    claimDue: vi.fn().mockResolvedValue([]),
    recordFiring: vi.fn().mockResolvedValue(null),
  };

  return {
    scheduleStore: fakeScheduleStore as unknown as SchedulerDeps["scheduleStore"],
    taskStore: fakeTaskStore as unknown as SchedulerDeps["taskStore"],
    sessionStore: fakeSessionStore as unknown as SchedulerDeps["sessionStore"],
    getAgents: () => [{ id: "agent-a", name: "Agent A", gateway_url: "http://127.0.0.1:8000", backend: "claude_code" as const, access: { owners: ["user@example.com"], shared_with: [], shared_with_groups: [] } }],
    makeClient: () => fakeGw as unknown as SchedulerDeps["makeClient"] extends (url: string) => infer R ? R : never,
    ...overrides,
  };
}

// ── fireSchedule ──────────────────────────────────────────────────────────────

describe("fireSchedule", () => {
  it("session_mode=new: creates gateway session + DB session + task", async () => {
    const deps = makeDeps();
    const schedule = makeSchedule({ session_mode: "new" });

    await fireSchedule(deps, schedule);

    const gw = deps.makeClient("") as unknown as ReturnType<typeof makeDeps>["makeClient"] extends (url: string) => infer R ? R : never;
    // Gateway called with prompt
    expect((deps.makeClient as ReturnType<typeof vi.fn>)).toBeDefined();

    // sessionStore.create called
    expect((deps.sessionStore.create as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);

    // taskStore.create called with fromScheduleId and status=running
    const taskCreateCall = (deps.taskStore.create as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(taskCreateCall.fromScheduleId).toBe(1);
    expect(taskCreateCall.status).toBe("running");
  });

  it("session_mode=new: throws when agent not found", async () => {
    const deps = makeDeps({ getAgents: () => [] });
    const schedule = makeSchedule({ session_mode: "new" });

    await expect(fireSchedule(deps, schedule)).rejects.toThrow("agent");
  });

  it("session_mode=reuse with no active task: creates running task and calls sendMessage", async () => {
    const deps = makeDeps();
    (deps.taskStore.latestActiveForSession as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    const schedule = makeSchedule({ session_mode: "reuse", reuse_session_id: 10 });

    await fireSchedule(deps, schedule);

    const taskCall = (deps.taskStore.create as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(taskCall.status).toBe("running");
    expect(taskCall.fromScheduleId).toBe(1);
  });

  it("session_mode=reuse with active task: creates pending task, no sendMessage", async () => {
    const deps = makeDeps();
    const activeTask = { id: 50, status: "running", session_id: 10, prompt: "prev", submitter_email: "user@example.com", created_at: new Date(), started_at: new Date(), ended_at: null, output_preview: null, gateway_seq_start: null, from_schedule_id: null };
    (deps.taskStore.latestActiveForSession as ReturnType<typeof vi.fn>).mockResolvedValue(activeTask);
    const schedule = makeSchedule({ session_mode: "reuse", reuse_session_id: 10 });

    await fireSchedule(deps, schedule);

    const taskCall = (deps.taskStore.create as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(taskCall.status).toBe("pending");
  });

  it("session_mode=reuse: throws when reuse_session_id is missing", async () => {
    const deps = makeDeps();
    const schedule = makeSchedule({ session_mode: "reuse", reuse_session_id: null });

    await expect(fireSchedule(deps, schedule)).rejects.toThrow("reuse_session_id");
  });
});

// ── fireOnce ──────────────────────────────────────────────────────────────────

describe("fireOnce", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("interval schedule fires and next_fire_at advances", async () => {
    const schedule = makeSchedule({ trigger_type: "interval", interval_seconds: 3600 });
    const deps = makeDeps();
    (deps.scheduleStore.claimDue as ReturnType<typeof vi.fn>).mockResolvedValue([schedule]);

    await fireOnce(deps);

    expect((deps.scheduleStore.recordFiring as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);
    const [id, { nextFireAt, disableIfDone }] = (deps.scheduleStore.recordFiring as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(id).toBe(1);
    expect(nextFireAt).not.toBeNull();
    // ~3600s from now
    const diff = (nextFireAt as Date).getTime() - Date.now();
    expect(diff).toBeGreaterThan(3598_000);
    expect(disableIfDone).toBe(false);
  });

  it("once schedule fires and is disabled (next_fire_at=null)", async () => {
    const runAt = new Date(Date.now() - 1000);
    const schedule = makeSchedule({ trigger_type: "once", run_at: runAt, interval_seconds: null });
    const deps = makeDeps();
    (deps.scheduleStore.claimDue as ReturnType<typeof vi.fn>).mockResolvedValue([schedule]);

    await fireOnce(deps);

    const [, { nextFireAt, disableIfDone }] = (deps.scheduleStore.recordFiring as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(nextFireAt).toBeNull();
    expect(disableIfDone).toBe(true);
  });

  it("cron schedule fires and next_fire_at is computed correctly", async () => {
    const schedule = makeSchedule({ trigger_type: "cron", cron_expr: "0 * * * *", interval_seconds: null });
    const deps = makeDeps();
    (deps.scheduleStore.claimDue as ReturnType<typeof vi.fn>).mockResolvedValue([schedule]);

    await fireOnce(deps);

    const [, { nextFireAt }] = (deps.scheduleStore.recordFiring as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(nextFireAt).not.toBeNull();
    expect((nextFireAt as Date).getMinutes()).toBe(0);
  });

  it("gateway error does not call recordFiring (schedule retried next tick)", async () => {
    const schedule = makeSchedule();
    const deps = makeDeps();
    (deps.scheduleStore.claimDue as ReturnType<typeof vi.fn>).mockResolvedValue([schedule]);

    // Make fireSchedule fail by removing the agent
    deps.getAgents = () => [];

    await fireOnce(deps);

    // recordFiring should NOT have been called
    expect((deps.scheduleStore.recordFiring as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it("no due schedules → no side effects", async () => {
    const deps = makeDeps();
    (deps.scheduleStore.claimDue as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    await fireOnce(deps);

    expect((deps.scheduleStore.recordFiring as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
    expect((deps.taskStore.create as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });
});
