import { Routes, Route } from "react-router-dom";
import { Login } from "./pages/Login.tsx";
import { Signup } from "./pages/Signup.tsx";
import { Protected } from "./components/Protected.tsx";
import { AppLayout } from "./components/AppLayout.tsx";
import { Home } from "./pages/Home.tsx";
import { Tasks } from "./pages/Tasks.tsx";
import { Schedules } from "./pages/Schedules.tsx";
import { AgentDetail } from "./pages/AgentDetail.tsx";
import { SessionWorkspace } from "./pages/SessionWorkspace.tsx";
import { Admin } from "./pages/Admin.tsx";
import { AdminUsage } from "./pages/AdminUsage.tsx";
import { ReportAvailability } from "./pages/ReportAvailability.tsx";
import ReportReliability from "./pages/ReportReliability.tsx";
import { JobMetrics } from "./pages/JobMetrics.tsx";
import { Utilization } from "./pages/Utilization.tsx";
import { AgentMetrics } from "./components/AgentMetrics.tsx";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route
        element={
          <Protected>
            <AppLayout />
          </Protected>
        }
      >
        <Route path="/" element={<Home />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/schedules" element={<Schedules />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/sessions/:id" element={<SessionWorkspace />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/admin/usage" element={<AdminUsage />} />
        <Route path="/reports/usage" element={<AdminUsage />} />
        <Route path="/reports/availability" element={<ReportAvailability />} />
        <Route path="/reports/reliability" element={<ReportReliability />} />
        <Route path="/reports/jobs" element={<JobMetrics />} />
        <Route path="/reports/utilization" element={<Utilization />} />
        <Route path="/reports/agent-metrics" element={<AgentMetrics />} />
      </Route>
    </Routes>
  );
}
