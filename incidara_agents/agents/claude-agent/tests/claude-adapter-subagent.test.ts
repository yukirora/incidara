import { describe, it, expect } from "vitest";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";

function mockQuery(events: any[]) {
  return async function* () { for (const e of events) yield e; };
}

describe("subagent events", () => {
  it("propagates parent_tool_use_id on tool.started", async () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const collected: any[] = [];
    const orig = bus.publish.bind(bus);
    bus.publish = (e) => { collected.push(e); orig(e); };

    const s = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      {
        type: "stream_event",
        parent_tool_use_id: "parent_xyz",
        event: { type: "content_block_start", content_block: { type: "tool_use", id: "tu_1", name: "Bash" } },
      },
      { type: "result", subtype: "success" },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    const toolStarted = collected.find((e) => e.event_type === "tool.started");
    expect((toolStarted!.payload as any).parent_tool_use_id).toBe("parent_xyz");
  });

  it("omits parent_tool_use_id when event does not have it", async () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const collected: any[] = [];
    const orig = bus.publish.bind(bus);
    bus.publish = (e) => { collected.push(e); orig(e); };

    const s = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      {
        type: "stream_event",
        event: { type: "content_block_start", content_block: { type: "tool_use", id: "tu_1", name: "Bash" } },
      },
      { type: "result", subtype: "success" },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    const toolStarted = collected.find((e) => e.event_type === "tool.started");
    expect((toolStarted!.payload as any).parent_tool_use_id).toBeUndefined();
  });
});
