import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FsPersistence } from "../src/persistence.js";
import type { GatewayEvent, Session } from "../src/types.js";

describe("FsPersistence", () => {
  let root: string;
  beforeEach(() => { root = mkdtempSync(join(tmpdir(), "gw-")); });
  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it("appends events to JSONL", () => {
    const p = new FsPersistence(root);
    const evt: GatewayEvent = {
      seq: 1, session_id: "s1", event_type: "message.delta",
      timestamp: "2026-01-01T00:00:00Z", payload: { text: "hi" },
    };
    p.appendEvent(evt);
    const file = join(root, "s1", "events.jsonl");
    expect(readFileSync(file, "utf8").trim()).toBe(JSON.stringify(evt));
  });

  it("writes meta.json on upsertMeta", () => {
    const p = new FsPersistence(root);
    const s: Session = {
      id: "s1", status: "running", workspacePath: "/w",
      createdAt: new Date("2026-01-01T00:00:00Z"),
      updatedAt: new Date("2026-01-01T00:00:00Z"),
      abortController: new AbortController(),
    };
    p.upsertMeta(s);
    const meta = JSON.parse(readFileSync(join(root, "s1", "meta.json"), "utf8"));
    expect(meta.id).toBe("s1");
    expect(meta.status).toBe("running");
  });

  it("listMetas recovers from disk", () => {
    const p = new FsPersistence(root);
    const s: Session = {
      id: "s1", status: "completed", workspacePath: "/w",
      createdAt: new Date(), updatedAt: new Date(),
      abortController: new AbortController(),
    };
    p.upsertMeta(s);
    const metas = p.listMetas();
    expect(metas).toHaveLength(1);
    expect(metas[0].id).toBe("s1");
  });

  it("readEvents parses JSONL back to GatewayEvents", () => {
    const p = new FsPersistence(root);
    const e1: GatewayEvent = { seq: 1, session_id: "s1", event_type: "message.delta", timestamp: "t1", payload: { text: "a" } };
    const e2: GatewayEvent = { seq: 2, session_id: "s1", event_type: "message.delta", timestamp: "t2", payload: { text: "b" } };
    p.appendEvent(e1);
    p.appendEvent(e2);
    const read = p.readEvents("s1");
    expect(read).toHaveLength(2);
    expect(read[0].payload.text).toBe("a");
    expect(read[1].seq).toBe(2);
  });

  it("readEvents returns empty when session has no events", () => {
    const p = new FsPersistence(root);
    expect(p.readEvents("nope")).toEqual([]);
  });
});
