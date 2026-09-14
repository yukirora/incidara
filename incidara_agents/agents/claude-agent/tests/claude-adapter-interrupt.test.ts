import { describe, it, expect } from "vitest";
import { ClaudeAdapter } from "../src/claude-adapter.js";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";

describe("ClaudeAdapter interrupt", () => {
  it("emits session.interrupted with partial content on abort", async () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const collected: any[] = [];
    const orig = bus.publish.bind(bus);
    bus.publish = (e) => { collected.push(e); orig(e); };
    const s = store.createSession({ workspacePath: "/w" });

    // Emit a few deltas, then hang — the abort should break the loop.
    const slow = async function* () {
      yield { type: "system", subtype: "init", session_id: "c1" };
      yield { type: "stream_event", event: { type: "content_block_delta", delta: { type: "text_delta", text: "hi" } } };
      yield { type: "stream_event", event: { type: "content_block_delta", delta: { type: "text_delta", text: " there" } } };
      await new Promise((r) => setTimeout(r, 50));
      yield { type: "result", subtype: "success" };
    };

    const adapter = new ClaudeAdapter(store, bus, { queryFn: (() => slow()) as any });
    const p = adapter.runSessionLoop(s, "x");
    setTimeout(() => s.abortController.abort(), 10);
    await p;
    const interrupted = collected.find((e) => e.event_type === "session.interrupted");
    expect(interrupted).toBeDefined();
    expect((interrupted.payload as any).partial_content).toContain("hi");
    expect(s.status).toBe("interrupted");
  });
});
