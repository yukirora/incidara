import { useState } from "react";

function prettyPrintJson(s: string): string {
  try {
    const parsed = JSON.parse(s);
    // If the top-level has a single key like "result" that contains JSON, unwrap it
    if (typeof parsed === "object" && parsed !== null && Object.keys(parsed).length === 1) {
      const inner = parsed[Object.keys(parsed)[0]];
      if (typeof inner === "string") {
        try {
          return JSON.stringify(JSON.parse(inner), null, 2);
        } catch { /* inner not JSON */ }
      }
    }
    return JSON.stringify(parsed, null, 2);
  } catch {
    return s;
  }
}

interface ToolCardProps {
  toolName: string;
  toolUseId: string;
  toolInput?: string;
  stdout?: string;
  status: "running" | "completed" | "error";
  result?: string;
  mcpProgress?: { progress?: number; total?: number; message?: string; elapsed_time_ms?: number };
}

export function ToolCard({ toolName, toolUseId: _toolUseId, toolInput, stdout, status, result, mcpProgress }: ToolCardProps) {
  const [expanded, setExpanded] = useState(false);

  // Try to parse toolInput as JSON for preview and pretty-printing
  let parsedInput: Record<string, unknown> | null = null;
  let displayInput = toolInput ?? "";
  if (toolInput) {
    try {
      parsedInput = JSON.parse(toolInput);
      // Always pretty-print if it's valid JSON
      displayInput = JSON.stringify(parsedInput, null, 2);
    } catch {
      // Not JSON — keep as-is
    }
  }

  // Build a short preview from the parsed input
  let inputPreview = "";
  if (parsedInput) {
    if (parsedInput.command) {
      inputPreview = String(parsedInput.command);
    } else if (parsedInput.file_path) {
      inputPreview = String(parsedInput.file_path);
    } else if (parsedInput.pattern) {
      inputPreview = String(parsedInput.pattern);
    } else if (parsedInput.skill) {
      // Skill tool: "skill-name [args]"
      inputPreview = String(parsedInput.skill) + (parsedInput.args ? " " + parsedInput.args : "");
    } else {
      // Generic: show first key=value pair
      const firstKey = Object.keys(parsedInput)[0];
      if (firstKey) {
        const firstVal = String(parsedInput[firstKey]);
        inputPreview = firstKey + "=" + (firstVal.length > 40 ? firstVal.slice(0, 40) + "…" : firstVal);
      }
    }
  } else if (toolInput) {
    inputPreview = toolInput.length > 80 ? toolInput.slice(0, 80) + "…" : toolInput;
  }

  return (
    <div className="border border-zinc-200 rounded-lg bg-zinc-50 text-xs mb-2 overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-zinc-100 select-none"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="text-base">🛠</span>
        <span className="font-mono text-zinc-700 font-medium">{toolName}</span>
        {inputPreview && (
          <>
            <span className="text-zinc-300">·</span>
            <span className="font-mono text-zinc-500 flex-1 truncate">{inputPreview}</span>
          </>
        )}
        {!inputPreview && <span className="flex-1" />}
        {status === "running" && mcpProgress && (
          <span className="flex items-center gap-1.5 text-blue-500 font-medium text-[11px]">
            {mcpProgress.progress != null && mcpProgress.total != null && mcpProgress.total > 0 && (
              <>
                <span className="w-16 h-1.5 bg-blue-100 rounded-full overflow-hidden">
                  <span
                    className="block h-full bg-blue-500 rounded-full transition-all duration-300"
                    style={{ width: `${Math.min(100, (mcpProgress.progress / mcpProgress.total) * 100)}%` }}
                  />
                </span>
                <span>{mcpProgress.progress}/{mcpProgress.total}</span>
              </>
            )}
            {mcpProgress.message && (
              <span className="truncate max-w-[200px]" title={mcpProgress.message}>
                {mcpProgress.message}
              </span>
            )}
            {mcpProgress.elapsed_time_ms != null && !mcpProgress.message && (
              <span>{(mcpProgress.elapsed_time_ms / 1000).toFixed(1)}s</span>
            )}
          </span>
        )}
        {status === "running" && !mcpProgress && (
          <span className="text-blue-500 animate-pulse font-medium">running…</span>
        )}
        {status === "completed" && (
          <span className="text-emerald-600 font-medium">✓ done</span>
        )}
        {status === "error" && (
          <span className="text-red-500 font-medium">✗ error</span>
        )}
        <span className="text-zinc-400 ml-1">{expanded ? "▲" : "▼"}</span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-zinc-200">
          {/* Input section */}
          {displayInput && (
            <div className="px-3 py-2 bg-blue-50 border-b border-zinc-200">
              <div className="text-[10px] font-semibold text-blue-500 uppercase tracking-wider mb-1">Input</div>
              <pre className="text-zinc-800 whitespace-pre-wrap font-mono text-xs leading-relaxed max-h-60 overflow-y-auto">
                {displayInput}
              </pre>
            </div>
          )}

          {/* Output section */}
          {result && (
            <div className={`px-3 py-2 ${status === "error" ? "bg-red-50" : "bg-emerald-50"} border-b border-zinc-200`}>
              <div className={`text-[10px] font-semibold uppercase tracking-wider mb-1 ${status === "error" ? "text-red-500" : "text-emerald-500"}`}>
                Output
              </div>
              <pre className={`whitespace-pre-wrap font-mono text-xs leading-relaxed max-h-64 overflow-y-auto ${status === "error" ? "text-red-700" : "text-zinc-800"}`}>{prettyPrintJson(result)}</pre>
            </div>
          )}

          {/* Raw stdout (if different from result — shows streaming partial) */}
          {stdout && stdout !== toolInput && stdout !== result && (
            <div className="px-3 py-2 bg-zinc-900">
              <div className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1">Raw stdout</div>
              <pre className="text-zinc-100 whitespace-pre-wrap font-mono text-xs leading-relaxed max-h-40 overflow-y-auto">
                {stdout}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
