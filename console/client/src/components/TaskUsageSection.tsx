import { useState } from "react";
import { fetchTaskUsage, type TaskUsage, type TaskModelUsage } from "../api/usage";

interface TaskUsageSectionProps {
  taskId: number;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function formatCost(usd: number): string {
  if (usd >= 1) return "$" + usd.toFixed(2);
  if (usd >= 0.01) return "$" + usd.toFixed(3);
  return "$" + usd.toFixed(4);
}

export function TaskUsageSection({ taskId }: TaskUsageSectionProps) {
  const [expanded, setExpanded] = useState(false);
  const [usage, setUsage] = useState<TaskUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExpand = async () => {
    if (!expanded && !usage) {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchTaskUsage(taskId);
        setUsage(data);
      } catch (err: any) {
        setError(err.message || "Failed to load usage");
      } finally {
        setLoading(false);
      }
    }
    setExpanded(!expanded);
  };

  return (
    <div className="border-t border-zinc-100 mt-2">
      <button
        onClick={handleExpand}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs text-zinc-500 hover:text-zinc-700 hover:bg-zinc-50 transition-colors"
      >
        <span className="text-[10px]">{expanded ? "▼" : "▶"}</span>
        <span className="font-medium">Usage</span>
        {usage && (
          <span className="ml-auto text-zinc-400">
            {formatTokens(usage.total_tokens)} tokens · {formatCost(usage.total_cost_usd)}
          </span>
        )}
        {loading && <span className="ml-auto text-zinc-400 animate-pulse">Calculating…</span>}
        {!usage && !loading && (
          <span className="ml-auto text-zinc-400">Click to load</span>
        )}
      </button>

      {expanded && usage && (
        <div className="px-3 pb-3 space-y-3">
          {/* Token breakdown */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="bg-zinc-50 rounded px-2 py-1.5">
              <div className="text-zinc-400">Input</div>
              <div className="font-semibold">{formatTokens(usage.total_input_tokens)}</div>
            </div>
            <div className="bg-zinc-50 rounded px-2 py-1.5">
              <div className="text-zinc-400">Output</div>
              <div className="font-semibold">{formatTokens(usage.total_output_tokens)}</div>
            </div>
            <div className="bg-zinc-50 rounded px-2 py-1.5">
              <div className="text-zinc-400">Cache Write</div>
              <div className="font-semibold">{formatTokens(usage.total_cache_creation_input_tokens)}</div>
            </div>
            <div className="bg-zinc-50 rounded px-2 py-1.5">
              <div className="text-zinc-400">Cache Read</div>
              <div className="font-semibold">{formatTokens(usage.total_cache_read_input_tokens)}</div>
            </div>
          </div>

          {/* Cost & turns */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-zinc-500">{usage.turns} turns</span>
            <span className="font-semibold text-zinc-800">{formatCost(usage.total_cost_usd)}</span>
          </div>

          {/* Per-model table */}
          {usage.model_usage && usage.model_usage.length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-zinc-400">
                  <th className="text-left font-medium">Model</th>
                  <th className="text-right font-medium">Tokens</th>
                  <th className="text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {usage.model_usage.map((m: TaskModelUsage) => (
                  <tr key={m.model}>
                    <td className="py-0.5">
                      <span className="bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded font-medium">
                        {m.model}
                      </span>
                    </td>
                    <td className="text-right text-zinc-600">
                      {formatTokens(m.input_tokens + m.output_tokens + m.cache_creation_input_tokens + m.cache_read_input_tokens)}
                    </td>
                    <td className="text-right font-medium">{formatCost(m.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* Sub-agent note */}
          {usage.turns > 5 && (
            <p className="text-[10px] text-zinc-400">Includes sub-agent usage</p>
          )}
        </div>
      )}

      {expanded && error && (
        <div className="px-3 pb-2 text-xs text-red-500">{error}</div>
      )}
    </div>
  );
}
