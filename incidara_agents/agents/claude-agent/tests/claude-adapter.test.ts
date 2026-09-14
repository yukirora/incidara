import { describe, it, expect, beforeEach, vi } from "vitest";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import type { GatewayEvent } from "../src/types.js";

function mockQuery(events: any[]) {
  return async function* () {
    for (const e of events) yield e;
  };
}

describe("ClaudeAdapter", () => {
  let store: SessionStore;
  let bus: EventBus;
  let collected: GatewayEvent[];

  beforeEach(() => {
    store = new SessionStore();
    bus = new EventBus();
    collected = [];
    const origPublish = bus.publish.bind(bus);
    bus.publish = (e) => { collected.push(e); origPublish(e); };
  });

  it("maps system.init to session.started and captures claudeSessionId", async () => {
    const session = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "claude-123" },
      { type: "result", subtype: "success", session_id: "claude-123" },
    ];
    const adapter = new ClaudeAdapter(store, bus, {
      queryFn: () => mockQuery(events)() as any,
    });
    await adapter.runSessionLoop(session, "hello");
    const started = collected.find(e => e.event_type === "session.started");
    expect(started).toBeDefined();
    expect(session.claudeSessionId).toBe("claude-123");
    expect(session.status).toBe("completed");
  });

  it("emits tool.started on content_block_start tool_use and transitions to busy", async () => {
    const session = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      {
        type: "stream_event",
        event: {
          type: "content_block_start",
          content_block: { type: "tool_use", id: "tu_1", name: "Bash" },
        },
      },
      { type: "result", subtype: "success", session_id: "c1" },
    ];
    const adapter = new ClaudeAdapter(store, bus, {
      queryFn: () => mockQuery(events)() as any,
    });
    await adapter.runSessionLoop(session, "run ls");
    const toolStarted = collected.find(e => e.event_type === "tool.started");
    expect(toolStarted?.payload).toMatchObject({ tool_name: "Bash", tool_use_id: "tu_1" });
  });

  it("emits message.delta on text_delta stream events", async () => {
    const session = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      {
        type: "stream_event",
        event: { type: "content_block_delta", delta: { type: "text_delta", text: "hello" } },
      },
      {
        type: "stream_event",
        event: { type: "content_block_delta", delta: { type: "text_delta", text: " world" } },
      },
      { type: "result", subtype: "success", session_id: "c1" },
    ];
    const adapter = new ClaudeAdapter(store, bus, {
      queryFn: () => mockQuery(events)() as any,
    });
    await adapter.runSessionLoop(session, "greet");
    const deltas = collected.filter(e => e.event_type === "message.delta");
    expect(deltas).toHaveLength(2);
    expect(deltas[0].payload.text).toBe("hello");
    expect(deltas[1].payload.text).toBe(" world");
  });

  it("emits tool.finished on user tool_result block", async () => {
    const session = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      {
        type: "stream_event",
        event: { type: "content_block_start", content_block: { type: "tool_use", id: "tu_1", name: "Bash" } },
      },
      {
        type: "user",
        message: {
          content: [{ type: "tool_result", tool_use_id: "tu_1", content: "ok" }],
        },
      },
      { type: "result", subtype: "success", session_id: "c1" },
    ];
    const adapter = new ClaudeAdapter(store, bus, {
      queryFn: () => mockQuery(events)() as any,
    });
    await adapter.runSessionLoop(session, "run ls");
    const finished = collected.find(e => e.event_type === "tool.finished");
    expect(finished?.payload).toMatchObject({ tool_use_id: "tu_1", result: "ok" });
  });

  it("emits session.error when result has is_error", async () => {
    const session = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      { type: "result", is_error: true, error: "boom" },
    ];
    const adapter = new ClaudeAdapter(store, bus, {
      queryFn: () => mockQuery(events)() as any,
    });
    await adapter.runSessionLoop(session, "x");
    const err = collected.find(e => e.event_type === "session.error");
    expect(err?.payload.error).toBe("boom");
    expect(session.status).toBe("error");
  });
});
