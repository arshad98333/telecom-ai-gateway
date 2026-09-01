import { useState } from "react";
import { motion } from "framer-motion";
import { fetchAudit } from "@/api/client";
import { getStoredToken, setStoredToken, type DevRole } from "@/api/auth";
import { Panel } from "@/components/ui";

const ROLES: { id: DevRole; label: string; note: string }[] = [
  { id: "admin_security", label: "Security admin", note: "Required for /audit" },
  { id: "supervisor_approver", label: "Supervisor", note: "Approvals queue" },
  { id: "customer", label: "Customer", note: "Account tools" },
];

export function AuditingPage() {
  const [token, setToken] = useState(getStoredToken());
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchAudit>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    setStoredToken(token);
    try {
      const result = await fetchAudit(token);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "audit failed");
      setData(null);
    }
  }

  return (
    <div className="space-y-8">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <h1 className="text-3xl font-bold text-white">Auditing</h1>
        <p className="mt-2 max-w-2xl text-slate-400">
          Hash-chained audit trail from the middleware API. Requires a token with
          audit:read (mint with role admin_security).
        </p>
      </motion.div>

      <Panel title="Access token">
        <textarea
          value={token}
          onChange={(e) => setToken(e.target.value)}
          rows={3}
          className="w-full rounded-xl border border-slate-700 bg-surface-950 p-3 font-mono text-xs text-slate-200"
          placeholder="Paste token from: uv run python scripts/mint_dev_token.py --role admin_security"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          {ROLES.map((r) => (
            <span
              key={r.id}
              className="rounded-lg bg-slate-800 px-2 py-1 text-xs text-slate-400"
              title={r.note}
            >
              {r.label}
            </span>
          ))}
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="mt-4 rounded-xl bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500"
        >
          Load audit trail
        </button>
        {error ? <p className="mt-3 text-sm text-rose-300">{error}</p> : null}
      </Panel>

      {data ? (
        <Panel
          title={`Records (${data.records.length})`}
          action={
            data.chain_broken_at != null ? (
              <span className="text-sm text-rose-400">Chain broken at seq {data.chain_broken_at}</span>
            ) : (
              <span className="text-sm text-emerald-400">Chain intact</span>
            )
          }
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr>
                  <th className="pb-2 pr-4">Seq</th>
                  <th className="pb-2 pr-4">Action</th>
                  <th className="pb-2 pr-4">Decision</th>
                  <th className="pb-2 pr-4">Role</th>
                  <th className="pb-2">Correlation</th>
                </tr>
              </thead>
              <tbody>
                {data.records.map((row) => (
                  <tr key={row.record_id} className="border-t border-slate-800 text-slate-300">
                    <td className="py-2 pr-4 font-mono">{row.seq}</td>
                    <td className="py-2 pr-4">{row.action}</td>
                    <td className="py-2 pr-4">{row.decision}</td>
                    <td className="py-2 pr-4">{row.actor_role}</td>
                    <td className="py-2 font-mono text-xs">{row.correlation_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
