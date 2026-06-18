/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GRACE2_WS_URL?: string;
  readonly VITE_GRACE2_HTTP_URL?: string;
  // sprint-14-aws CloudFront/HTTPS: a single public origin (e.g.
  // "https://d123.cloudfront.net"). When set, the web derives wss://<domain>/ws
  // for the agent socket and https://<domain> for the HTTP base (catalog).
  // When unset, every URL derivation is byte-identical to today.
  readonly VITE_GRACE2_PUBLIC_BASE?: string;
  // auto-stop/wake infra (NATE 2026-06-17): the API-Gateway HTTP endpoint that
  // fronts the StartInstances "wake" Lambda (infra/aws-autostop). When the
  // always-on agent box is STOPPED by the idle-check Lambda it answers neither
  // the WebSocket nor any HTTP endpoint; the web POSTs here to ask the wake
  // Lambda to start it. Precedence: VITE_GRACE2_WAKE_URL > VITE_GRACE2_PUBLIC_BASE(/wake)
  // > null (wake disabled — dev/LAN, where the box is never auto-stopped).
  readonly VITE_GRACE2_WAKE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
