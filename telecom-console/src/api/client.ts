export type HealthReport = {
  status: string;
  version?: string;
  components?: Array<{ name: string; status: string; detail?: string }>;
};

export type KpiReport = {
  version?: string;
  environment?: string;
  indicators?: Record<string, unknown>;
  objectives?: Array<{ name: string; met: boolean; target?: string }>;
  breached?: Array<{ name: string; met: boolean }>;
};

export type AuditRecord = {
  seq: number;
  record_id: string;
  at: string;
  correlation_id: string;
  actor_role: string;
  cx_ref: string | null;
  action: string;
  resource: string;
  decision: string;
  outcome: string;
  failure_reason: string | null;
  entry_hash: string;
};

export type AuditResponse = {
  records: AuditRecord[];
  chain_broken_at: number | null;
};

export type Approval = {
  request_id: string;
  cx_id: string;
  state: string;
  amount_minor?: number;
  currency?: string;
  submitted_at?: string;
  money_moved?: boolean;
};

export type ApprovalsResponse = {
  requests: Approval[];
};

const API_PREFIX = import.meta.env.VITE_API_PREFIX ?? "/api/v1";

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
  };
}

export async function fetchMiddlewareHealth(): Promise<HealthReport> {
  const base = import.meta.env.VITE_MIDDLEWARE_BASE_URL ?? "";
  const url = base ? `${base}/readyz` : "/middleware/readyz";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`middleware readyz ${res.status}`);
  return res.json();
}

export async function fetchMcpHealth(): Promise<HealthReport> {
  const base = import.meta.env.VITE_MCP_BASE_URL ?? "/ops";
  const path = base.startsWith("http") ? `${base}/readyz` : "/ops/readyz";
  const res = await fetch(path);
  if (!res.ok) throw new Error(`mcp readyz ${res.status}`);
  return res.json();
}

export async function fetchMcpKpi(): Promise<KpiReport> {
  const res = await fetch("/ops/kpi");
  if (!res.ok) throw new Error(`kpi ${res.status}`);
  return res.json();
}

export async function fetchAudit(token: string, limit = 50): Promise<AuditResponse> {
  const res = await fetch(`${API_PREFIX}/audit?limit=${limit}`, {
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`audit ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

export async function fetchApprovals(token: string): Promise<ApprovalsResponse> {
  const res = await fetch(`${API_PREFIX}/approvals`, {
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`approvals ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

export async function listMcpTools(token: string): Promise<unknown> {
  const initBody = {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "telecom-console", version: "0.1.0" },
    },
  };
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };
  const initRes = await fetch("/mcp/", { method: "POST", headers, body: JSON.stringify(initBody) });
  if (!initRes.ok) throw new Error(`mcp initialize ${initRes.status}`);
  const listRes = await fetch("/mcp/", {
    method: "POST",
    headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }),
  });
  if (!listRes.ok) throw new Error(`mcp tools/list ${listRes.status}`);
  return listRes.json();
}
