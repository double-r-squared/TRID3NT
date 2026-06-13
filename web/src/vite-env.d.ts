/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GRACE2_WS_URL?: string;
  readonly VITE_GRACE2_HTTP_URL?: string;
  // sprint-14-aws CloudFront/HTTPS: a single public origin (e.g.
  // "https://d123.cloudfront.net"). When set, the web derives wss://<domain>/ws
  // for the agent socket and https://<domain> for the HTTP base (catalog).
  // When unset, every URL derivation is byte-identical to today.
  readonly VITE_GRACE2_PUBLIC_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
