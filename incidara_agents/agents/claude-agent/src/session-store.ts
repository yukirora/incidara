import { randomUUID } from "node:crypto";
import type { Session, SessionStatus, GatewayEvent, GatewayEventType } from "./types.js";

type CreateSessionInput = {
  workspacePath: string;
  title?: string;
};

type Options = {
  maxEventsPerSession?: number;
};

export class SessionStore {
  private sessions = new Map<string, Session>();
  private eventBuffers = new Map<string, GatewayEvent[]>();
  private seqCounters = new Map<string, number>();
  private readonly maxEvents: number;

  constructor(opts: Options = {}) {
    this.maxEvents = opts.maxEventsPerSession ?? 10000;
  }

  createSession(input: CreateSessionInput): Session {
    const id = `sess_${randomUUID().slice(0, 8)}`;
    const now = new Date();
    const session: Session = {
      id,
      status: "starting",
      title: input.title,
      workspacePath: input.workspacePath,
      createdAt: now,
      updatedAt: now,
      abortController: new AbortController(),
    };
    this.sessions.set(id, session);
    this.eventBuffers.set(id, []);
    this.seqCounters.set(id, 0);
    return session;
  }

  getSession(id: string): Session | undefined {
    return this.sessions.get(id);
  }

  listSessions(): Session[] {
    return [...this.sessions.values()];
  }

  deleteSession(id: string): boolean {
    if (!this.sessions.has(id)) return false;
    this.sessions.delete(id);
    this.eventBuffers.delete(id);
    this.seqCounters.delete(id);
    return true;
  }

  setStatus(id: string, to: SessionStatus): void {
    const s = this.sessions.get(id);
    if (!s) return;
    const from = s.status;
    if (from === to) return;
    s.status = to;
    s.updatedAt = new Date();
    this.appendEvent(id, "session.state_changed", { from, to });
  }

  appendEvent(
    id: string,
    event_type: GatewayEventType,
    payload: Record<string, unknown>,
  ): GatewayEvent {
    const seq = (this.seqCounters.get(id) ?? 0) + 1;
    this.seqCounters.set(id, seq);
    const event: GatewayEvent = {
      seq,
      session_id: id,
      event_type,
      timestamp: new Date().toISOString(),
      payload,
    };
    const buf = this.eventBuffers.get(id) ?? [];
    buf.push(event);
    while (buf.length > this.maxEvents) buf.shift();
    this.eventBuffers.set(id, buf);
    return event;
  }

  getEventsSince(id: string, afterSeq: number): GatewayEvent[] {
    const buf = this.eventBuffers.get(id) ?? [];
    return buf.filter((e) => e.seq > afterSeq);
  }

  /**
   * Restore a persisted session into the store, replaying its events into the
   * ring buffer. Used on startup to recover from disk.
   */
  restoreSession(session: Session, events: GatewayEvent[]): void {
    this.sessions.set(session.id, session);
    const buf = events.slice(-this.maxEvents);
    this.eventBuffers.set(session.id, buf);
    const lastSeq = events.length ? events[events.length - 1].seq : 0;
    this.seqCounters.set(session.id, lastSeq);
  }
}
