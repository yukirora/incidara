import type { Session } from "./types.js";
import { SessionStore } from "./session-store.js";
import { EventBus } from "./event-bus.js";
import type { PermissionManager } from "./permission-manager.js";

/** Thrown when a transient API error (504, 429, etc.) should be auto-retried */
class TransientApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TransientApiError";
  }
}

type QueryFn = (args: { prompt: string; options: any }) => AsyncIterable<any>;

type Deps = {
  queryFn?: QueryFn;
};

export function makePreToolUseHook(session: Session, store: SessionStore, bus: EventBus) {
  return async (input: any) => {
    bus.publish(
      store.appendEvent(session.id, "permission.requested", {
        tool_use_id: input.tool_use_id,
        tool_name: input.tool_name,
        input: input.tool_input,
      }),
    );
    return {};
  };
}

export function makePostToolUseHook(session: Session, store: SessionStore, bus: EventBus) {
  return async (input: any) => {
    // Don't emit permission.resolved here — the preToolUseHook already
    // emitted the correct event (allow/deny/ask). Emitting another
    // "allow" here is misleading and can contradict a prior deny.
    return {};
  };
}

export class ClaudeAdapter {
  private queryFn: QueryFn | undefined;
  // Per-session accumulator for text deltas; flushed into session.interrupted payload.
  private pendingText = new Map<string, string>();
  // Track whether we received stream_event thinking for the current turn.
  // If true, skip emitting thinking from the `assistant` event to avoid duplicates.
  private receivedStreamThinking = new Map<string, boolean>();
  // Map: parent_tool_use_id → task_id (from task_started events, used to enrich
  // assistant+parent events which don't carry task_id)
  private parentToTaskId = new Map<string, string>();

  constructor(
    private store: SessionStore,
    private bus: EventBus,
    deps: Deps = {},
    private permissionManager?: PermissionManager,
  ) {
    this.queryFn = deps.queryFn;
  }

  private async resolveQueryFn(): Promise<QueryFn> {
    if (this.queryFn) return this.queryFn;
    const mod = await import("@anthropic-ai/claude-agent-sdk");
    this.queryFn = mod.query as unknown as QueryFn;
    return this.queryFn;
  }

  startSession(session: Session, prompt: string): void {
    this.runSessionLoop(session, prompt).catch((err) => {
      console.error(`[adapter] session ${session.id} loop crashed:`, err);
      this.emitErrorAndClose(session, String(err?.message ?? err));
    });
  }

  async runSessionLoop(session: Session, prompt: string): Promise<void> {
    const queryFn = await this.resolveQueryFn();
    const options: any = {
      cwd: session.workspacePath,
      maxTurns: parseInt(process.env.MAX_TURNS || "50", 10),
      includePartialMessages: true,
      agentProgressSummaries: true,
      model: process.env.MODEL || undefined,
      // Thinking is controlled per-model via the proxy:
      // - Bedrock opus models: use MODEL=claude-opus-4-7-thinking (adaptive, proxy handles it)
      // - Direct Anthropic sonnet models: thinking enabled by default
      // No explicit thinking option needed — the SDK/model handles it
      permissionMode: "auto",
      settingSources: ["user", "project"],
      sandbox: { enabled: false },
      hooks: {
        PreToolUse: [{
          matcher: ".*",
          hooks: [this.permissionManager
            ? this.permissionManager.makePreToolUseHook(session.id)
            : makePreToolUseHook(session, this.store, this.bus)
          ],
        }],
        PostToolUse: [{ matcher: ".*", hooks: [makePostToolUseHook(session, this.store, this.bus)] }],
      },
    };
    if (session.claudeSessionId) options.resume = session.claudeSessionId;

    this.bus.publish(this.store.appendEvent(session.id, "message.user", { content: prompt }));
    this.pendingText.set(session.id, "");

    try {
      for await (const evt of queryFn({ prompt, options })) {
        if (session.abortController.signal.aborted) {
          this.store.setStatus(session.id, "interrupted");
          this.bus.publish(
            this.store.appendEvent(session.id, "session.interrupted", {
              reason: "user",
              partial_content: this.pendingText.get(session.id) ?? "",
            }),
          );
          this.pendingText.delete(session.id);
          return;
        }
        this.handleSdkEvent(session, evt);
      }
    } catch (err: any) {
      const msg = err?.message ?? String(err);
      // Transient API error (504, 429, etc.) — auto-retry with resume
      if (err instanceof TransientApiError || this.isTransientApiError(msg)) {
        await this.retryAfterTransientError(session, msg, queryFn, options);
      }
      // If conversation was lost (container restart), retry without resume
      else if (msg.includes("No conversation found") && options.resume) {
        console.log(`[adapter] session ${session.id}: conversation lost, retrying fresh`);
        delete options.resume;
        session.claudeSessionId = undefined;
        try {
          for await (const evt of queryFn({ prompt, options })) {
            if (session.abortController.signal.aborted) {
              this.store.setStatus(session.id, "interrupted");
              this.bus.publish(
                this.store.appendEvent(session.id, "session.interrupted", {
                  reason: "user",
                  partial_content: this.pendingText.get(session.id) ?? "",
                }),
              );
              this.pendingText.delete(session.id);
              return;
            }
            this.handleSdkEvent(session, evt);
          }
        } catch (retryErr: any) {
          this.emitErrorAndClose(session, retryErr?.message ?? String(retryErr));
        }
      } else {
        this.emitErrorAndClose(session, msg);
      }
    } finally {
      this.pendingText.delete(session.id);
    }
  }

  interrupt(session: Session): void {
    session.abortController.abort();
  }

  private handleSdkEvent(session: Session, evt: any): void {
    if (evt.type === "system" && evt.subtype === "init") {
      session.claudeSessionId = evt.session_id;
      this.bus.publish(
        this.store.appendEvent(session.id, "session.started", {
          claude_session_id: evt.session_id,
          workspace_path: session.workspacePath,
        }),
      );
      this.store.setStatus(session.id, "running");
      return;
    }

    // --- Sub-agent lifecycle events ---
    if (evt.type === "system" && evt.subtype === "task_started") {
      // Track parent_tool_use_id → task_id mapping for later events
      if (evt.tool_use_id && evt.task_id) {
        this.parentToTaskId.set(evt.tool_use_id as string, evt.task_id as string);
      }
      this.bus.publish(
        this.store.appendEvent(session.id, "subagent.started", {
          task_id: evt.task_id,
          tool_use_id: evt.tool_use_id,
          description: evt.description,
          task_type: evt.task_type,
          prompt: evt.prompt,
        }),
      );
      return;
    }

    if (evt.type === "system" && evt.subtype === "task_progress") {
      this.bus.publish(
        this.store.appendEvent(session.id, "subagent.progress", {
          task_id: evt.task_id,
          tool_use_id: evt.tool_use_id,
          summary: evt.summary,
          last_tool_name: evt.last_tool_name,
          usage: evt.usage,
        }),
      );
      return;
    }

    if (evt.type === "system" && evt.subtype === "task_notification") {
      this.bus.publish(
        this.store.appendEvent(session.id, "subagent.finished", {
          task_id: evt.task_id,
          tool_use_id: evt.tool_use_id,
          status: evt.status,
          summary: evt.summary,
          usage: evt.usage,
        }),
      );
      return;
    }

    if (evt.type === "system" && evt.subtype === "task_updated") {
      this.bus.publish(
        this.store.appendEvent(session.id, "subagent.updated", {
          task_id: evt.task_id,
          patch: evt.patch,
        }),
      );
      return;
    }

    // --- Sub-agent tool progress (periodic, for running tools) ---
    if (evt.type === "tool_progress" && evt.parent_tool_use_id) {
      this.bus.publish(
        this.store.appendEvent(session.id, "subagent.tool.progress", {
          parent_tool_use_id: evt.parent_tool_use_id,
          task_id: evt.task_id,
          tool_use_id: evt.tool_use_id,
          tool_name: evt.tool_name,
          elapsed_time_seconds: evt.elapsed_time_seconds,
        }),
      );
      return;
    }

    // --- Top-level MCP tool progress ---
    // The SDK may emit tool_progress without parent_tool_use_id for MCP tools,
    // or mcp_progress with richer progress/total/message data.
    if (evt.type === "tool_progress" && !evt.parent_tool_use_id) {
      this.bus.publish(
        this.store.appendEvent(session.id, "mcp.progress", {
          tool_use_id: evt.tool_use_id,
          tool_name: evt.tool_name,
          elapsed_time_seconds: evt.elapsed_time_seconds,
        }),
      );
      return;
    }

    // --- MCP tool progress (from ctx.report_progress in long-running tools) ---
    if (evt.type === "mcp_progress") {
      this.bus.publish(
        this.store.appendEvent(session.id, "mcp.progress", {
          tool_use_id: evt.toolUseID,
          server_name: evt.data?.serverName,
          tool_name: evt.data?.toolName,
          status: evt.data?.status,
          progress: evt.data?.progress,
          total: evt.data?.total,
          message: evt.data?.progressMessage || evt.data?.status,
          elapsed_time_ms: evt.data?.elapsedTimeMs,
        }),
      );
      return;
    }

    // --- Sub-agent assistant messages (thinking + text + tool_use) ---
    if (evt.type === "assistant" && evt.parent_tool_use_id && Array.isArray(evt.message?.content)) {
      const parentToolUseId = evt.parent_tool_use_id as string;
      // SDK doesn't set task_id on assistant events — look it up from our map
      const taskId = (evt.task_id as string | undefined) || this.parentToTaskId.get(parentToolUseId) || "";
      for (const block of evt.message.content) {
        if (block.type === "thinking") {
          this.bus.publish(
            this.store.appendEvent(session.id, "subagent.delta", {
              parent_tool_use_id: parentToolUseId,
              task_id: taskId,
              text: block.thinking,
              kind: "thinking",
            }),
          );
        } else if (block.type === "text") {
          this.bus.publish(
            this.store.appendEvent(session.id, "subagent.delta", {
              parent_tool_use_id: parentToolUseId,
              task_id: taskId,
              text: block.text,
              kind: "text",
            }),
          );
        } else if (block.type === "tool_use") {
          this.bus.publish(
            this.store.appendEvent(session.id, "subagent.tool.started", {
              parent_tool_use_id: parentToolUseId,
              task_id: taskId,
              tool_name: block.name,
              tool_use_id: block.id,
              input: block.input,
            }),
          );
        }
      }
      return;
    }

    // --- Sub-agent tool results ---
    if (evt.type === "user" && evt.parent_tool_use_id && Array.isArray(evt.message?.content)) {
      const parentToolUseId = evt.parent_tool_use_id as string;
      const taskId = (evt.task_id as string | undefined) || this.parentToTaskId.get(parentToolUseId) || "";
      for (const block of evt.message.content) {
        if (block.type === "tool_result") {
          const text = typeof block.content === "string"
            ? block.content
            : Array.isArray(block.content)
              ? block.content.map((c: any) => c.text || "").join("")
              : JSON.stringify(block.content);
          this.bus.publish(
            this.store.appendEvent(session.id, "subagent.tool.finished", {
              parent_tool_use_id: parentToolUseId,
              task_id: taskId,
              tool_use_id: block.tool_use_id,
              result: text,
              error: block.is_error === true,
            }),
          );
        }
      }
      return;
    }

    // --- Main agent events (no parent_tool_use_id) ---

    if (evt.type === "stream_event") {
      const e = evt.event;
      if (e?.type === "content_block_delta" && e.delta?.type === "text_delta") {
        this.pendingText.set(
          session.id,
          (this.pendingText.get(session.id) ?? "") + e.delta.text,
        );
        this.bus.publish(
          this.store.appendEvent(session.id, "message.delta", { text: e.delta.text }),
        );
        return;
      }
      if (e?.type === "content_block_delta" && e.delta?.type === "thinking_delta") {
        this.receivedStreamThinking.set(session.id, true);
        this.bus.publish(
          this.store.appendEvent(session.id, "message.delta", {
            text: e.delta.thinking, kind: "thinking",
          }),
        );
        return;
      }
      if (e?.type === "content_block_delta" && e.delta?.type === "input_json_delta") {
        const stdoutPayload: Record<string, unknown> = {
          partial: e.delta.partial_json,
        };
        // Include tool_use_id so the frontend can separate interleaved partials
        // from concurrent tool calls
        if (session.currentToolUseId) {
          stdoutPayload.tool_use_id = session.currentToolUseId;
        }
        this.bus.publish(
          this.store.appendEvent(session.id, "tool.stdout", stdoutPayload),
        );
        return;
      }
      if (e?.type === "content_block_start" && e.content_block?.type === "tool_use") {
        const tuId = e.content_block.id;
        const name = e.content_block.name;
        session.currentTool = name;
        session.currentToolUseId = tuId;
        this.store.setStatus(session.id, "busy");
        const payload: Record<string, unknown> = {
          tool_name: name,
          tool_use_id: tuId,
        };
        if (evt.parent_tool_use_id) payload.parent_tool_use_id = evt.parent_tool_use_id;

        // For AskUserQuestion, also emit a question.requested event
        // so the chat-ui can show the question card
        if (name === "AskUserQuestion") {
          this.bus.publish(
            this.store.appendEvent(session.id, "question.requested", {
              tool_use_id: tuId,
              tool_name: name,
              awaiting_answer: true,
              // questions will be populated when tool.stdout delivers the full input
              // The chat-ui will read the full input from the tool.started event
              // (after compaction merges tool.stdout into tool.started.payload.input)
            }),
          );
        }

        this.bus.publish(
          this.store.appendEvent(session.id, "tool.started", payload),
        );
        return;
      }
    }

    if (evt.type === "user" && Array.isArray(evt.message?.content)) {
      for (const block of evt.message.content) {
        if (block.type === "tool_result") {
          const text = typeof block.content === "string"
            ? block.content
            : Array.isArray(block.content)
              ? block.content.map((c: any) => c.text || "").join("")
              : JSON.stringify(block.content);
          session.currentTool = undefined;
          session.currentToolUseId = undefined;
          this.store.setStatus(session.id, "running");
          this.bus.publish(
            this.store.appendEvent(session.id, "tool.finished", {
              tool_use_id: block.tool_use_id,
              tool_name: "",
              result: text,
              error: block.is_error === true,
            }),
          );
        }
      }
      return;
    }

    if (evt.type === "assistant") {
      // Emit thinking blocks as message.delta events — but only if we didn't
      // already receive them via stream_event (thinking_delta).
      // This handles the case where includePartialMessages didn't produce stream events
      // (e.g., old SDK version, CCR mode, or SDK bug).
      const gotStreamThinking = this.receivedStreamThinking.get(session.id) ?? false;
      this.receivedStreamThinking.set(session.id, false); // reset for next turn

      // SDK 0.2.x: evt.message.content is an array of content blocks
      // SDK 0.3.x: evt.message may not have .content, or it may be structured differently
      const msgContent = evt.message?.content;
      const isArray = Array.isArray(msgContent);

      if (isArray) {
        const thinkingBlocks = msgContent.filter((b: any) => b.type === "thinking");
        if (!gotStreamThinking) {
          for (const block of thinkingBlocks) {
            if (block.thinking) {
              this.bus.publish(
                this.store.appendEvent(session.id, "message.delta", {
                  text: block.thinking,
                  kind: "thinking",
                }),
              );
            }
          }
        }
        const text = msgContent
          .filter((b: any) => b.type === "text")
          .map((b: any) => b.text)
          .join("");
        if (text) {
          session.lastMessagePreview = text.slice(0, 200);
          this.bus.publish(
            this.store.appendEvent(session.id, "message.agent", { content: text }),
          );
        }
      } else {
        // Fallback: message.content might be a string or the event shape is different
        const text = typeof msgContent === "string"
          ? msgContent
          : (evt as any).text || "";
        if (text) {
          session.lastMessagePreview = text.slice(0, 200);
          this.bus.publish(
            this.store.appendEvent(session.id, "message.agent", { content: text }),
          );
        }
      }
      return;
    }

    if (evt.type === "result") {
      if (evt.is_error || evt.subtype === "error") {
        const errorMsg = evt.error ?? "unknown error";
        if (this.isTransientApiError(String(errorMsg))) {
          // Transient API error (504, 502, 429, etc.) — auto-retry with resume
          // Don't call emitErrorAndClose — the retryAfterTransientError will handle it
          throw new TransientApiError(String(errorMsg));
        }
        this.emitErrorAndClose(session, errorMsg);
      } else if (evt.terminal_reason === "awaiting_input") {
        this.store.setStatus(session.id, "waiting_input");
        this.bus.publish(
          this.store.appendEvent(session.id, "session.waiting_input", {}),
        );
      } else {
        this.store.setStatus(session.id, "completed");
        this.bus.publish(
          this.store.appendEvent(session.id, "session.completed", {}),
        );
      }
    }
  }

  /** Check if an error message looks like a transient API error that should be retried */
  private isTransientApiError(message: string): boolean {
    const lower = message.toLowerCase();
    return (
      lower.includes("504") ||
      lower.includes("502") ||
      lower.includes("503") ||
      lower.includes("429") ||
      lower.includes("rate limit") ||
      lower.includes("overloaded") ||
      lower.includes("bad response status code 50") ||
      lower.includes("server error") ||
      lower.includes("service unavailable") ||
      lower.includes("gateway timeout") ||
      lower.includes("api error: 5")
    );
  }

  /** Resume a session after a transient error, with retries */
  private async retryAfterTransientError(
    session: Session,
    errorMessage: string,
    queryFn: QueryFn,
    options: any,
    maxRetries: number = 10,
  ): Promise<void> {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      // Exponential backoff: 30s, 45s, 68s, 102s, 153s, 230s, 345s, 518s, 777s, 900s (capped at 15min)
      const delay = Math.min(30000 * Math.pow(1.5, attempt - 1), 900000);
      console.log(`[adapter] session ${session.id}: transient API error (attempt ${attempt}/${maxRetries}), retrying in ${delay}ms: ${errorMessage.slice(0, 80)}`);

      this.bus.publish(
        this.store.appendEvent(session.id, "session.error", {
          error: errorMessage,
          recoverable: true,
          retry_attempt: attempt,
          retry_in_ms: delay,
        }),
      );

      // Wait before retrying
      await new Promise((resolve) => setTimeout(resolve, delay));

      if (session.abortController.signal.aborted) {
        this.store.setStatus(session.id, "interrupted");
        this.bus.publish(
          this.store.appendEvent(session.id, "session.interrupted", {
            reason: "user",
            partial_content: "",
          }),
        );
        return;
      }

      // Resume the session
      options.resume = session.claudeSessionId;
      const resumePrompt = "Continue from where you left off.";
      this.bus.publish(this.store.appendEvent(session.id, "message.user", { content: resumePrompt }));
      this.pendingText.set(session.id, "");

      try {
        for await (const evt of queryFn({ prompt: resumePrompt, options })) {
          if (session.abortController.signal.aborted) {
            this.store.setStatus(session.id, "interrupted");
            this.bus.publish(
              this.store.appendEvent(session.id, "session.interrupted", {
                reason: "user",
                partial_content: this.pendingText.get(session.id) ?? "",
              }),
            );
            this.pendingText.delete(session.id);
            return;
          }
          this.handleSdkEvent(session, evt);
        }
        // Success — the query loop completed normally
        return;
      } catch (err: any) {
        const retryMsg = err?.message ?? String(err);
        if (this.isTransientApiError(retryMsg) && attempt < maxRetries) {
          continue; // retry again
        }
        this.emitErrorAndClose(session, retryMsg);
        return;
      }
    }
    // Exhausted all retries
    this.emitErrorAndClose(session, `Failed after ${maxRetries} retries: ${errorMessage}`);
  }

  private emitErrorAndClose(session: Session, message: string): void {
    this.store.setStatus(session.id, "error");
    this.bus.publish(
      this.store.appendEvent(session.id, "session.error", {
        error: message, recoverable: false,
      }),
    );
  }
}
