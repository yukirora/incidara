import type { GatewayEvent } from "./types.js";
import type { FsPersistence } from "./persistence.js";

export type Subscriber = (event: GatewayEvent) => void;

export class EventBus {
  private subs = new Map<string, Set<Subscriber>>();

  constructor(private persistence?: FsPersistence) {}

  subscribe(sessionId: string, cb: Subscriber): void {
    let set = this.subs.get(sessionId);
    if (!set) {
      set = new Set();
      this.subs.set(sessionId, set);
    }
    set.add(cb);
  }

  unsubscribe(sessionId: string, cb: Subscriber): void {
    const set = this.subs.get(sessionId);
    if (!set) return;
    set.delete(cb);
    if (set.size === 0) this.subs.delete(sessionId);
  }

  publish(event: GatewayEvent): void {
    try { this.persistence?.appendEvent(event); }
    catch (err) { console.error("[event-bus] persistence error:", err); }
    const set = this.subs.get(event.session_id);
    if (!set) return;
    for (const cb of set) {
      try { cb(event); } catch (err) { console.error("[event-bus] subscriber error:", err); }
    }
  }
}
