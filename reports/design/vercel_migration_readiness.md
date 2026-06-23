# Vercel frontend-migration readiness (2026-06-23)

Verdict: **MINOR-WORK READY.** The GRACE-2 frontend is unusually migration-ready. No code
rewrite is required -- the work is config + env + one small file + a Cognito console change.
Full audit (5 agents): see the workflow output in the session task dir (wf_2894f894-206).

## Why it's ready
- **Pure static Vite SPA.** `web/package.json` build = `tsc --noEmit && vite build` -> `dist/`.
  No SSR, no server functions, no API routes in the build. Directly deployable as a Vercel
  static build (framework = Vite, root = `web/`, output = `dist`). No `base` set -> root-absolute
  asset paths (correct for a domain-root deploy; do NOT set a sub-path base).
- **No AWS SDK / Amplify anywhere.** Cognito is hand-rolled OIDC code+PKCE in `web/src/auth.ts`
  (POSTs to the Cognito Hosted-UI origin) -- no AWS-origin coupling in the bundle.
- **Backend URLs flow through ONE env seam** -- `web/src/lib/public_base.ts`
  (`VITE_GRACE2_PUBLIC_BASE` -> `wss://<base>/ws` + `https://<base>`; per-surface overrides
  `VITE_GRACE2_WS_URL` / `VITE_GRACE2_HTTP_URL`). The migration was effectively pre-designed.
- **Tile URLs are baked by the AGENT, not the web** (`publish_layer.py` emits the full TiTiler
  XYZ template from `GRACE2_TILE_SERVER_BASE`). The browser fetches whatever origin the agent
  persisted -- independent of the SPA host.
- **CORS already handled**: the agent returns `Access-Control-Allow-Origin: *` for `/api` + tiles;
  WebSocket is CORS-exempt. The separate API-Gateway endpoints (wake/case-view/case-list/export)
  already run cross-origin today.

## The 3 hard blockers (all config, no code)
1. **No `vercel.json` SPA fallback.** `EntryRouter.tsx` routes `/app` `/privacy` `/landing` by the
   server-requested pathname with full-page `<a href>` nav (no History API). Without a catch-all
   rewrite to `/index.html`, those deep links 404. CloudFront does this fallback today.
   FIX: add `web/vercel.json` rewrite, excluding the static asset dirs:
   `{"rewrites":[{"source":"/((?!assets/|landing/).*)","destination":"/index.html"}]}`.
2. **Backends stay on AWS; keep CloudFront as a backend-only edge.** `/ws /api /cog /tiles` are
   CloudFront-proxied origins (E2L74AS56MVZ87) -- it terminates TLS and the agent WS keepalive
   (`ws.ts` 25s) is tuned for CloudFront's idle cull. Keep the distro fronting those four origins;
   point the Vercel build env at it (`VITE_GRACE2_PUBLIC_BASE=d125yfbyjrpbre.cloudfront.net`,
   `VITE_GRACE2_WS_URL=wss://d125yfbyjrpbre.cloudfront.net/ws`). Only the S3-web DEFAULT behavior
   (+ the S3 web bucket) retires once Vercel serves the SPA. Rebuild on Vercel so the bundle
   carries the production env (the current `dist` has CloudFront baked in, which is fine here).
3. **Cognito callback/logout origin.** `VITE_COGNITO_REDIRECT_URI` is pinned to the CloudFront
   origin and Cognito only accepts pre-registered redirect URIs. FIX: whitelist the Vercel origin
   (callback + logout URLs) on the Cognito app client + set `VITE_COGNITO_REDIRECT_URI` to it.

## Recommended topology
Static SPA on Vercel (Hobby/free) -> all backends stay on AWS behind the EXISTING CloudFront edge
(no WS-proxy problem, TLS + keepalive already right) -> add `vercel.json` + whitelist the Vercel
origin in Cognito + an env-set rebuild. Effort: roughly an afternoon, mostly Vercel project config
+ Cognito console. Note Vercel Hobby is non-commercial-use -- flag if this is ever commercial.
