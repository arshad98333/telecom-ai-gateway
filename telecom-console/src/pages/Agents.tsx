import { useState } from "react";
import { motion } from "framer-motion";
import { Check, Copy } from "lucide-react";
import { listMcpTools } from "@/api/client";
import { getStoredToken, setStoredToken } from "@/api/auth";
import { Panel } from "@/components/ui";

const CURSOR_CONFIG = `{
  "mcpServers": {
    "telecom-mcp-tools": {
      "url": "http://127.0.0.1:8080/mcp/",
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
      "url": "http://127.0.0.1:8080/mcp/",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}`;

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm font-medium text-slate-300">{label}</p>
        <button
          type="button"
          onClick={async () => {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          }}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-sky-400 hover:bg-slate-800"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto rounded-xl bg-surface-950 p-4 font-mono text-xs text-slate-300">
        {text}
      </pre>
    </div>
  );
}

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
        <p className="mt-2 max-w-3xl text-slate-400">
          Connect Cursor, Claude Desktop, or any MCP client over streamable HTTP. The tool
          server listens on POST /mcp/ with a Bearer token. Mint a token with make token.
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
          className="mt-3 rounded-xl bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-500"
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

      <Panel title="Claude Desktop (claude_desktop_config.json)">
        <CopyBlock label="Claude MCP HTTP transport" text={claudeWithToken} />
      </Panel>

      <Panel title="End-to-end test from terminal">
        <pre className="rounded-xl bg-surface-950 p-4 font-mono text-xs text-slate-300">{`cd telecom-mcp-client
$env:TELECOM_MCP_URL = "http://127.0.0.1:8080"
$env:TELECOM_MCP_ACCESS_TOKEN = "<paste token>"
uv run telecom-mcp-client list-tools`}</pre>
      </Panel>
    </div>
  );
}
