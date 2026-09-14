import { describe, it, expect } from "vitest";
import type { Session, GatewayEvent, SessionStatus, GatewayEventType } from "../src/types.js";

describe("types", () => {
  it("SessionStatus includes all lifecycle states", () => {
    const statuses: SessionStatus[] = [
      "starting", "running", "busy", "waiting_input",
      "interrupted", "completed", "error",
    ];
    expect(statuses.length).toBe(7);
  });

  it("GatewayEventType includes session/message/tool events", () => {
    const types: GatewayEventType[] = [
      "session.started", "session.state_changed", "session.completed",
      "session.error", "session.interrupted", "session.waiting_input",
      "message.user", "message.agent", "message.delta",
      "tool.started", "tool.stdout", "tool.finished",
      "permission.requested", "permission.resolved", "artifact.created",
    ];
    expect(types.length).toBe(15);
  });

  it("GatewayEvent envelope has required fields", () => {
    const e: GatewayEvent = {
      seq: 1,
      session_id: "sess_abc",
      event_type: "session.started",
      timestamp: "2026-04-16T12:00:00Z",
      payload: {},
    };
    expect(e.seq).toBe(1);
    expect(e.session_id).toBe("sess_abc");
  });

  it("Session has lifecycle fields", () => {
    const s: Session = {
      id: "sess_abc",
      status: "starting",
      workspacePath: "/app/workspace",
      createdAt: new Date(),
      updatedAt: new Date(),
      abortController: new AbortController(),
    };
    expect(s.status).toBe("starting");
  });
});
