/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MIDDLEWARE_BASE_URL?: string;
  readonly VITE_MCP_BASE_URL?: string;
  readonly VITE_API_PREFIX?: string;
  readonly VITE_DEV_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
