import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { AlertTriangle, BookOpen, Terminal, Zap } from "lucide-react";
import { fetchMcpHealth, fetchMiddlewareHealth } from "@/api/client";
import { CopyBlock, Panel, StatusDot, StepCard } from "@/components/ui";
import { LOCAL, STAGING } from "@/config/endpoints";

const steps = [
  {
    title: "Install prerequisites (one time)",
    body: (
      <>
        <p className="mb-3">You need these tools on your PATH. Install any that are missing:</p>
        <ul className="mb-3 list-disc space-y-1 pl-5 text-slate-400">
          <li>
            <strong className="text-slate-200">Python 3.12+</strong> —{" "}
            <a className="text-blue-400 hover:underline" href="https://python.org/downloads">
              python.org/downloads
            </a>
          </li>
          <li>
            <strong className="text-slate-200">uv</strong> (Python package manager) —{" "}
            <code className="rounded bg-slate-800 px-1 py-0.5 text-xs">pip install uv</code>
          </li>
          <li>
            <strong className="text-slate-200">Node.js 20+</strong> (for this console) —{" "}
            <a className="text-blue-400 hover:underline" href="https://nodejs.org">
              nodejs.org
            </a>
          </li>
        </ul>
        <CopyBlock
          label="Verify tools"
          text={`python --version
uv --version
node --version
npm --version`}
        />
      </>
    ),
  },
  {
    title: "First-time project setup",
    body: (
      <>
        <p className="mb-3">
          Open a <strong>new</strong> PowerShell window at the repo root. Do not activate any
          virtual environment — <code className="text-blue-300">uv run</code> handles that for you.
        </p>
        <CopyBlock
          label="Windows (recommended)"
          text={`# Open PowerShell in the repo root (folder that contains scripts/)
.\\scripts\\dev.ps1 setup`}
        />
        <p className="mt-3 text-slate-400">
          Linux/macOS with Make: <code className="text-blue-300">make setup</code>
        </p>
      </>
    ),
  },
  {
    title: "Start the backend (two terminals)",
    body: (
      <>
        <p className="mb-3">
          Run each command in its <strong>own</strong> terminal. Leave them running.
        </p>
        <CopyBlock
          label="Terminal 1 — Middleware API (:9000)"
          text={`.\\scripts\\dev.ps1 run-middleware`}
        />
        <div className="mt-3" />
        <CopyBlock
          label="Terminal 2 — MCP tool server (:8080)"
          text={`.\\scripts\\dev.ps1 run-mcp`}
        />
        <p className="mt-3 text-slate-400">
          Optional: seed demo data if you use the real middleware store instead of the fake
          backend —{" "}
          <code className="text-blue-300">
            cd telecom-middleware; uv run --env-file .env telecom-middleware seed
          </code>
        </p>
      </>
    ),
  },
  {
    title: "Start this console (third terminal)",
    body: (
      <>
        <CopyBlock
          label="Terminal 3 — React dev server (:5173)"
          text={`.\\scripts\\dev.ps1 console-dev`}
        />
        <p className="mt-3">
          Open{" "}
          <a className="font-medium text-blue-400 hover:underline" href="http://localhost:5173">
            http://localhost:5173
          </a>{" "}
          in your browser. The Dashboard should show both services as <em>healthy</em>.
        </p>
      </>
    ),
  },
  {
    title: "Mint a token and call a tool (one command)",
    body: (
      <>
        <p className="mb-3">
          Avoid pasting multi-line scripts into a broken terminal. Use the built-in helper — it
          mints a token, sets env vars, lists tools, and calls a demo account in one shot:
        </p>
        <CopyBlock
          label="All-in-one demo (fourth terminal)"
          text={`.\\scripts\\dev.ps1 client-demo`}
        />
        <p className="mt-3 text-slate-400">
          Or step by step: <code className="text-blue-300">.\\scripts\\dev.ps1 token</code> then{" "}
          <code className="text-blue-300">.\\scripts\\dev.ps1 client-tools</code>
        </p>
      </>
    ),
  },
  {
    title: "Connect Cursor or Claude",
    body: (
      <>
        <p className="mb-3">
          Go to the{" "}
          <Link to="/agents" className="font-medium text-blue-400 hover:underline">
            AI Agents
          </Link>{" "}
          page, paste your bearer token, and copy the MCP config into Cursor (
          <code className="text-blue-300">.cursor/mcp.json</code>) or Claude Desktop. Templates
          also live in the <code className="text-blue-300">mcp/</code> folder at the repo root.
        </p>
        <p className="text-slate-400">
          After saving the config, restart Cursor/Claude so the MCP server is picked up.
        </p>
      </>
    ),
  },
];

export function GuidePage() {
  const [mw, setMw] = useState<"checking" | "healthy" | "down">("checking");
  const [mcp, setMcp] = useState<"checking" | "healthy" | "down">("checking");

  useEffect(() => {
    fetchMiddlewareHealth()
      .then((h) => setMw(h.status === "healthy" ? "healthy" : "down"))
      .catch(() => setMw("down"));
    fetchMcpHealth()
      .then((h) => setMcp(h.status === "healthy" ? "healthy" : "down"))
      .catch(() => setMcp("down"));
  }, []);

  const allHealthy = mw === "healthy" && mcp === "healthy";

  return (
    <div className="mx-auto max-w-3xl space-y-8 pb-12">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <div className="mb-2 flex items-center gap-2 text-blue-400">
          <BookOpen className="h-5 w-5" />
          <span className="text-sm font-semibold uppercase tracking-wide">Developer guide</span>
        </div>
        <h1 className="text-3xl font-bold text-white">Run everything locally</h1>
        <p className="mt-3 text-base leading-relaxed text-slate-400">
          Step-by-step setup for Windows and VS Code. No Auth0, no Docker required for your first
          run — just Python, Node, and three terminals.
        </p>
      </motion.div>

      <Panel title="Health and MCP (bookmark these)">
        <ul className="space-y-2 text-sm text-slate-300">
          <li>
            Local{" "}
            <a className="text-blue-400 hover:underline" href={LOCAL.healthz}>
              /healthz
            </a>
            {" · "}
            <a className="text-blue-400 hover:underline" href={LOCAL.readyz}>
              /readyz
            </a>
            {" · "}
            <code className="text-blue-300">POST {LOCAL.mcp}</code>
          </li>
          <li>
            Staging{" "}
            <a className="text-blue-400 hover:underline" href={STAGING.healthz}>
              /healthz
            </a>
            {" · "}
            <a className="text-blue-400 hover:underline" href={STAGING.readyz}>
              /readyz
            </a>
            {" · "}
            <a className="text-blue-400 hover:underline" href={STAGING.mcp}>
              /mcp/
            </a>
          </li>
        </ul>
      </Panel>

      <Panel
        title="Live checklist"
        action={
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              allHealthy
                ? "bg-emerald-500/15 text-emerald-300"
                : "bg-amber-500/15 text-amber-300"
            }`}
          >
            {allHealthy ? "Ready to develop" : "Start the services below"}
          </span>
        }
      >
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-3">
            <StatusDot ok={mw === "healthy"} />
            <span>Middleware API — :9000</span>
            <span className="text-slate-500">{mw === "checking" ? "checking…" : mw}</span>
          </div>
          <div className="flex items-center gap-3">
            <StatusDot ok={mcp === "healthy"} />
            <span>MCP tool server — :8080</span>
            <span className="text-slate-500">{mcp === "checking" ? "checking…" : mcp}</span>
          </div>
        </div>
      </Panel>

      <div className="space-y-4">
        {steps.map((s, i) => (
          <StepCard
            key={s.title}
            step={i + 1}
            title={s.title}
            done={
              i < 3
                ? i === 0
                  ? true
                  : i === 1
                    ? true
                    : mw === "healthy" && mcp === "healthy"
                : i === 3
                  ? typeof window !== "undefined"
                  : false
            }
          >
            {s.body}
          </StepCard>
        ))}
      </div>

      <Panel title="PowerShell paste errors (PSReadLine)">
        <div className="flex gap-3 text-sm text-slate-300">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
          <div className="space-y-3">
            <p>
              If you see <code className="text-rose-300">ArgumentOutOfRangeException</code> with{" "}
              <code className="text-rose-300">top Actual value was -1</code>, that is a{" "}
              <strong>PowerShell display bug</strong>, not your app failing. It often happens when
              pasting long commands into the integrated terminal.
            </p>
            <p className="font-medium text-white">Fix it in 30 seconds:</p>
            <ol className="list-decimal space-y-2 pl-5 text-slate-400">
              <li>
                Open a <strong>fresh</strong> terminal tab (Ctrl+Shift+`) — do not reuse a broken
                one.
              </li>
              <li>
                Run <code className="text-blue-300">deactivate</code> if you see{" "}
                <code className="text-blue-300">(telecom-mcp-tools)</code> in the prompt.
              </li>
              <li>
                Prefer one-liners from this guide, e.g.{" "}
                <code className="text-blue-300">.\\scripts\\dev.ps1 client-demo</code> — no manual
                env vars.
              </li>
              <li>
                Or use Windows Terminal / cmd.exe instead of the VS Code panel for paste-heavy
                work.
              </li>
            </ol>
            <CopyBlock
              label="Clear stuck ports (if restart fails)"
              text={`foreach ($port in 8080, 9000, 5173) {
  Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}
Write-Host "Ports cleared."`}
            />
          </div>
        </div>
      </Panel>

      <Panel title="Cursor hooks (elite DX)">
        <div className="flex gap-3 text-sm text-slate-300">
          <Zap className="mt-0.5 h-5 w-5 shrink-0 text-blue-400" />
          <div className="space-y-2">
            <p>
              This repo ships <code className="text-blue-300">.cursor/hooks.json</code> so Cursor
              agents get local-dev context on session start and a gentle warning if you try to
              manually activate a Python venv (which breaks <code className="text-blue-300">uv run</code>
              ).
            </p>
            <p className="text-slate-400">
              Hooks reload when you save <code className="text-blue-300">hooks.json</code>. Check
              the Hooks output channel in Cursor if they do not fire.
            </p>
          </div>
        </div>
      </Panel>

      <Panel title="Quick reference">
        <div className="grid gap-3 sm:grid-cols-2">
          {[
            { cmd: ".\\scripts\\dev.ps1 setup", desc: "First-time install" },
            { cmd: ".\\scripts\\dev.ps1 health", desc: "Probe both services" },
            { cmd: ".\\scripts\\dev.ps1 token", desc: "Print bearer token" },
            { cmd: ".\\scripts\\dev.ps1 client-demo", desc: "Token + list + demo call" },
            { cmd: ".\\scripts\\dev.ps1 clear-ports", desc: "Free 8080/9000/5173" },
            { cmd: ".\\scripts\\dev.ps1 console-dev", desc: "This UI on :5173" },
          ].map((row) => (
            <div
              key={row.cmd}
              className="rounded-xl border border-slate-800 bg-surface-950/80 p-3"
            >
              <div className="flex items-start gap-2">
                <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
                <div>
                  <code className="text-xs text-blue-300">{row.cmd}</code>
                  <p className="mt-1 text-xs text-slate-500">{row.desc}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
