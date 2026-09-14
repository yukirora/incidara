import { Router, type Response } from "express";
import type { SessionStore } from "../session-store.js";
import type { EventBus, Subscriber } from "../event-bus.js";
import type { FsPersistence } from "../persistence.js";
import type { GatewayEvent } from "../types.js";

type Deps = { store: SessionStore; bus: EventBus; persistence?: FsPersistence };

function writeSse(res: Response, evt: GatewayEvent) {
  res.write(`id: ${evt.seq}\n`);
  res.write(`event: ${evt.event_type}\n`);
  res.write(`data: ${JSON.stringify(evt)}\n\n`);
}

export function eventsRouter({ store, bus, persistence }: Deps): Router {
  const r = Router();

  /** Helper: get all events for a session from ring buffer + JSONL fallback */
  function getAllEvents(sessionId: string): GatewayEvent[] {
    const bufferEvents = store.getEventsSince(sessionId, 0);

    if (bufferEvents.length === 0 && persistence) {
      return persistence.readEvents(sessionId);
    }

    if (bufferEvents.length > 0 && bufferEvents[0].seq > 1 && persistence) {
      const diskEvents = persistence.readEvents(sessionId);
      const gap = diskEvents.filter((e: GatewayEvent) => e.seq < bufferEvents[0].seq);
      return [...gap, ...bufferEvents];
    }

    return bufferEvents;
  }

  r.get("/:id/rounds", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });

    const allEvents = getAllEvents(s.id);
    const userEvents = allEvents.filter((e: GatewayEvent) => e.event_type === "message.user");

    const rounds = userEvents.map((e: GatewayEvent) => ({
      seq: e.seq,
      preview: String((e.payload as Record<string, unknown>).content ?? "").slice(0, 80),
    }));

    res.json({ rounds, total: rounds.length });
  });

  r.get("/:id/events", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    const afterSeq = Number(req.query.after_seq ?? 0);
    const beforeSeq = req.query.before_seq !== undefined ? Number(req.query.before_seq) : undefined;
    const limit = Math.min(Number(req.query.limit ?? 500), 2000);
    let events = store.getEventsSince(s.id, afterSeq).slice(0, limit);

    // If ring buffer doesn't cover the requested range, fall back to JSONL on disk
    if (events.length === 0 && afterSeq === 0 && persistence) {
      const diskEvents = persistence.readEvents(s.id);
      events = diskEvents.filter((e: GatewayEvent) => e.seq > afterSeq).slice(0, limit);
    } else if (events.length > 0 && events[0].seq > afterSeq + 1 && persistence) {
      const diskEvents = persistence.readEvents(s.id);
      const gap = diskEvents.filter((e: GatewayEvent) => e.seq > afterSeq && e.seq < events[0].seq);
      events = [...gap, ...events].slice(0, limit);
    }

    // Apply before_seq filter if provided
    if (beforeSeq !== undefined) {
      events = events.filter((e: GatewayEvent) => e.seq < beforeSeq);
    }

    const nextAfterSeq = events.length ? events[events.length - 1].seq : afterSeq;
    res.json({ events, next_after_seq: nextAfterSeq });
  });

  r.get("/:id/events/stream", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });

    const afterSeq = Number(req.query.after_seq ?? req.header("Last-Event-ID") ?? 0);

    res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders();

    for (const evt of store.getEventsSince(s.id, afterSeq)) writeSse(res, evt);

    const sub: Subscriber = (evt) => {
      if (evt.seq <= afterSeq) return;
      writeSse(res, evt);
    };
    bus.subscribe(s.id, sub);

    const heartbeat = setInterval(() => {
      res.write(": heartbeat\n\n");
    }, 15_000);

    req.on("close", () => {
      clearInterval(heartbeat);
      bus.unsubscribe(s.id, sub);
    });
  });

  return r;
}
