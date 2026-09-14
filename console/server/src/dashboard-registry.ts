/**
 * Central registry of all dashboard pages.
 * Single source of truth — all other code reads from here.
 *
 * To add a new dashboard:
 *   1. Add an entry here
 *   2. Add the route in App.tsx
 *   3. Add the sidebar link in Sidebar.tsx
 *   4. The admin Dashboards tab, auth response, and seed will pick it up automatically.
 */

export interface DashboardDef {
  /** Route key used in dashboard_access DB column and /api/auth/me response */
  id: string;
  /** Display name for sidebar and admin UI */
  label: string;
  /** Short description for admin UI */
  desc: string;
  /** Sidebar section: "cluster" or "agent" */
  section: "cluster" | "agent";
}

export const DASHBOARDS: DashboardDef[] = [
  { id: "availability", label: "Availability", desc: "Uptime, incident rates, and node health trends", section: "cluster" },
  { id: "reliability", label: "Reliability", desc: "Node MTBF, failure analysis, and RCA breakdown", section: "cluster" },
  { id: "jobs", label: "Job Metrics", desc: "Job duration, MTBI, and failure analysis", section: "cluster" },
  { id: "utilization", label: "Utilization", desc: "GPU allocation, usage efficiency, and per-VC breakdown", section: "cluster" },
  { id: "usage", label: "Usage & Cost", desc: "Token usage, cost per agent, and LTM quota", section: "agent" },
];

export const DASHBOARD_IDS: string[] = DASHBOARDS.map(d => d.id);
