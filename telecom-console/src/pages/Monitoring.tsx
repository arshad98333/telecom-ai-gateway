import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { fetchMcpHealth, fetchMcpKpi, fetchMiddlewareHealth } from "@/api/client";
import { Panel, StatCard } from "@/components/ui";

export function MonitoringPage() {
  const [kpi, setKpi] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [mw, mcp, report] = await Promise.all([
        fetchMiddlewareHealth(),
        fetchMcpHealth(),
        fetchMcpKpi(),
      ]);
      setKpi({
        middleware: mw,
        mcp: mcp,
        ...report,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 15000);
    return () => clearInterval(id);
  }, [load]);

  const breached = (kpi?.breached as Array<{ name: string }> | undefined) ?? [];

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">Monitoring</h1>
          <p className="mt-2 text-slate-400">Live health and KPI/SLO signals from both services.</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="flex items-center gap-2 rounded-xl border border-slate-600 bg-slate-800/80 px-4 py-2 text-sm text-white hover:bg-slate-700"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {error ? (
        <p className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-rose-200">{error}</p>
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          label="SLO breaches"
          value={breached.length}
          tone={breached.length === 0 ? "success" : "warning"}
        />
        <StatCard label="Environment" value={String(kpi?.environment ?? "local")} />
        <StatCard label="MCP version" value={String(kpi?.version ?? "-")} />
      </div>

      <Panel title="KPI report (from /kpi)">
        <pre className="max-h-96 overflow-auto rounded-xl bg-surface-950 p-4 font-mono text-xs text-slate-300">
          {JSON.stringify(kpi, null, 2)}
        </pre>
      </Panel>
    </div>
  );
}
