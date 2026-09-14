import { describe, it, expect, beforeEach, vi } from "vitest";
import { EventBus } from "../src/event-bus.js";
import type { GatewayEvent } from "../src/types.js";

const sample: GatewayEvent = {
  seq: 1, session_id: "s1", event_type: "message.delta",
  timestamp: "2026-04-16T00:00:00Z", payload: { text: "hi" },
};

describe("EventBus", () => {
  let bus: EventBus;
  beforeEach(() => { bus = new EventBus(); });

  it("publish invokes subscribers for the matching session", () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    bus.subscribe("s1", cb1);
    bus.subscribe("s2", cb2);
    bus.publish(sample);
    expect(cb1).toHaveBeenCalledWith(sample);
    expect(cb2).not.toHaveBeenCalled();
  });

  it("unsubscribe removes callback", () => {
    const cb = vi.fn();
    bus.subscribe("s1", cb);
    bus.unsubscribe("s1", cb);
    bus.publish(sample);
    expect(cb).not.toHaveBeenCalled();
  });

  it("supports multiple subscribers per session", () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    bus.subscribe("s1", cb1);
    bus.subscribe("s1", cb2);
    bus.publish(sample);
    expect(cb1).toHaveBeenCalledOnce();
    expect(cb2).toHaveBeenCalledOnce();
  });

  it("no-op when no subscribers exist", () => {
    expect(() => bus.publish(sample)).not.toThrow();
  });
});
