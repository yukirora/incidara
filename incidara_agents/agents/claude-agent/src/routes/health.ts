import { Router } from "express";
import type { SessionStore } from "../session-store.js";

const startedAt = Date.now();

export function healthRouter({ store }: { store: SessionStore }): Router {
  const r = Router();
  r.get("/health", (_req, res) => {
    const sessions = store.listSessions();
    res.json({
      status: "ok",
      uptime_s: Math.floor((Date.now() - startedAt) / 1000),
      sessions_active: sessions.filter((s) =>
        !["completed", "error"].includes(s.status)).length,
      sessions_total: sessions.length,
    });
  });
  return r;
}
