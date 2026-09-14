import { randomUUID } from "node:crypto";
import type { SessionStore } from "./session-store.js";
import type { EventBus } from "./event-bus.js";

export type PermissionRule = {
  pattern: string;    // e.g. "Bash(git *)", "Read(*)", "*"
  decision: "allow" | "deny" | "ask";
};

export type PendingApproval = {
  id: string;
  sessionId: string;
  toolName: string;
  toolInput: any;
  resolve: (decision: "allow" | "deny") => void;
  createdAt: Date;
};

export class PermissionManager {
  private pending = new Map<string, PendingApproval>();
  private timeoutMs: number;

  constructor(
    private rules: PermissionRule[],
    private store: SessionStore,
    private bus: EventBus,
    timeoutMs = 300_000, // 5 min default
  ) {
    // Node.js setTimeout overflows for values > 2^31-1 (≈24.8 days).
    // Cap to MAX_SAFE_TIMEOUT to avoid the timeout firing immediately.
    const MAX_SAFE_TIMEOUT = 2_147_483_647;
    this.timeoutMs = Math.min(timeoutMs, MAX_SAFE_TIMEOUT);
  }

  /**
   * Match tool call against rules. Returns the decision for this tool.
   * Rules are evaluated top-to-bottom; first match wins.
   */
  matchRule(toolName: string, toolInput: any): "allow" | "deny" | "ask" {
    for (const rule of this.rules) {
      if (matchPattern(rule.pattern, toolName, toolInput)) {
        return rule.decision;
      }
    }
    return "allow"; // default if no rules match
  }

  /**
   * Create a PreToolUse hook function for a session.
   * - "allow" rules → return immediately with {decision: "allow"}
   * - "deny" rules → return immediately with {decision: "deny"}
   * - "ask" rules → emit permission.requested event, BLOCK until user approves/denies
   */
  makePreToolUseHook(sessionId: string) {
    return async (input: any) => {
      const toolName = String(input.tool_name ?? "unknown");
      const toolInput = input.tool_input;
      const decision = this.matchRule(toolName, toolInput);

      if (decision === "allow") {
        // Auto-approve — emit event but don't block
        this.bus.publish(
          this.store.appendEvent(sessionId, "permission.requested", {
            tool_name: toolName,
            input: toolInput,
            auto_decision: "allow",
          }),
        );
        return {};
      }

      if (decision === "deny") {
        this.bus.publish(
          this.store.appendEvent(sessionId, "permission.requested", {
            tool_name: toolName,
            input: toolInput,
            auto_decision: "deny",
          }),
        );
        return { decision: "deny" };
      }

      // decision === "ask" → block and wait for user approval
      const permId = randomUUID();
      this.bus.publish(
        this.store.appendEvent(sessionId, "permission.requested", {
          permission_id: permId,
          tool_name: toolName,
          input: toolInput,
          awaiting_approval: true,
        }),
      );

      const userDecision = await new Promise<"allow" | "deny">((resolve) => {
        this.pending.set(permId, {
          id: permId,
          sessionId,
          toolName,
          toolInput,
          resolve,
          createdAt: new Date(),
        });

        // Timeout: auto-deny after timeoutMs. Set to 0 to wait forever.
        if (this.timeoutMs > 0) {
          setTimeout(() => {
            if (this.pending.has(permId)) {
              this.pending.delete(permId);
              resolve("deny");
            }
          }, this.timeoutMs);
        }
      });

      this.pending.delete(permId);

      this.bus.publish(
        this.store.appendEvent(sessionId, "permission.resolved", {
          permission_id: permId,
          decision: userDecision,
          tool_name: toolName,
        }),
      );

      return userDecision === "allow"
        ? { decision: "allow" }
        : {
            decision: "deny",
            error: `User denied this action. Do NOT retry or attempt the same operation through alternative methods. The user explicitly rejected ${toolName}.`,
          };
    };
  }

  /**
   * Called when user approves or denies a permission via the API.
   */
  resolvePermission(permId: string, decision: "allow" | "deny"): boolean {
    const pending = this.pending.get(permId);
    if (!pending) return false;
    pending.resolve(decision);
    return true;
  }

  /**
   * List all pending approvals (for debugging / UI display).
   */
  listPending(): PendingApproval[] {
    return [...this.pending.values()];
  }
}

/**
 * Match a pattern like "Bash(git *)" against a tool call.
 * Patterns:
 *   "*" → matches everything
 *   "ToolName(*)" → matches any input for that tool
 *   "ToolName(pattern)" → matches if the tool's primary input contains the pattern
 *   "ToolName" → matches the tool name exactly (no input filter)
 */
function matchPattern(pattern: string, toolName: string, toolInput: any): boolean {
  if (pattern === "*") return true;

  const match = pattern.match(/^([.\w-]+)(?:\((.+)\))?$/);
  if (!match) return false;

  const [, patternTool, inputPattern] = match;
  if (patternTool !== toolName) return false;
  if (!inputPattern) return true; // just tool name match
  if (inputPattern === "*") return true; // any input

  // Match against the primary input value (command for Bash, file_path for Read, etc.)
  const primaryInput = extractPrimaryInput(toolName, toolInput);
  return globMatch(inputPattern, primaryInput);
}

function extractPrimaryInput(toolName: string, toolInput: any): string {
  if (!toolInput) return "";
  if (typeof toolInput === "string") return toolInput;
  // Common tool input patterns
  if (toolInput.command) return String(toolInput.command);
  if (toolInput.file_path) return String(toolInput.file_path);
  if (toolInput.pattern) return String(toolInput.pattern);
  if (toolInput.query) return String(toolInput.query);
  if (toolInput.prompt) return String(toolInput.prompt);
  return JSON.stringify(toolInput);
}

function globMatch(pattern: string, text: string): boolean {
  // Simple glob: * matches anything, rest is literal
  const regex = new RegExp(
    "^" + pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$",
    "i",
  );
  return regex.test(text);
}
