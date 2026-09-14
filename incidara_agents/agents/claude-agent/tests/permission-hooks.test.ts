import { describe, it, expect } from "vitest";
import { SessionStore } from "../src/session-store.js";
import { EventBus } from "../src/event-bus.js";
import { makePreToolUseHook, makePostToolUseHook } from "../src/claude-adapter.js";

describe("permission hooks", () => {
  it("PreToolUse hook emits permission.requested", async () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const collected: any[] = [];
    const orig = bus.publish.bind(bus);
    bus.publish = (e) => { collected.push(e); orig(e); };
    const s = store.createSession({ workspacePath: "/w" });
    const hook = makePreToolUseHook(s, store, bus);
    await hook({ tool_use_id: "tu_1", tool_name: "Bash", tool_input: { command: "ls" } });
    const evt = collected.find((e) => e.event_type === "permission.requested");
    expect(evt?.payload).toMatchObject({
      tool_use_id: "tu_1", tool_name: "Bash", input: { command: "ls" },
    });
  });

  it("PostToolUse does not overwrite the earlier permission decision", async () => {
    const store = new SessionStore();
    const bus = new EventBus();
    const collected: any[] = [];
    const orig = bus.publish.bind(bus);
    bus.publish = (e) => { collected.push(e); orig(e); };
    const s = store.createSession({ workspacePath: "/w" });
    const hook = makePostToolUseHook(s, store, bus);
    await hook({ tool_use_id: "tu_1" });
    const evt = collected.find((e) => e.event_type === "permission.resolved");
    expect(evt).toBeUndefined();
  });
});
