/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" serves fixtures from src/api/fixtures instead of hitting the network. */
  readonly VITE_API_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
