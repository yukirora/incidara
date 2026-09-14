import { useNavigate } from "react-router-dom";
import type { AgentSummary } from "../api/agents.ts";

interface SummaryStripProps {
  agents: AgentSummary[];
}

function MetricCard({ value, label, icon, color, onClick }: {
  value: number | string;
  label: string;
  icon: React.ReactNode;
  color: string;
  onClick?: () => void;
}) {
  return (
    <div
      className={`relative bg-white rounded-xl border border-zinc-200/80 p-4 overflow-hidden group ${
        onClick ? "cursor-pointer hover:border-zinc-300 hover:shadow-sm transition-all" : ""
      }`}
      onClick={onClick}
    >
      <div className={`absolute top-0 left-0 w-1 h-full ${color}`} />
      <div className="flex items-center justify-between">
        <div>
          <p className="text-2xl font-bold text-zinc-900">{value}</p>
          <p className="text-xs text-zinc-500 mt-0.5">{label}</p>
        </div>
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center text-base ${color.replace('bg-', 'bg-').replace('-500', '-50')} ${color.replace('bg-', 'text-')}`}>
          {icon}
        </div>
      </div>
    </div>
  );
}

export function SummaryStrip({ agents }: SummaryStripProps) {
  const navigate = useNavigate();
  const totalAgents = agents.length;
  const activeRunning = agents.reduce((sum, a) => sum + a.counters.running, 0);
  const waitingInput = agents.reduce((sum, a) => sum + a.counters.waiting, 0);
  const completedToday = agents.reduce((sum, a) => sum + a.counters.completed_today, 0);

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <MetricCard
        value={totalAgents}
        label="Agents"
        icon="🤖"
        color="bg-blue-500"
      />
      <MetricCard
        value={activeRunning}
        label="Active"
        icon="⚡"
        color="bg-emerald-500"
        onClick={() => navigate("/tasks?status=running")}
      />
      <MetricCard
        value={waitingInput}
        label="Needs input"
        icon="💬"
        color="bg-amber-500"
        onClick={() => navigate("/tasks?status=waiting_input")}
      />
      <MetricCard
        value={completedToday}
        label="Done today"
        icon="✅"
        color="bg-violet-500"
        onClick={() => navigate("/tasks?status=completed")}
      />
    </div>
  );
}
