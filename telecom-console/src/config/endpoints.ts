export const LOCAL = {
  mcpBase: "http://127.0.0.1:8080",
  mcp: "http://127.0.0.1:8080/mcp/",
  healthz: "http://127.0.0.1:8080/healthz",
  readyz: "http://127.0.0.1:8080/readyz",
  middlewareReadyz: "http://127.0.0.1:9000/readyz",
} as const;

/** Azure Container Apps staging — keep in sync with docs/REFERENCE.md */
export const STAGING = {
  mcpBase: "https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io",
  mcp: "https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/mcp/",
  healthz:
    "https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/healthz",
  readyz:
    "https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/readyz",
} as const;
