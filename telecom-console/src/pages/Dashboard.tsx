import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { fetchMcpHealth, fetchMiddlewareHealth } from "@/api/client";
import { Panel, StatCard, StatusDot } from "@/components/ui";

export function DashboardPage() {
  const [mw, setMw] = useState<string>("checking");
  const [mcp, setMcp] = useState<string>("checking");

  useEffect(() => {
    fetchMiddlewareHealth()
      .then((h) => setMw(h.status))
      .catch(() => setMw("unreachable"));
    fetchMcpHealth()
      .then((h) => setMcp(h.status))
      .catch(() => setMcp("unreachable"));
  }, []);

  return (
    <div className="space-y-8">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <h1 className="text-3xl font-bold text-white">Operations overview</h1>
        <p className="mt-2 max-w-2xl text-slate-400">
          Monitor service health, review audit chains, approve refunds, and connect AI
          agents to the MCP tool server.
        </p>
      </motion.div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Middleware API" value={mw} hint=":9000 /readyz" tone={mw === "healthy" ? "success" : "danger"} delay={0} />
        <StatCard label="MCP tool server" value={mcp} hint=":8080 /readyz" tone={mcp === "healthy" ? "success" : "danger"} delay={0.05} />
        <StatCard label="Auth mode" value="local" hint="No Auth0 required for dev" delay={0.1} />
        <StatCard label="MCP transport" value="HTTP" hint="POST /mcp/ streamable" delay={0.15} />
      </div>

      <Panel title="Quick start">
        <ol className="list-decimal space-y-2 pl-5 text-sm text-slate-300">
          <li>Run <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-sky-300">make run-mcp</code> in one terminal.</li>
          <li>Run <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-sky-300">make run-middleware</code> for audit and approvals (optional with fake backend).</li>
          <li>Run <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-sky-300">make console-dev</code> for this UI.</li>
          <li>Open <strong>AI Agents</strong> to copy MCP config for Cursor or Claude.</li>
        </ol>
      </Panel>

      <Panel title="Service status">
        <div className="space-y-3">
          <div className="flex items-center gap-3 text-sm">
            <StatusDot ok={mw === "healthy"} />
            <span>telecom-middleware</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <StatusDot ok={mcp === "healthy"} />
            <span>telecom-mcp</span>
          </div>
        </div>
      </Panel>
    </div>
  );
}
