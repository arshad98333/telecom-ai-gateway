import { useState } from "react";
import { fetchApprovals } from "@/api/client";
import { getStoredToken, setStoredToken } from "@/api/auth";
import { Panel } from "@/components/ui";

export function ApprovalsPage() {
  const [token, setToken] = useState(getStoredToken());
  const [rows, setRows] = useState<Awaited<ReturnType<typeof fetchApprovals>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    setStoredToken(token);
    try {
      setRows(await fetchApprovals(token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
      setRows(null);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-white">Approvals</h1>
        <p className="mt-2 text-slate-400">
          Supervisor queue from GET /api/v1/approvals. Seed data with make seed or make demo.
        </p>
      </div>

      <Panel title="Supervisor token">
        <textarea
          value={token}
          onChange={(e) => setToken(e.target.value)}
          rows={2}
          className="w-full rounded-xl border border-slate-700 bg-surface-950 p-3 font-mono text-xs"
        />
        <button
          type="button"
          onClick={() => void load()}
          className="mt-3 rounded-xl bg-sky-600 px-4 py-2 text-sm text-white hover:bg-sky-500"
        >
          Load queue
        </button>
        {error ? <p className="mt-2 text-sm text-rose-300">{error}</p> : null}
      </Panel>

      {rows ? (
        <Panel title="Pending requests">
          {rows.requests.length === 0 ? (
            <p className="text-slate-400">No pending approvals. Run seed to load demo data.</p>
          ) : (
            <ul className="space-y-2">
              {rows.requests.map((r) => (
                <li
                  key={r.request_id}
                  className="rounded-xl border border-slate-800 bg-surface-950 px-4 py-3 text-sm"
                >
                  <span className="font-mono text-sky-300">{r.request_id}</span>
                  <span className="mx-2 text-slate-600">|</span>
                  {r.cx_id} <span className="text-slate-500">({r.state})</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      ) : null}
    </div>
  );
}
