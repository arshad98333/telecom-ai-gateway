const STORAGE_KEY = "telecom_console_token";
const ROLE_KEY = "telecom_console_role";

export type DevRole =
  | "customer"
  | "support_agent"
  | "supervisor_approver"
  | "admin_security";

export function getStoredToken(): string {
  return localStorage.getItem(STORAGE_KEY) ?? import.meta.env.VITE_DEV_TOKEN ?? "";
}

export function setStoredToken(token: string): void {
  localStorage.setItem(STORAGE_KEY, token);
}

export function getStoredRole(): DevRole {
  return (localStorage.getItem(ROLE_KEY) as DevRole) ?? "supervisor_approver";
}

export function setStoredRole(role: DevRole): void {
  localStorage.setItem(ROLE_KEY, role);
}

export async function mintDevToken(role: DevRole): Promise<string> {
  const res = await fetch(`/api/dev-token?role=${role}`);
  if (!res.ok) {
    throw new Error(
      "Could not mint a token. Start the console dev server or paste a token from: make token",
    );
  }
  const data = (await res.json()) as { token: string };
  return data.token;
}
