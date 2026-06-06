/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GRACE2_WS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
