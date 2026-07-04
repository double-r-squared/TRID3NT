# Edge and Web

---

## CloudFront distribution

**Distribution ID:** `E2L74AS56MVZ87`
**Public hostname:** `d125yfbyjrpbre.cloudfront.net`

### Behavior / origin routing

| Path pattern | Origin | Notes |
|---|---|---|
| `/ws*` | ALB `grace2-agent-broker` (or `origin-broker-ws`) | WebSocket upgrade; idle timeout 4000 s (longer than API GW's 2 h cap) |
| `/cog*` `/tiles*` | TiTiler EC2 `t3.small` :8080 | COG range requests, GDAL /vsis3 |
| `/api/*` | Agent Fargate :8766 (HTTP catalog) | Tool catalog endpoint; routed via CF |
| `/*` (default) | S3 web bucket `grace2-hazard-web-226996537797` | SPA static assets |

!!! note "Dead origins"
    `origin-agent-ws` and `origin-catalog` in the CloudFront config still point to the stopped
    legacy EC2 box (`54.185.114.233`). These origins are superseded but not yet removed.
    `/ws*` was cut over to `origin-broker-ws` (the ALB) as of 2026-06-29.

---

## Vercel SPA

The React + MapLibre GL JS frontend is hosted on **Vercel**. It auto-deploys on every push to
`origin/main` via `web/vercel.json` (Vite build). There is no manual `aws s3 sync` step for the
frontend -- pushing to `origin/main` is the deploy.

**Env vars** for the frontend are managed in the Vercel dashboard, not in the repo.

---

## S3 web bucket

**Bucket:** `grace2-hazard-web-226996537797`

This bucket serves:

- **Cold tool catalog:** `catalog/tool-catalog.json` -- published by `scripts/deploy_agent_bundle.sh`
  with `public-read` ACL and `Cache-Control: max-age=300`. The web client reads this first (cold
  path) so tool descriptions are available without waking the agent.
- **Static assets:** bundled by the Vercel build (SPA files).

The bucket has **Block Public Access OFF** for the tool catalog; the runs bucket has BPA-on (signer
only).

---

## Serverless cold API

**API Gateway ID:** `9ib093sis6`

Cold-path Lambdas (all called with a 10 s `AbortController` timeout from the client):

| Lambda | Path | Purpose |
|---|---|---|
| case-list | `/case-list` | Scan `trid3nt_cases` by `user_id` (JWT sub) |
| case-view-url | `/case-view-url?case_id=...` | Presign `case-views/<case_id>/snapshot.json` |
| case-export-url | `/case-export-url` | Presign export archive in S3 |
| demo-token | `/demo-token` | Create ephemeral Cognito user for the demo code-gate |
| wake | `/wake` | Start TiTiler box if stopped (not the agent -- agent is broker-provisioned) |

These Lambdas are DynamoDB on-demand + S3 presign operations with near-zero idle cost.

---

## TiTiler raster tiles

**EC2 instance:** `i-06cfdd3d6c66b2126`, `t3.small`, EIP `44.247.187.124`
**Port:** `:8080`
**IaC:** `infra/aws-titiler/`

TiTiler is the **one truly always-on** component in the design: it must be up 24/7 to serve tile
requests from the browser even when the agent is offline.

- Runs two Uvicorn workers with GDAL `/vsis3` range reads against the `grace-2-hazard-prod-cog`
  bucket.
- A **watchdog** loop restarts the process if it wedges (history: `job-0314` wedge incident).
- CloudFront `/cog*` and `/tiles*` behaviors route directly to the EC2 origin, bypassing the ALB.

!!! warning "No S3 Gateway endpoint"
    As of the 2026-07-04 architecture read pass, there is no free S3 Gateway endpoint configured
    despite TiTiler's continuous `/vsis3` range reads. Adding one (free) would reduce NAT/internet
    data-transfer costs.

---

## Basemaps

CartoDB basemaps (Positron, Dark Matter) are served directly from CartoDB CDN -- they do not touch
any GRACE-2 infrastructure.
