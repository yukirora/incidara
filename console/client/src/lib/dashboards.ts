export interface DashboardDef {
  id: string;
  label: string;
  desc: string;
  icon: string;
  path: string;
}

export const DASHBOARDS: DashboardDef[] = [
  { id: "availability", label: "Availability", desc: "Uptime, incident rates, and node health trends", icon: "📊", path: "/reports/availability" },
  { id: "reliability", label: "Reliability", desc: "Node MTBF, failure analysis, and RCA breakdown", icon: "🔧", path: "/reports/reliability" },
  { id: "jobs", label: "Job Metrics", desc: "Job duration, MTBI, and failure analysis", icon: "⚙️", path: "/reports/jobs" },
  { id: "utilization", label: "Utilization", desc: "GPU allocation, usage efficiency, and per-VC breakdown", icon: "📈", path: "/reports/utilization" },
  { id: "usage", label: "Usage & Cost", desc: "Token usage, cost per agent, and LTM quota", icon: "💰", path: "/reports/usage" },
];
