import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import { createPool } from "./db/client.js";
import { SessionStore } from "./session-store.js";
import { ScheduleStore, computeNextFire } from "./schedule-store.js";
import { signup } from "./auth.js";
import type pg from "pg";

const DB_URL = process.env.CHAT_UI_DATABASE_URL;

// ─── computeNextFire unit tests (no DB needed) ───────────────────────────────

describe("computeNextFire", () => {
  const base = new Date("2026-01-01T00:00:00.000Z");

  it("once: returns runAt when in the future", () => {
    const runAt = new Date("2026-06-01T00:00:00.000Z");
    const result = computeNextFire({ triggerType: "once", runAt, from: base });
    expect(result?.toISOString()).toBe(runAt.toISOString());
  });

  it("once: returns null when runAt is in the past", () => {
    const runAt = new Date("2025-01-01T00:00:00.000Z");
    const result = computeNextFire({ triggerType: "once", runAt, from: base });
    expect(result).toBeNull();
  });

  it("once: returns null when runAt equals from", () => {
    const result = computeNextFire({ triggerType: "once", runAt: base, from: base });
    expect(result).toBeNull();
  });

  it("once: throws when runAt missing", () => {
    expect(() => computeNextFire({ triggerType: "once", from: base })).toThrow();
  });

  it("interval: returns from + intervalSeconds", () => {
    const result = computeNextFire({ triggerType: "interval", intervalSeconds: 3600, from: base });
    expect(result?.getTime()).toBe(base.getTime() + 3600 * 1000);
  });

  it("interval: throws when intervalSeconds missing", () => {
    expect(() => computeNextFire({ triggerType: "interval", from: base })).toThrow();
  });

  it("cron: returns next occurrence after from", () => {
    // "every minute" — next should be exactly at the next minute boundary
    const from = new Date("2026-01-01T00:00:30.000Z"); // 30s past minute
    const result = computeNextFire({
      triggerType: "cron",
      cronExpr: "* * * * *",
      timezone: "UTC",
      from,
    });
    expect(result).not.toBeNull();
    // Should be at the next minute
    expect(result!.getTime()).toBeGreaterThan(from.getTime());
    expect(result!.getSeconds()).toBe(0);
  });

  it("cron: respects timezone (Asia/Shanghai is UTC+8)", () => {
    // "0 2 * * *" in Asia/Shanghai = 18:00 UTC
    const result = computeNextFire({
      triggerType: "cron",
      cronExpr: "0 2 * * *",
      timezone: "Asia/Shanghai",
      from: new Date("2026-01-01T00:00:00.000Z"), // midnight UTC
    });
    expect(result).not.toBeNull();
    // Should fire at 18:00 UTC same day (2:00 AM Shanghai)
    expect(result!.getUTCHours()).toBe(18);
  });

  it("cron: throws on invalid expression", () => {
    expect(() =>
      computeNextFire({ triggerType: "cron", cronExpr: "not-valid", from: base })
    ).toThrow();
  });
});

// ─── ScheduleStore integration tests ─────────────────────────────────────────

describe.skipIf(!DB_URL)("ScheduleStore", () => {
  let pool: pg.Pool;
  let scheduleStore: ScheduleStore;
  let sessionStore: SessionStore;
  const ownerEmail = `sched_owner_${Date.now()}@example.com`;
  const otherEmail = `sched_other_${Date.now()}@example.com`;

  beforeAll(async () => {
    pool = createPool(DB_URL!);
    scheduleStore = new ScheduleStore(pool);
    sessionStore = new SessionStore(pool);
    await signup(pool, { email: ownerEmail, password: "testpass1234" }).catch(() => {});
    await signup(pool, { email: otherEmail, password: "testpass1234" }).catch(() => {});
  });

  afterAll(async () => {
    await pool.query("DELETE FROM schedules WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM sessions WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.query("DELETE FROM users WHERE email = ANY($1)", [[ownerEmail, otherEmail]]).catch(() => {});
    await pool.end();
  });

  beforeEach(async () => {
    await pool.query("DELETE FROM schedules WHERE owner_email = ANY($1)", [[ownerEmail, otherEmail]]);
  });

  // ── create + getById ──────────────────────────────────────────────────────

  it("creates an interval schedule and sets next_fire_at", async () => {
    const before = new Date();
    const schedule = await scheduleStore.create({
      agentId: "agent-a",
      ownerEmail,
      prompt: "do the thing",
      triggerType: "interval",
      intervalSeconds: 3600,
      sessionMode: "new",
    });

    expect(schedule.id).toBeTypeOf("number");
    expect(schedule.trigger_type).toBe("interval");
    expect(schedule.interval_seconds).toBe(3600);
    expect(schedule.enabled).toBe(true);
    expect(schedule.next_fire_at).not.toBeNull();
    // next_fire_at should be approximately now + 1h
    const diff = schedule.next_fire_at!.getTime() - before.getTime();
    expect(diff).toBeGreaterThanOrEqual(3599_000);
    expect(diff).toBeLessThanOrEqual(3601_500);
  });

  it("creates a once schedule with correct next_fire_at", async () => {
    const runAt = new Date(Date.now() + 10 * 60 * 1000); // 10 min from now
    const schedule = await scheduleStore.create({
      agentId: "agent-a",
      ownerEmail,
      prompt: "once",
      triggerType: "once",
      runAt,
      sessionMode: "new",
    });

    expect(schedule.trigger_type).toBe("once");
    expect(schedule.next_fire_at?.toISOString()).toBe(runAt.toISOString());
  });

  it("creates a cron schedule with correct next_fire_at", async () => {
    const schedule = await scheduleStore.create({
      agentId: "agent-a",
      ownerEmail,
      prompt: "cron job",
      triggerType: "cron",
      cronExpr: "0 * * * *", // every hour on the hour
      timezone: "UTC",
      sessionMode: "new",
    });

    expect(schedule.trigger_type).toBe("cron");
    expect(schedule.cron_expr).toBe("0 * * * *");
    expect(schedule.next_fire_at).not.toBeNull();
    // should fire at the next top of the hour
    expect(schedule.next_fire_at!.getMinutes()).toBe(0);
  });

  it("getById returns the schedule", async () => {
    const created = await scheduleStore.create({
      agentId: "agent-a",
      ownerEmail,
      prompt: "get me",
      triggerType: "interval",
      intervalSeconds: 60,
      sessionMode: "new",
    });

    const fetched = await scheduleStore.getById(created.id);
    expect(fetched?.id).toBe(created.id);
    expect(fetched?.prompt).toBe("get me");
  });

  it("getById returns null for unknown id", async () => {
    expect(await scheduleStore.getById(999_999_999)).toBeNull();
  });

  // ── list ──────────────────────────────────────────────────────────────────

  it("list returns schedules for owner", async () => {
    await scheduleStore.create({ agentId: "agent-a", ownerEmail, prompt: "a", triggerType: "interval", intervalSeconds: 60, sessionMode: "new" });
    await scheduleStore.create({ agentId: "agent-b", ownerEmail, prompt: "b", triggerType: "interval", intervalSeconds: 120, sessionMode: "new" });
    // Other owner — should NOT appear
    await scheduleStore.create({ agentId: "agent-a", ownerEmail: otherEmail, prompt: "other", triggerType: "interval", intervalSeconds: 60, sessionMode: "new" });

    const list = await scheduleStore.list({ ownerEmail });
    expect(list.length).toBe(2);
    expect(list.every((s) => s.owner_email === ownerEmail)).toBe(true);
  });

  it("list filters by agentId", async () => {
    await scheduleStore.create({ agentId: "agent-x", ownerEmail, prompt: "x", triggerType: "interval", intervalSeconds: 60, sessionMode: "new" });
    await scheduleStore.create({ agentId: "agent-y", ownerEmail, prompt: "y", triggerType: "interval", intervalSeconds: 60, sessionMode: "new" });

    const list = await scheduleStore.list({ ownerEmail, agentId: "agent-x" });
    expect(list.length).toBe(1);
    expect(list[0].agent_id).toBe("agent-x");
  });

  it("list filters by enabled", async () => {
    const s = await scheduleStore.create({ agentId: "agent-a", ownerEmail, prompt: "p", triggerType: "interval", intervalSeconds: 60, sessionMode: "new" });
    await scheduleStore.update(s.id, { enabled: false });

    const enabled = await scheduleStore.list({ ownerEmail, enabled: true });
    const disabled = await scheduleStore.list({ ownerEmail, enabled: false });
    expect(enabled.length).toBe(0);
    expect(disabled.length).toBe(1);
  });

  // ── update ────────────────────────────────────────────────────────────────

  it("update: changing prompt does not recompute next_fire_at", async () => {
    const s = await scheduleStore.create({ agentId: "agent-a", ownerEmail, prompt: "old", triggerType: "interval", intervalSeconds: 3600, sessionMode: "new" });
    const originalNextFire = s.next_fire_at!.getTime();

    // Small delay to ensure time advances
    await new Promise((r) => setTimeout(r, 10));
    const updated = await scheduleStore.update(s.id, { prompt: "new" });

    expect(updated?.prompt).toBe("new");
    // next_fire_at should be unchanged (within a small tolerance)
    expect(updated?.next_fire_at!.getTime()).toBe(originalNextFire);
  });

  it("update: changing intervalSeconds recomputes next_fire_at", async () => {
    const s = await scheduleStore.create({ agentId: "agent-a", ownerEmail, prompt: "p", triggerType: "interval", intervalSeconds: 3600, sessionMode: "new" });
    const before = new Date();

    await new Promise((r) => setTimeout(r, 10));
    const updated = await scheduleStore.update(s.id, { intervalSeconds: 7200 });

    expect(updated?.interval_seconds).toBe(7200);
    // new next_fire_at should be approximately now + 2h
    const diff = updated!.next_fire_at!.getTime() - before.getTime();
    expect(diff).toBeGreaterThanOrEqual(7199_000);
    expect(diff).toBeLessThanOrEqual(7202_000);
  });

  it("update: can disable a schedule", async () => {
    const s = await scheduleStore.create({ agentId: "agent-a", ownerEmail, prompt: "p", triggerType: "interval", intervalSeconds: 60, sessionMode: "new" });
    const updated = await scheduleStore.update(s.id, { enabled: false });
    expect(updated?.enabled).toBe(false);
  });

  it("update: returns null for unknown id", async () => {
    const result = await scheduleStore.update(999_999_999, { prompt: "x" });
    expect(result).toBeNull();
  });

  // ── delete ────────────────────────────────────────────────────────────────

  it("delete removes the schedule", async () => {
    const s = await scheduleStore.create({ agentId: "agent-a", ownerEmail, prompt: "del", triggerType: "interval", intervalSeconds: 60, sessionMode: "new" });
    await scheduleStore.delete(s.id);
    expect(await scheduleStore.getById(s.id)).toBeNull();
  });

  // ── claimDue ──────────────────────────────────────────────────────────────

  it("claimDue returns schedules with next_fire_at <= now", async () => {
    // Create a schedule with a past next_fire_at by directly inserting
    const pastTime = new Date(Date.now() - 5000);
    await pool.query(
      `INSERT INTO schedules (agent_id, owner_email, prompt, trigger_type, interval_seconds, timezone, session_mode, enabled, next_fire_at)
       VALUES ('agent-a', $1, 'due', 'interval', 60, 'UTC', 'new', true, $2)`,
      [ownerEmail, pastTime]
    );

    const futureTime = new Date(Date.now() + 60_000);
    await pool.query(
      `INSERT INTO schedules (agent_id, owner_email, prompt, trigger_type, interval_seconds, timezone, session_mode, enabled, next_fire_at)
       VALUES ('agent-a', $1, 'not due', 'interval', 60, 'UTC', 'new', true, $2)`,
      [ownerEmail, futureTime]
    );

    const due = await scheduleStore.claimDue({ now: new Date(), limit: 10 });
    const dueMine = due.filter((s) => s.owner_email === ownerEmail);
    expect(dueMine.length).toBe(1);
    expect(dueMine[0].prompt).toBe("due");
  });

  it("claimDue does not return disabled schedules", async () => {
    const pastTime = new Date(Date.now() - 5000);
    await pool.query(
      `INSERT INTO schedules (agent_id, owner_email, prompt, trigger_type, interval_seconds, timezone, session_mode, enabled, next_fire_at)
       VALUES ('agent-a', $1, 'disabled-due', 'interval', 60, 'UTC', 'new', false, $2)`,
      [ownerEmail, pastTime]
    );

    const due = await scheduleStore.claimDue({ now: new Date(), limit: 10 });
    const disabledDue = due.filter((s) => s.prompt === "disabled-due");
    expect(disabledDue.length).toBe(0);
  });

  // ── recordFiring ──────────────────────────────────────────────────────────

  it("recordFiring updates last_fired_at and next_fire_at", async () => {
    const s = await scheduleStore.create({ agentId: "agent-a", ownerEmail, prompt: "fire", triggerType: "interval", intervalSeconds: 3600, sessionMode: "new" });

    const firedAt = new Date();
    const nextFireAt = new Date(firedAt.getTime() + 3600_000);
    const updated = await scheduleStore.recordFiring(s.id, { firedAt, nextFireAt });

    expect(updated?.last_fired_at?.toISOString()).toBe(firedAt.toISOString());
    expect(updated?.next_fire_at?.toISOString()).toBe(nextFireAt.toISOString());
    expect(updated?.enabled).toBe(true);
  });

  it("recordFiring with disableIfDone=true and nextFireAt=null disables schedule", async () => {
    const s = await scheduleStore.create({
      agentId: "agent-a",
      ownerEmail,
      prompt: "once",
      triggerType: "once",
      runAt: new Date(Date.now() + 10_000),
      sessionMode: "new",
    });

    const firedAt = new Date();
    const updated = await scheduleStore.recordFiring(s.id, { firedAt, nextFireAt: null, disableIfDone: true });

    expect(updated?.last_fired_at?.toISOString()).toBe(firedAt.toISOString());
    expect(updated?.next_fire_at).toBeNull();
    expect(updated?.enabled).toBe(false);
  });

  it("recordFiring returns null for unknown id", async () => {
    const result = await scheduleStore.recordFiring(999_999_999, { firedAt: new Date(), nextFireAt: null });
    expect(result).toBeNull();
  });
});
