// OpenAI-compatible facade, backward-compat for chat-portal.
import { Router } from "express";
import { randomUUID, createHash } from "node:crypto";
import type { SessionStore } from "../session-store.js";
import type { EventBus, Subscriber } from "../event-bus.js";
import type { ClaudeAdapter } from "../claude-adapter.js";
import type { GatewayEvent } from "../types.js";

type Deps = { store: SessionStore; bus: EventBus; adapter: ClaudeAdapter };

// Deterministic mapping: session_key (from header or body) → sess_<hash>
export function sessionIdForKey(key: string): string {
  return `sess_${createHash("sha256").update(key).digest("hex").slice(0, 8)}`;
}

function renderForStream(evt: GatewayEvent): string | null {
  if (evt.event_type === "message.delta") return (evt.payload as any).text ?? "";
  if (evt.event_type === "tool.started") return `\n\n> **${(evt.payload as any).tool_name}**\n> `;
  if (evt.event_type === "tool.stdout") return (evt.payload as any).partial ?? "";
  if (evt.event_type === "tool.finished") {
    const text = (evt.payload as any).result ?? "";
    return `\n\`\`\`\n${text}\n\`\`\`\n`;
  }
  return null;
}

function renderForFinal(evt: GatewayEvent): string {
  if (evt.event_type === "message.agent") return (evt.payload as any).content ?? "";
  if (evt.event_type === "tool.started") {
    const p = evt.payload as any;
    return `\n**${p.tool_name}**\n`;
  }
  return "";
}

export function compatRouter({ store, bus, adapter }: Deps): Router {
  const r = Router();

  r.post("/chat/completions", (req, res) => {
    const sessionKey = (req.headers["x-session-key"] as string) || req.body.session_key || null;
    const messages = req.body.messages || [];
    const stream = !!req.body.stream;
    const userMsg = [...messages].reverse().find((m: any) => m.role === "user");
    if (!userMsg) return res.status(400).json({ error: "No user message" });
    const content: string = userMsg.content;
    const model = req.body.model || "claude-code";
    const responseId = `chatcmpl-${randomUUID().slice(0, 12)}`;
    const created = Math.floor(Date.now() / 1000);

    // Get or create session keyed by session_key
    let session = sessionKey ? store.getSession(sessionIdForKey(sessionKey)) : undefined;
    if (!session) {
      session = store.createSession({ workspacePath: process.env.CLAUDE_CWD || "/app/workspace" });
      // Override id to be deterministic from key (hack: replace map entry)
      if (sessionKey) {
        const stableId = sessionIdForKey(sessionKey);
        const origId = session.id;
        (session as any).id = stableId;
        (store as any).sessions.delete(origId);
        (store as any).sessions.set(stableId, session);
        (store as any).eventBuffers.set(stableId, (store as any).eventBuffers.get(origId) ?? []);
        (store as any).seqCounters.set(stableId, (store as any).seqCounters.get(origId) ?? 0);
      }
    }

    if (stream) {
      res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      res.setHeader("X-Accel-Buffering", "no");
      res.setHeader("X-Session-Key", sessionKey || "");
      res.flushHeaders();

      let sentFirst = false;
      let done = false;
      const writeChunk = (delta: any) => {
        res.write(
          `data: ${JSON.stringify({
            id: responseId, object: "chat.completion.chunk", created, model,
            choices: [{ delta, logprobs: null, finish_reason: null, index: 0 }],
            usage: null,
          })}\n\n`,
        );
      };

      const keepalive = setInterval(() => {
        if (!done && sentFirst) writeChunk({ content: "" });
      }, 3000);

      const sub: Subscriber = (evt) => {
        if (evt.event_type === "session.completed" || evt.event_type === "session.error") {
          done = true;
          res.write(
            `data: ${JSON.stringify({
              id: responseId, object: "chat.completion.chunk", created, model,
              choices: [{ delta: {}, logprobs: null, finish_reason: "stop", index: 0 }],
              usage: null,
            })}\n\n`,
          );
          res.write("data: [DONE]\n\n");
          res.end();
          bus.unsubscribe(session!.id, sub);
          clearInterval(keepalive);
          return;
        }
        const piece = renderForStream(evt);
        if (piece) {
          if (!sentFirst) {
            writeChunk({ content: "", role: "assistant" });
            sentFirst = true;
          }
          writeChunk({ content: piece });
        }
      };
      bus.subscribe(session.id, sub);
      res.on("close", () => {
        if (!done) {
          bus.unsubscribe(session!.id, sub);
          clearInterval(keepalive);
          done = true;
        }
      });
      adapter.startSession(session, content);
    } else {
      // Non-streaming: prefer message.agent (final complete text) per turn;
      // fall back to accumulated deltas for tool output.
      let fullText = "";
      let responded = false;
      const sub: Subscriber = (evt) => {
        if (responded) return;
        if (evt.event_type === "session.completed" || evt.event_type === "session.error") {
          responded = true;
          bus.unsubscribe(session!.id, sub);
          res.json({
            id: responseId, object: "chat.completion", created, model,
            choices: [{ index: 0, message: { role: "assistant", content: fullText }, finish_reason: "stop" }],
            session_key: sessionKey || null,
          });
          return;
        }
        // Only emit tool output + final agent text; skip per-token deltas
        // to avoid double-counting with message.agent.
        if (evt.event_type === "message.agent") {
          fullText += (evt.payload as any).content ?? "";
        } else if (evt.event_type === "tool.started") {
          fullText += `\n**${(evt.payload as any).tool_name}**\n`;
        } else if (evt.event_type === "tool.finished") {
          fullText += `\n\`\`\`\n${(evt.payload as any).result ?? ""}\n\`\`\`\n`;
        }
      };
      bus.subscribe(session.id, sub);
      res.on("close", () => {
        if (!responded) bus.unsubscribe(session!.id, sub);
      });
      adapter.startSession(session, content);
    }
  });

  r.get("/models", (_req, res) => {
    res.json({ object: "list", data: [{ id: "claude-code", object: "model", owned_by: "anthropic" }] });
  });
  r.get("/models/:id", (req, res) => {
    res.json({ id: req.params.id, object: "model", owned_by: "anthropic" });
  });

  return r;
}
