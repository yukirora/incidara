import { Router } from "express";
import type { SessionStore } from "../session-store.js";
import * as fs from "fs";
import * as path from "path";

type Deps = { store: SessionStore };

interface UsageEntry {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  seq: number;
  timestamp: string;
}

interface SessionUsage {
  gateway_session_id: string;
  claude_session_id: string;
  turn_details: TurnUsage[];
  models: Record<string, UsageEntry>;
  turns: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_input_tokens: number;
  total_cache_read_input_tokens: number;
  total_tokens: number;
  first_event: string | null;
  last_event: string | null;
  unmapped: boolean;
}

interface TurnUsage {
  seq: number;
  timestamp: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

function parseJsonlUsage(filePath: string): {
  models: Record<string, UsageEntry>;
  turns: TurnUsage[];
  turns_total: number;
  first_event: string | null;
  last_event: string | null;
} {
  const models: Record<string, UsageEntry> = {};
  const turns: TurnUsage[] = [];
  let first_event: string | null = null;
  let last_event: string | null = null;

  // Deduplicate by message.id — same as ccusage.
  // SDK writes multiple `assistant` entries per turn (one per content block:
  // thinking, text, tool_use) all sharing the same message.id and usage.
  // We keep only one entry per message.id, preferring the one with the
  // highest token total (the complete/final entry).
  const seenMessageIds = new Map<string, { index: number; total: number }>();

  const data = fs.readFileSync(filePath, "utf-8");
  for (const line of data.split("\n")) {
    if (!line.trim()) continue;
    try {
      const entry = JSON.parse(line);
      const ts = entry.timestamp as string | undefined;
      if (ts) {
        if (!first_event) first_event = ts;
        last_event = ts;
      }

      if (entry.type === "assistant") {
        const msg = entry.message;
        if (!msg || typeof msg !== "object") continue;
        const usage = (msg as any).usage;
        if (!usage) continue;

        const rawModel = ((msg as any).model as string) || "unknown";
        if (rawModel === "<synthetic>") continue;
        const model = rawModel;

        const messageId = (msg as any).id as string | undefined;
        const input_tokens = usage.input_tokens ?? 0;
        const output_tokens = usage.output_tokens ?? 0;
        const cache_creation_input_tokens = usage.cache_creation_input_tokens ?? 0;
        const cache_read_input_tokens = usage.cache_read_input_tokens ?? 0;
        const tokenTotal = input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens;

        if (messageId) {
          const existing = seenMessageIds.get(messageId);
          if (existing) {
            // Duplicate — keep the one with higher token total
            if (tokenTotal > existing.total) {
              // Replace the previous turn entry
              turns[existing.index] = {
                seq: 0,
                timestamp: ts || "",
                model,
                input_tokens,
                output_tokens,
                cache_creation_input_tokens,
                cache_read_input_tokens,
              };
            }
            // Skip — don't add to models tally either
            continue;
          }
          seenMessageIds.set(messageId, { index: turns.length, total: tokenTotal });
        }

        turns.push({
          seq: 0, // filled by caller using gateway_seq mapping
          timestamp: ts || "",
          model,
          input_tokens,
          output_tokens,
          cache_creation_input_tokens,
          cache_read_input_tokens,
        });

        if (!models[model]) {
          models[model] = { model, input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, seq: 0, timestamp: "" };
        }
        const m = models[model];
        m.input_tokens += input_tokens;
        m.output_tokens += output_tokens;
        m.cache_creation_input_tokens += cache_creation_input_tokens;
        m.cache_read_input_tokens += cache_read_input_tokens;
      }
    } catch {
      // skip malformed lines
    }
  }
  return { models, turns, turns_total: turns.length, first_event, last_event };
}

export function usageRouter({ store }: Deps): Router {
  const r = Router();

  /**
   * GET /usage
   *
   * Scans all JSONL files in ~/.claude/projects/, extracts token usage
   * per claude session, and maps them to gateway session IDs.
   *
   * Query params:
   *   include_subagents=true  — include sub-agent JSONL files (default: false)
   */
  r.get("/", (_req, res) => {
    const claudeHome = process.env.CLAUDE_HOME || "/root/.claude";
    const projectsDir = path.join(claudeHome, "projects");
    const includeSubagents = _req.query.include_subagents === "true";

    if (!fs.existsSync(projectsDir)) {
      res.json({ sessions: [], summary: makeSummary([]), unmapped: [] });
      return;
    }

    // Find all JSONL files recursively in projects dir
    const jsonlFiles: string[] = [];
    function scanDir(dir: string, depth: number) {
      if (depth > 3) return; // prevent deep recursion
      if (!fs.existsSync(dir)) return;
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          // Skip subagents dirs unless requested
          if (entry.name === "subagents" && !includeSubagents) continue;
          scanDir(fullPath, depth + 1);
        } else if (entry.name.endsWith(".jsonl")) {
          // Skip subagent files unless requested
          if (!includeSubagents && fullPath.includes("/subagents/")) continue;
          jsonlFiles.push(fullPath);
        }
      }
    }
    scanDir(projectsDir, 0);

    // Build claude_session_id → gateway_session_id mapping from store
    const sessions = store.listSessions();
    const claudeToGateway = new Map<string, string>();
    for (const s of sessions) {
      if (s.claudeSessionId) {
        claudeToGateway.set(s.claudeSessionId, s.id);
      }
    }

    // Parse each JSONL file
    const sessionUsages: SessionUsage[] = [];
    const unmappedUsages: SessionUsage[] = [];
    const subagentUsages: { filePath: string; usage: SessionUsage }[] = [];

    for (const filePath of jsonlFiles) {
      const basename = path.basename(filePath, ".jsonl");
      const isSubagent = filePath.includes("/subagents/");

      const parsed = parseJsonlUsage(filePath);
      if (parsed.turns_total === 0) continue; // skip empty sessions

      // Calculate totals
      let total_input = 0, total_output = 0, total_cache_create = 0, total_cache_read = 0;
      for (const m of Object.values(parsed.models)) {
        total_input += m.input_tokens;
        total_output += m.output_tokens;
        total_cache_create += m.cache_creation_input_tokens;
        total_cache_read += m.cache_read_input_tokens;
      }

      const usage: SessionUsage = {
        gateway_session_id: "",
        claude_session_id: basename,
        models: parsed.models,
        turns: parsed.turns_total,
        turn_details: parsed.turns,
        total_input_tokens: total_input,
        total_output_tokens: total_output,
        total_cache_creation_input_tokens: total_cache_create,
        total_cache_read_input_tokens: total_cache_read,
        total_tokens: total_input + total_output + total_cache_create + total_cache_read,
        first_event: parsed.first_event,
        last_event: parsed.last_event,
        unmapped: false,
      };

      if (!isSubagent) {
        const gwId = claudeToGateway.get(basename);
        if (gwId) {
          usage.gateway_session_id = gwId;
          sessionUsages.push(usage);
        } else {
          usage.unmapped = true;
          unmappedUsages.push(usage);
        }
      } else if (includeSubagents) {
        // Defer sub-agent processing until all main sessions are loaded
        subagentUsages.push({ filePath, usage });
      }
    }

    // Second pass: merge sub-agent usage into parent sessions
    if (includeSubagents) {
      const sessionByGwId = new Map<string, SessionUsage>();
      for (const s of sessionUsages) {
        sessionByGwId.set(s.gateway_session_id, s);
      }

      for (const { filePath, usage } of subagentUsages) {
        const parentClaudeId = path.basename(path.dirname(path.dirname(filePath)));
        const parentGwId = claudeToGateway.get(parentClaudeId);

        if (parentGwId) {
          const parent = sessionByGwId.get(parentGwId);
          if (parent) {
            parent.turns += usage.turns;
            parent.total_input_tokens += usage.total_input_tokens;
            parent.total_output_tokens += usage.total_output_tokens;
            parent.total_cache_creation_input_tokens += usage.total_cache_creation_input_tokens;
            parent.total_cache_read_input_tokens += usage.total_cache_read_input_tokens;
            parent.total_tokens += usage.total_tokens;
            for (const [model, entry] of Object.entries(usage.models)) {
              if (!parent.models[model]) {
                parent.models[model] = { model, input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, seq: 0, timestamp: "" };
              }
              parent.models[model].input_tokens += entry.input_tokens;
              parent.models[model].output_tokens += entry.output_tokens;
              parent.models[model].cache_creation_input_tokens += entry.cache_creation_input_tokens;
              parent.models[model].cache_read_input_tokens += entry.cache_read_input_tokens;
            }
          } else {
            usage.claude_session_id = "subagent:" + usage.claude_session_id;
            usage.unmapped = true;
            unmappedUsages.push(usage);
          }
        } else {
          usage.claude_session_id = "subagent:" + usage.claude_session_id;
          usage.unmapped = true;
          unmappedUsages.push(usage);
        }
      }
    }

    res.json({
      sessions: sessionUsages,
      unmapped: unmappedUsages,
      summary: makeSummary(sessionUsages),
    });
  });

  /**
   * GET /sessions/:sessionId
   *
   * Get usage for a specific gateway session by reading its JSONL file.
   * Includes sub-agent usage if include_subagents=true.
   */
  r.get("/sessions/:sessionId", (req, res) => {
    const sessionId = req.params.sessionId;
    const session = store.getSession(sessionId);
    if (!session) {
      res.status(404).json({ error: "session not found" });
      return;
    }

    const claudeHome = process.env.CLAUDE_HOME || "/root/.claude";
    const includeSubagents = req.query.include_subagents === "true";

    const mainFile = findJsonlFile(claudeHome, session.claudeSessionId || "");
    if (!mainFile) {
      res.json({ session_id: sessionId, claude_session_id: session.claudeSessionId, models: {}, turns: 0, total_tokens: 0, subagents: [] });
      return;
    }

    const mainParsed = parseJsonlUsage(mainFile);
    const result = buildUsageResponse(sessionId, session.claudeSessionId || "", mainParsed);

    // Collect sub-agent usage and merge into parent totals
    if (includeSubagents) {
      // Sub-agents are in a directory with the same name as the .jsonl file
      // e.g. /root/.claude/projects/-app-workspace/{sessionId}/subagents/agent-*.jsonl
      const mainDir = path.join(path.dirname(mainFile), session.claudeSessionId || "");
      const subDir = path.join(mainDir, "subagents");
      if (fs.existsSync(subDir)) {
        for (const file of fs.readdirSync(subDir)) {
          if (!file.endsWith(".jsonl")) continue;
          const subParsed = parseJsonlUsage(path.join(subDir, file));
          if (subParsed.turns_total > 0) {
            const subId = path.basename(file, ".jsonl");
            const subUsage = buildUsageResponse("", subId, subParsed);
            result.subagents.push({
              ...subUsage,
              claude_session_id: subId,
            });

            // Merge sub-agent usage into parent totals
            result.turns += subParsed.turns_total;
            result.total_input_tokens += subUsage.total_input_tokens;
            result.total_output_tokens += subUsage.total_output_tokens;
            result.total_cache_creation_input_tokens += subUsage.total_cache_creation_input_tokens;
            result.total_cache_read_input_tokens += subUsage.total_cache_read_input_tokens;
            result.total_tokens += subUsage.total_tokens;

            // Merge model usage
            for (const [model, entry] of Object.entries(subUsage.models)) {
              if (!result.models[model]) {
                result.models[model] = { model, input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, seq: 0, timestamp: "" };
              }
              result.models[model].input_tokens += entry.input_tokens;
              result.models[model].output_tokens += entry.output_tokens;
              result.models[model].cache_creation_input_tokens += entry.cache_creation_input_tokens;
              result.models[model].cache_read_input_tokens += entry.cache_read_input_tokens;
            }
          }
        }
      }
    }

    res.json(result);
  });

  /**
   * GET /usage/sessions/:sessionId/turns
   *
   * Returns per-turn usage with timestamps for task-level splitting.
   * Each turn = one `assistant` entry with token counts.
   */
  r.get("/sessions/:sessionId/turns", (req, res) => {
    const sessionId = req.params.sessionId;
    const session = store.getSession(sessionId);
    if (!session) {
      res.status(404).json({ error: "session not found" });
      return;
    }

    const claudeHome = process.env.CLAUDE_HOME || "/root/.claude";
    const mainFile = findJsonlFile(claudeHome, session.claudeSessionId || "");
    if (!mainFile) {
      res.json({ session_id: sessionId, turns: [] });
      return;
    }

    const parsed = parseJsonlUsage(mainFile);
    // Include sub-agent turns
    const subDir = path.join(path.dirname(mainFile), session.claudeSessionId || "", "subagents");
    const allTurns = [...parsed.turns];
    if (fs.existsSync(subDir)) {
      for (const file of fs.readdirSync(subDir)) {
        if (!file.endsWith(".jsonl")) continue;
        const subParsed = parseJsonlUsage(path.join(subDir, file));
        for (const t of subParsed.turns) {
          allTurns.push({ ...t, model: `subagent:${t.model}` });
        }
      }
    }

    res.json({
      session_id: sessionId,
      claude_session_id: session.claudeSessionId,
      turns: allTurns,
    });
  });

  return r;
}

function findJsonlFile(claudeHome: string, claudeSessionId: string): string | null {
  const projectsDir = path.join(claudeHome, "projects");
  if (!fs.existsSync(projectsDir)) return null;

  for (const dir of fs.readdirSync(projectsDir)) {
    const dirPath = path.join(projectsDir, dir);
    if (!fs.statSync(dirPath).isDirectory()) continue;
    const candidate = path.join(dirPath, claudeSessionId + ".jsonl");
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function buildUsageResponse(gatewayId: string, claudeId: string, parsed: ReturnType<typeof parseJsonlUsage>) {
  let total_input = 0, total_output = 0, total_cache_create = 0, total_cache_read = 0;
  for (const m of Object.values(parsed.models)) {
    total_input += m.input_tokens;
    total_output += m.output_tokens;
    total_cache_create += m.cache_creation_input_tokens;
    total_cache_read += m.cache_read_input_tokens;
  }

  return {
    session_id: gatewayId,
    claude_session_id: claudeId,
    models: parsed.models,
    turns: parsed.turns_total,
    total_input_tokens: total_input,
    total_output_tokens: total_output,
    total_cache_creation_input_tokens: total_cache_create,
    total_cache_read_input_tokens: total_cache_read,
    total_tokens: total_input + total_output + total_cache_create + total_cache_read,
    first_event: parsed.first_event,
    last_event: parsed.last_event,
    subagents: [] as any[],
  };
}

function makeSummary(sessions: SessionUsage[]) {
  let total_input = 0, total_output = 0, total_cache_create = 0, total_cache_read = 0, total_turns = 0;
  const models: Record<string, UsageEntry> = {};

  for (const s of sessions) {
    total_input += s.total_input_tokens;
    total_output += s.total_output_tokens;
    total_cache_create += s.total_cache_creation_input_tokens;
    total_cache_read += s.total_cache_read_input_tokens;
    total_turns += s.turns;

    for (const [name, entry] of Object.entries(s.models)) {
      if (!models[name]) {
        models[name] = { model: name, input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, seq: 0, timestamp: "" };
      }
      models[name].input_tokens += entry.input_tokens;
      models[name].output_tokens += entry.output_tokens;
      models[name].cache_creation_input_tokens += entry.cache_creation_input_tokens;
      models[name].cache_read_input_tokens += entry.cache_read_input_tokens;
    }
  }

  return {
    total_sessions: sessions.length,
    total_turns,
    total_input_tokens: total_input,
    total_output_tokens: total_output,
    total_cache_creation_input_tokens: total_cache_create,
    total_cache_read_input_tokens: total_cache_read,
    total_tokens: total_input + total_output + total_cache_create + total_cache_read,
    models,
  };
}
