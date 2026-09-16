import { Router } from "express";
import type { SessionStore } from "../session-store.js";
import type { ClaudeAdapter } from "../claude-adapter.js";
import type { Session } from "../types.js";

type Deps = { store: SessionStore; adapter: ClaudeAdapter };

function toJson(s: Session) {
  return {
    id: s.id,
    claude_session_id: s.claudeSessionId,
    status: s.status,
    title: s.title,
    workspace_path: s.workspacePath,
    current_tool: s.currentTool,
    last_message_preview: s.lastMessagePreview,
    created_at: s.createdAt.toISOString(),
    updated_at: s.updatedAt.toISOString(),
  };
}

export function sessionsRouter({ store, adapter }: Deps): Router {
  const r = Router();

  r.post("/", (req, res) => {
    const { prompt, workspace_path, title, disallowed_tools, append_system_prompt } = req.body ?? {};
    if (typeof prompt !== "string" || !prompt.trim()) {
      return res.status(400).json({ error: "prompt is required" });
    }
    if (disallowed_tools !== undefined && (!Array.isArray(disallowed_tools) || !disallowed_tools.every((tool) => typeof tool === "string"))) {
      return res.status(400).json({ error: "disallowed_tools must be an array of strings" });
    }
    if (append_system_prompt !== undefined && typeof append_system_prompt !== "string") {
      return res.status(400).json({ error: "append_system_prompt must be a string" });
    }
    const cwd = typeof workspace_path === "string" && workspace_path
      ? workspace_path
      : (process.env.CLAUDE_CWD || "/app/workspace");
    const s = store.createSession({ workspacePath: cwd, title });
    adapter.startSession(s, prompt, {
      disallowedTools: disallowed_tools,
      appendSystemPrompt: append_system_prompt,
    });
    res.status(201).json(toJson(s));
  });

  r.get("/", (req, res) => {
    const status = (req.query.status as string | undefined);
    const limit = Math.min(Number(req.query.limit ?? 100), 500);
    let list = store.listSessions();
    if (status && status !== "all") list = list.filter((s) => s.status === status);
    list = list.slice(-limit);
    res.json({ sessions: list.map(toJson) });
  });

  r.get("/:id", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    res.json(toJson(s));
  });

  r.delete("/:id", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    adapter.interrupt(s);
    store.deleteSession(s.id);
    res.status(204).end();
  });

  return r;
}
