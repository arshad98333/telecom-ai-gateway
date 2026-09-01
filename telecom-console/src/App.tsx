import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { DashboardPage } from "@/pages/Dashboard";
import { MonitoringPage } from "@/pages/Monitoring";
import { AuditingPage } from "@/pages/Auditing";
import { ApprovalsPage } from "@/pages/Approvals";
import { AgentsPage } from "@/pages/Agents";
import { GuidePage } from "@/pages/Guide";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="guide" element={<GuidePage />} />
          <Route index element={<DashboardPage />} />
          <Route path="monitoring" element={<MonitoringPage />} />
          <Route path="auditing" element={<AuditingPage />} />
          <Route path="approvals" element={<ApprovalsPage />} />
          <Route path="agents" element={<AgentsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
