import { describe, it, expect, beforeEach } from "vitest";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import type { GatewayEvent } from "../src/types.js";

function mockQuery(events: any[]) {
  return async function* () { for (const e of events) yield e; };
}

function collect(bus: EventBus): GatewayEvent[] {
  const out: GatewayEvent[] = [];
  const orig = bus.publish.bind(bus);
  bus.publish = (e) => { out.push(e); orig(e); };
  return out;
}

describe("ClaudeAdapter edge cases", () => {
  let store: SessionStore; let bus: EventBus;
  beforeEach(() => { store = new SessionStore(); bus = new EventBus(); });

  it("maps thinking_delta to message.delta with kind=thinking", async () => {
    const s = store.createSession({ workspacePath: "/w" });
    const collected = collect(bus);
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      { type: "stream_event", event: { type: "content_block_delta", delta: { type: "thinking_delta", thinking: "pondering" } } },
      { type: "result", subtype: "success" },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    const delta = collected.find((e) => e.event_type === "message.delta");
    expect(delta?.payload).toMatchObject({ text: "pondering", kind: "thinking" });
  });

  it("maps input_json_delta to tool.stdout", async () => {
    const s = store.createSession({ workspacePath: "/w" });
    const collected = collect(bus);
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      { type: "stream_event", event: { type: "content_block_start", content_block: { type: "tool_use", id: "tu1", name: "Bash" } } },
      { type: "stream_event", event: { type: "content_block_delta", delta: { type: "input_json_delta", partial_json: "{\"cmd\":\"ls" } } },
      { type: "stream_event", event: { type: "content_block_delta", delta: { type: "input_json_delta", partial_json: "\"}" } } },
      { type: "result", subtype: "success" },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    const stdouts = collected.filter((e) => e.event_type === "tool.stdout");
    expect(stdouts).toHaveLength(2);
    expect(stdouts.map((e) => (e.payload as any).partial).join("")).toBe("{\"cmd\":\"ls\"}");
  });

  it("handles multiple consecutive tool calls with proper state transitions", async () => {
    const s = store.createSession({ workspacePath: "/w" });
    const collected = collect(bus);
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      { type: "stream_event", event: { type: "content_block_start", content_block: { type: "tool_use", id: "tu1", name: "Bash" } } },
      { type: "user", message: { content: [{ type: "tool_result", tool_use_id: "tu1", content: "ok1" }] } },
      { type: "stream_event", event: { type: "content_block_start", content_block: { type: "tool_use", id: "tu2", name: "Read" } } },
      { type: "user", message: { content: [{ type: "tool_result", tool_use_id: "tu2", content: "ok2" }] } },
      { type: "result", subtype: "success" },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    const starts = collected.filter((e) => e.event_type === "tool.started");
    const finishes = collected.filter((e) => e.event_type === "tool.finished");
    expect(starts).toHaveLength(2);
    expect(finishes).toHaveLength(2);
    expect(s.status).toBe("completed");
  });

  it("assistant message with no text blocks does not emit message.agent", async () => {
    const s = store.createSession({ workspacePath: "/w" });
    const collected = collect(bus);
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      { type: "assistant", message: { content: [{ type: "tool_use", id: "tu1", name: "Bash", input: {} }] } },
      { type: "result", subtype: "success" },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    const agentMsgs = collected.filter((e) => e.event_type === "message.agent");
    expect(agentMsgs).toHaveLength(0);
  });

  it("captures lastMessagePreview from assistant text", async () => {
    const s = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      { type: "assistant", message: { content: [{ type: "text", text: "The answer is 42." }] } },
      { type: "result", subtype: "success" },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    expect(s.lastMessagePreview).toBe("The answer is 42.");
  });
});
