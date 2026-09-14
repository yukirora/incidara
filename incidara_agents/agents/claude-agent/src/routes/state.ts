import { Router } from "express";
import type { SessionStore } from "../session-store.js";

type Deps = { store: SessionStore };

export function stateRouter({ store }: Deps): Router {
  const r = Router();

  r.get("/:id/state", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    const events = store.getEventsSince(s.id, 0);
    const latestSeq = events.length ? events[events.length - 1].seq : 0;
    res.json({
      session_id: s.id,
      status: s.status,
      current_tool: s.currentTool,
      last_message_preview: s.lastMessagePreview,
      waiting_for_input: s.status === "waiting_input",
      claude_session_id: s.claudeSessionId,
      latest_seq: latestSeq,
    });
  });

  return r;
}
