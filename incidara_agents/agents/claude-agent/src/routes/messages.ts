import { Router } from "express";
import fs from "fs";
import path from "path";
import type { SessionStore } from "../session-store.js";
import type { ClaudeAdapter } from "../claude-adapter.js";
import type { PermissionManager } from "../permission-manager.js";

type Deps = { store: SessionStore; adapter: ClaudeAdapter; permissionManager?: PermissionManager };

export function messagesRouter({ store, adapter, permissionManager }: Deps): Router {
  const r = Router();

  r.post("/:id/messages", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    const content = req.body?.content;
    if (typeof content !== "string" || !content.trim()) {
      return res.status(400).json({ error: "content is required" });
    }
    const allowed = new Set(["running", "waiting_input", "interrupted", "completed", "error"]);
    if (!allowed.has(s.status)) {
      return res.status(409).json({
        error: `session status is ${s.status}; cannot accept new message`,
      });
    }
    // Reset abort controller for sessions being restarted. In particular,
    // interrupted sessions carry an aborted signal from prior runs.
    if (s.status === "completed" || s.status === "error" || s.status === "interrupted") {
      s.abortController = new AbortController();
    }
    adapter.startSession(s, content);
    res.status(202).json({ accepted: true, session_id: s.id });
  });

  r.post("/:id/interrupt", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    adapter.interrupt(s);
    res.json({ ok: true });
  });

  r.post("/:id/resume", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    const resumable = new Set(["interrupted", "waiting_input", "completed", "error"]);
    if (!resumable.has(s.status)) {
      return res.status(409).json({ error: `cannot resume from ${s.status}` });
    }
    const content = req.body?.content ?? "Continue.";
    // Reset for a fresh query() loop — preserves claudeSessionId for context
    s.abortController = new AbortController();
    adapter.startSession(s, content);
    res.status(202).json({ accepted: true, status: "running" });
  });

  r.post("/:id/permissions/:permId/approve", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    if (!permissionManager) return res.status(503).json({ error: "permission manager not available" });
    const ok = permissionManager.resolvePermission(req.params.permId, "allow");
    if (!ok) return res.status(404).json({ error: "permission not found or already resolved" });
    res.json({ ok: true, decision: "allow" });
  });

  r.post("/:id/permissions/:permId/deny", (req, res) => {
    const s = store.getSession(req.params.id);
    if (!s) return res.status(404).json({ error: "not found" });
    if (!permissionManager) return res.status(503).json({ error: "permission manager not available" });
    const ok = permissionManager.resolvePermission(req.params.permId, "deny");
    if (!ok) return res.status(404).json({ error: "permission not found or already resolved" });
    res.json({ ok: true, decision: "deny" });
  });

  /**
   * GET /:id/subagents/:taskId/transcript
   * Read a sub-agent's transcript file from disk and return structured data:
   *  - toolCalls: [{ name, input, result, isError }]
   *  - text: agent text output snippets
   *  - thinking: agent thinking snippets
   *  - finalResult: last assistant text message (the sub-agent's conclusion)
   */
  r.get("/:id/subagents/:taskId/transcript", (req, res) => {
    const session = store.getSession(req.params.id);
    if (!session) return res.status(404).json({ error: "session not found" });

    const taskId = req.params.taskId;
    const claudeSessionId = session.claudeSessionId;
    if (!claudeSessionId) {
      return res.status(404).json({ error: "no claude session id" });
    }

    // Transcript files are at /root/.claude/projects/-app-workspace/{claudeSessionId}/subagents/agent-{taskId}.jsonl
    const projectsDir = path.join(
      process.env.CLAUDE_HOME || "/root/.claude",
      "projects",
      "-app-workspace"
    );
    const transcriptPath = path.join(
      projectsDir,
      claudeSessionId,
      "subagents",
      `agent-${taskId}.jsonl`
    );

    if (!fs.existsSync(transcriptPath)) {
      // Try scanning all project dirs if the exact path doesn't exist
      // (claudeSessionId might differ from the directory name)
      try {
        const dirs = fs.readdirSync(projectsDir);
        for (const dir of dirs) {
          const candidate = path.join(projectsDir, dir, "subagents", `agent-${taskId}.jsonl`);
          if (fs.existsSync(candidate)) {
            return parseTranscript(candidate, taskId, res);
          }
        }
      } catch {
        // ignore
      }
      return res.status(404).json({ error: "transcript not found", path: transcriptPath });
    }

    return parseTranscript(transcriptPath, taskId, res);
  });

  return r;
}

function parseTranscript(transcriptPath: string, taskId: string, res: any) {
  try {
    const lines = fs.readFileSync(transcriptPath, "utf-8").split("\n").filter(Boolean);
    const toolCalls: Array<{
      name: string;
      input: any;
      result: string;
      isError: boolean;
    }> = [];
    const textSnippets: string[] = [];
    const thinkingSnippets: string[] = [];
    let finalResult = "";

    // Track pending tool calls by tool_use_id to match results
    const pendingTools = new Map<string, { name: string; input: any; idx: number }>();

    for (const line of lines) {
      let entry: any;
      try { entry = JSON.parse(line); } catch { continue; }

      if (entry.type === "assistant" && entry.message?.content) {
        for (const block of entry.message.content) {
          if (block.type === "tool_use") {
            const idx = toolCalls.length;
            toolCalls.push({ name: block.name, input: block.input, result: "", isError: false });
            pendingTools.set(block.id, { name: block.name, input: block.input, idx });
          } else if (block.type === "text" && block.text) {
            textSnippets.push(block.text);
            finalResult = block.text; // last text block is the final result
          } else if (block.type === "thinking" && block.thinking) {
            thinkingSnippets.push(block.thinking);
          }
        }
      } else if (entry.type === "user" && entry.message?.content) {
        const content = entry.message.content;
        if (Array.isArray(content)) {
          for (const block of content) {
            if (block.type === "tool_result" && block.tool_use_id) {
              const pending = pendingTools.get(block.tool_use_id);
              if (pending) {
                const text = typeof block.content === "string"
                  ? block.content
                  : Array.isArray(block.content)
                    ? block.content.map((c: any) => c.text || "").join("")
                    : JSON.stringify(block.content);
                toolCalls[pending.idx].result = text.slice(0, 10000);
                toolCalls[pending.idx].isError = block.is_error === true;
                pendingTools.delete(block.tool_use_id);
              }
            }
          }
        }
      }
    }

    res.json({
      task_id: taskId,
      toolCalls,
      textSnippets: textSnippets.map(t => t.slice(0, 2000)),
      thinkingSnippets: thinkingSnippets.map(t => t.slice(0, 2000)),
      finalResult: finalResult.slice(0, 5000),
      totalMessages: lines.length,
    });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
}
