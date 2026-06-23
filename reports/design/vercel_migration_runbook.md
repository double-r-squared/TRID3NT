# Vercel frontend migration runbook (scaffolded 2026-06-23)

Static SPA -> Vercel (Hobby/free); all backends STAY on AWS behind the existing CloudFront edge.
No code changes -- this is Vercel project config + Cognito console + DNS. Readiness analysis:
reports/design/vercel_migration_readiness.md. Scaffold landed: `web/vercel.json`.

## 1. Vercel project
- Import the GitHub repo `double-r-squared/GRACE-2`.
- **Root Directory = `web/`** (the SPA lives there; `web/vercel.json` is picked up automatically).
- Framework preset = **Vite** (build `npm run build`, output `dist`) -- already pinned in vercel.json.
- `web/vercel.json` provides the SPA-fallback rewrite (all non-asset paths -> `/index.html`, so
  `/app` `/privacy` `/landing` deep links work) + immutable cache on `/assets/*`.

## 2. Vercel Environment Variables (Production scope)
Set these in the Vercel dashboard (Project -> Settings -> Environment Variables). All are PUBLIC
(they ship in the browser bundle today) -- no secrets. Copied from `web/.env.production.local`,
with ONLY the Cognito redirect changed to the Vercel origin:

```
VITE_GRACE2_PUBLIC_BASE   = d125yfbyjrpbre.cloudfront.net
VITE_GRACE2_WS_URL        = wss://d125yfbyjrpbre.cloudfront.net/ws
VITE_GRACE2_WAKE_URL      = https://9ib093sis6.execute-api.us-west-2.amazonaws.com/wake
VITE_GRACE2_CASE_VIEW_URL = https://9ib093sis6.execute-api.us-west-2.amazonaws.com/case-view-url
VITE_GRACE2_CASE_LIST_URL = https://9ib093sis6.execute-api.us-west-2.amazonaws.com/case-list
VITE_GRACE2_CASE_EXPORT_URL = https://9ib093sis6.execute-api.us-west-2.amazonaws.com/case-export-url
VITE_COGNITO_USER_POOL_ID = us-west-2_mIpKrr727
VITE_COGNITO_CLIENT_ID    = 43ovkrtt97oh6gsnl006aecera
VITE_COGNITO_DOMAIN       = grace2-auth.auth.us-west-2.amazoncognito.com
VITE_COGNITO_REGION       = us-west-2
VITE_COGNITO_REDIRECT_URI = https://<your-vercel-domain>/      <-- CHANGE to the Vercel origin
```
Keeping `VITE_GRACE2_PUBLIC_BASE` = the CloudFront domain means `/ws /api /cog /tiles` keep flowing
through CloudFront (TLS terminated, 25s WS keepalive already tuned for its idle cull). The agent
returns `Access-Control-Allow-Origin: *` for `/api`+tiles and WS is CORS-exempt, so cross-origin
from the Vercel host works without server changes.

## 3. Cognito console (one change, else sign-in 400s)
User pool `us-west-2_mIpKrr727`, app client `43ovkrtt97oh6gsnl006aecera`:
- Add `https://<your-vercel-domain>/` to **Allowed callback URLs**.
- Add `https://<your-vercel-domain>/` (or `/landing`) to **Allowed sign-out URLs**.
- (Leave the existing CloudFront entries so the AWS-hosted SPA keeps working during cutover.)

## 4. Custom domain (optional)
Point the domain's DNS at Vercel; then update `VITE_COGNITO_REDIRECT_URI` + the Cognito callback/
sign-out URLs to the custom domain instead of `*.vercel.app`.

## 5. Verify after deploy
- `/`, `/app`, `/privacy`, `/landing` all load (SPA fallback working -- no 404s).
- Sign-in round-trips through Cognito Hosted UI and returns to the app.
- A layer renders on the map (tiles fetched via the CloudFront `/cog` origin).
- The agent WebSocket connects (wake the box; watch the connecting -> connected state).

## 6. What retires vs stays
- RETIRE (after cutover): the S3 web bucket `grace2-hazard-web-226996537797` + the CloudFront
  distro's DEFAULT (S3-web) behavior. Keep everything else.
- KEEP: CloudFront `E2L74AS56MVZ87` as a BACKEND-ONLY edge for `/ws /api /cog /tiles`; the agent
  box, Batch solvers, TiTiler, DynamoDB, Cognito, the API-Gateway endpoints -- all unchanged.

## 7. Rollback
S3 + CloudFront keep serving the SPA until DNS is cut to Vercel -- so rollback is just pointing the
domain back (or using the CloudFront URL). Zero-risk cutover.

## Caveat
Vercel Hobby (free) tier is non-commercial-use. If GRACE-2 becomes commercial, move to a paid tier
or back to S3+CloudFront for hosting.
