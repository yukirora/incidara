import type { SessionState, GatewayEvent } from "../api/sessions.ts";

interface InspectorProps {
  state: SessionState | null;
  events: GatewayEvent[];
}

export function Inspector({ state, events }: InspectorProps) {
  return (
    <div className="w-80 border-l border-zinc-200 overflow-y-auto bg-zinc-50 p-3 flex-shrink-0 flex flex-col gap-4">
      <div>
        <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
          Gateway State
        </h3>
        <pre className="text-xs text-zinc-700 whitespace-pre-wrap break-all bg-white border border-zinc-200 rounded p-2">
          {state ? JSON.stringify(state, null, 2) : "No state yet…"}
        </pre>
      </div>

      <div>
        <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
          Recent Events ({events.length})
        </h3>
        <div className="space-y-1 overflow-y-auto max-h-96">
          {[...events].reverse().slice(0, 20).map((e) => (
            <div
              key={e.seq}
              className="text-xs text-zinc-600 bg-white border border-zinc-200 rounded p-1.5 break-all"
            >
              <span className="font-mono text-zinc-400 mr-1">#{e.seq}</span>
              <span className="font-medium">{e.event_type}</span>
              {Object.keys(e.payload).length > 0 && (
                <pre className="mt-0.5 text-zinc-400 whitespace-pre-wrap text-[10px]">
                  {JSON.stringify(e.payload, null, 1)}
                </pre>
              )}
            </div>
          ))}
          {events.length === 0 && (
            <p className="text-xs text-zinc-400">No events yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}
