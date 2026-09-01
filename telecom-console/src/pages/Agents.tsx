import { useState } from "react";
import { motion } from "framer-motion";
import { listMcpTools } from "@/api/client";
import { getStoredToken, setStoredToken } from "@/api/auth";
import { CopyBlock, Panel } from "@/components/ui";
import { LOCAL, STAGING } from "@/config/endpoints";

const CURSOR_CONFIG = `{
  "mcpServers": {
    "telecom-mcp-tools": {
      "url": "${LOCAL.mcp}",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}`;

const CLAUDE_CONFIG = `{
  "mcpServers": {
    "telecom-mcp-tools": {
      "type": "http",
      "url": "${LOCAL.mcp}",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}`;

const STAGING_CONFIG = `{
  "mcpServers": {
    "telecom-mcp-staging": {
      "url": "${STAGING.mcp}"
    }
  }
}`;

export function AgentsPage() {
  const [token, setToken] = useState(getStoredToken());
  const [tools, setTools] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function testConnection() {
    setError(null);
    setStoredToken(token);
    try {
      const res = await listMcpTools(token);
      setTools(JSON.stringify(res, null, 2));
    } catch (e) {
      setError(e instanceof Error ? e.message : "connection failed");
      setTools(null);
    }
  }

  const cursorWithToken = CURSOR_CONFIG.replace("YOUR_TOKEN_HERE", token || "YOUR_TOKEN_HERE");
  const claudeWithToken = CLAUDE_CONFIG.replace("YOUR_TOKEN_HERE", token || "YOUR_TOKEN_HERE");

  return (
    <div className="space-y-8">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <h1 className="text-3xl font-bold text-white">AI agent connections</h1>
        <p className="mt-2 max-w-3xl leading-relaxed text-slate-400">
          Connect Cursor, Claude Desktop, or any MCP client over streamable HTTP. Mint a token with{" "}
          <code className="text-blue-300">.\scripts\dev.ps1 token</code> or use{" "}
          <code className="text-blue-300">client-demo</code> from the Guide.
        </p>
      </motion.div>

      <Panel title="Bearer token">
        <textarea
          value={token}
          onChange={(e) => setToken(e.target.value)}
          rows={2}
          className="w-full rounded-xl border border-slate-700 bg-surface-950 p-3 font-mono text-xs"
        />
        <button
          type="button"
          onClick={() => void testConnection()}
          className="mt-3 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
        >
          Test tools/list via MCP
        </button>
        {error ? <p className="mt-2 text-sm text-rose-300">{error}</p> : null}
        {tools ? (
          <pre className="mt-4 max-h-64 overflow-auto rounded-xl bg-surface-950 p-3 font-mono text-xs text-slate-400">
            {tools}
          </pre>
        ) : null}
      </Panel>

      <Panel title="Cursor (.cursor/mcp.json)">
        <CopyBlock label="Project or user MCP config" text={cursorWithToken} />
      </Panel>

      <Panel title="Staging Azure MCP">
        <p className="mb-3 text-sm text-slate-400">
          Health{" "}
          <a className="text-blue-400 hover:underline" href={STAGING.healthz}>
            /healthz
          </a>
          {" · "}
          <a className="text-blue-400 hover:underline" href={STAGING.readyz}>
            /readyz
          </a>
        </p>
        <CopyBlock label="cursor-mcp.staging.json" text={STAGING_CONFIG} />
      </Panel>

      <Panel title="Claude Desktop (claude_desktop_config.json)">
        <CopyBlock label="Claude MCP HTTP transport" text={claudeWithToken} />
      </Panel>

      <Panel title="End-to-end test from terminal">
        <CopyBlock
          text={`.\\scripts\\dev.ps1 client-demo

# Or manually:
.\\scripts\\dev.ps1 token
.\\scripts\\dev.ps1 client-tools`}
        />
      </Panel>
    </div>
  );
}
