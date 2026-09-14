import { describe, it, expect, beforeEach } from "vitest";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";

function mockQuery(events: any[]) {
  return async function* () { for (const e of events) yield e; };
}

describe("ClaudeAdapter waiting_input", () => {
  let store: SessionStore; let bus: EventBus;
  beforeEach(() => { store = new SessionStore(); bus = new EventBus(); });

  it("transitions to waiting_input when result has terminal_reason: awaiting_input", async () => {
    const s = store.createSession({ workspacePath: "/w" });
    const events = [
      { type: "system", subtype: "init", session_id: "c1" },
      {
        type: "stream_event",
        event: { type: "content_block_start", content_block: { type: "tool_use", id: "tu_1", name: "AskUserQuestion" } },
      },
      {
        type: "result", subtype: "success", terminal_reason: "awaiting_input",
      },
    ];
    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => mockQuery(events)()) as any });
    await adapter.runSessionLoop(s, "x");
    expect(s.status).toBe("waiting_input");
  });
});
