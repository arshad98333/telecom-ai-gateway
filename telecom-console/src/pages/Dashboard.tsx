import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { fetchMcpHealth, fetchMiddlewareHealth } from "@/api/client";
import { Panel, StatCard, StatusDot } from "@/components/ui";
import { LOCAL, STAGING } from "@/config/endpoints";

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
          Perito ops: health, audit, refunds, MCP. Local and staging URLs are listed below.
        </p>
      </motion.div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Middleware API"
          value={mw === "unreachable" ? "not started" : mw}
          hint={
            mw === "unreachable"
              ? "Optional — .\\scripts\\dev.ps1 run-middleware"
              : ":9000 /readyz"
          }
          tone={mw === "healthy" ? "success" : mw === "unreachable" ? "warning" : "danger"}
          delay={0}
        />
        <StatCard label="MCP tool server" value={mcp} hint=":8080 /readyz" tone={mcp === "healthy" ? "success" : "danger"} delay={0.05} />
        <StatCard label="Auth mode" value="local" hint="No Auth0 required for dev" delay={0.1} />
        <StatCard label="MCP transport" value="HTTP" hint="POST /mcp/ streamable" delay={0.15} />
      </div>

      <Panel
        title="Quick start"
        action={
          <Link
            to="/guide"
            className="rounded-lg bg-blue-600/20 px-3 py-1.5 text-xs font-semibold text-blue-300 ring-1 ring-blue-500/30 hover:bg-blue-600/30"
          >
            Full guide →
          </Link>
        }
      >
        <ol className="list-decimal space-y-2 pl-5 text-sm leading-relaxed text-slate-300">
          <li>
            <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[13px] text-blue-300">
              .\scripts\dev.ps1 run-mcp
            </code>{" "}
            — tool server on :8080
          </li>
          <li>
            <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[13px] text-blue-300">
              .\scripts\dev.ps1 run-middleware
            </code>{" "}
            — API on :9000 (needed for Auditing & Approvals pages)
          </li>
          <li>
            <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[13px] text-blue-300">
              .\scripts\dev.ps1 console-dev
            </code>{" "}
            — this UI on :5173
          </li>
          <li>
            <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[13px] text-blue-300">
              .\scripts\dev.ps1 client-demo
            </code>{" "}
            — mint token and call a demo tool
          </li>
        </ol>
      </Panel>

      <Panel title="Reference — health and MCP">
        <ul className="space-y-2 text-sm text-slate-300">
          <li>
            Local ready:{" "}
            <a className="text-blue-400 hover:underline" href={LOCAL.readyz}>
              {LOCAL.readyz}
            </a>
          </li>
          <li>
            Local MCP:{" "}
            <code className="text-blue-300">{LOCAL.mcp}</code>
          </li>
          <li>
            Staging health:{" "}
            <a className="text-blue-400 hover:underline" href={STAGING.healthz}>
              {STAGING.healthz}
            </a>
          </li>
          <li>
            Staging ready:{" "}
            <a className="text-blue-400 hover:underline" href={STAGING.readyz}>
              {STAGING.readyz}
            </a>
          </li>
          <li>
            Staging MCP:{" "}
            <a className="text-blue-400 hover:underline" href={STAGING.mcp}>
              {STAGING.mcp}
            </a>
          </li>
        </ul>
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
