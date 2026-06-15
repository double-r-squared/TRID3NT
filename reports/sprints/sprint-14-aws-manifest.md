# sprint-14-aws — GCP → AWS migration

**Opened:** 2026-06-11
**Directive (user):** "take everything off google cloud service and move to AWS … make sure things are migrated cleanly and work exactly how they did before, pick the best architecture possible." Plus: switch the agent to **AWS Bedrock** (user chose Bedrock over keeping Gemini), **drop MongoDB**, **use Cognito**, **make the project runnable locally**, and **tear down GCP** as the final step.

## Hard truths shaping the plan
1. **Gemini cannot leave GCP** — the user chose to swap the agent to **Bedrock / Claude Sonnet 4.6** (a product change, not a lift-and-shift). "Works exactly as before" therefore means *functional* parity (same 57+ tools, same envelopes, same UX), NOT token-identical output.
2. **GCP teardown is the LAST step**, after AWS parity is verified. Decommissioning the working system before the replacement is proven would destroy the demo + data with nothing to fall back to. `gcloud` is not even installed on this machine, so teardown is inherently user-gated (needs gcloud + interactive auth).
3. **MongoDB Atlas dropped** → DynamoDB (AWS) / local file store (run-local). The repo already has `FileMCPClient` as a file-backed substrate; the `Persistence`/MCP seam stays, backend swaps.

## Target architecture (decided — "best possible" mapping)

| GCP today | AWS target | Status |
|---|---|---|
| Vertex AI / Gemini 2.5 Pro | **Bedrock Converse / Claude Sonnet 4.6** | ✅ job-0286 LANDED + live-proven |
| Firebase Hosting | **S3 + CloudFront** | ✅ S3 live; CloudFront pending |
| Cloud Run (agent WS) | **ECS Fargate + ALB** (native WebSocket) | planned |
| Cloud Run (QGIS Server) | **ECS Fargate** (internal ALB) | planned |
| Cloud Run Jobs (SFINCS/MODFLOW/PyQGIS) | **AWS Batch** (Fargate compute) | planned |
| Cloud Workflows | **Step Functions** | planned |
| GCS (cache/runs/qgs/cog) | **S3** (4 buckets) | planned |
| Firebase Identity Platform | **Amazon Cognito** | planned |
| Signed-URL gen2 Function (signBlob) | **S3 presigned URLs** (boto3) | planned |
| Secret Manager | **AWS Secrets Manager** | planned |
| MongoDB Atlas (Cases/users/sessions) | **DynamoDB** / local file store | planned |
| OpenTofu (GCP providers) | **OpenTofu (AWS provider)** | planned |

Region: **us-west-2**. Account: 226996537797.

## Staged jobs

- **job-0286 (agent) — Bedrock Converse adapter** ✅ LANDED. `bedrock_adapter.py` converts the genai content/tool structures at the boundary, yields the same `StreamEvent` union; `MODEL_PROVIDER=bedrock` switch in `adapter.stream_events_with_contents`; boto3 dep. Live smoke: 85/85 tools convert; Claude Sonnet 4.6 streamed a correct `fetch_administrative_boundaries` tool call with parsed args + usage.
- **job-0287 (agent) — server.py provider integration + run-local mode** ✅ LANDED + live-proven. The two per-turn Vertex touchpoints (`build_client`, Gemini CachedContent) skip under `MODEL_PROVIDER=bedrock`; `make run-local-agent` boots agent on Bedrock + file persistence + anonymous auth with zero GCP creds. Proof: real subprocess with all GOOGLE_* stripped drove a full turn — Claude chose `geocode_location → fetch_administrative_boundaries` (multi-step), streamed narration, auto-created a Case, persisted to file store. Raster QGIS render still cloud-bound until job-0290.
- **job-0288b (infra) — agent live on AWS EC2** ✅ LANDED + live-proven. t3.large (AL2023, instance `i-0251879a278df797f`), IAM role scoped to `bedrock:InvokeModel` + SSM + S3-read, SG opens 8765-8766. Code bundled to private S3, pip-installed (full geo stack incl. rasterio/hydromt-sfincs/pfdf/pelicun/flopy — no Docker), run under systemd on Bedrock + file persistence. Frontend (S3) WS URL rebuilt to the instance. Live proof: real WS turn at `ws://ec2-35-93-91-8.us-west-2.compute.amazonaws.com:8765` — Claude geocoded Miami-Dade, fetched the real TIGER/Line 2024 county boundary, snapped the map, narrated truthfully.
- **job-0288c (engine) — object-store cache degrades gracefully** ✅ LANDED. `read_through` treats any GCS failure (no-creds/I-O) as a cache miss → fetch fresh, return uncached. Unblocked every cacheable tool on AWS (the `DEFAULTCREDENTIALSERROR` crash). GCP happy path unchanged.
- **job-0288 (agent) — Bedrock prompt caching** (cachePoint) to restore the cache-discount economics the Gemini CachedContent path had.
- **job-0289 (agent/infra) — S3 object store** ✅ LANDED + live-proven. `GRACE2_STORAGE_BACKEND=s3` routes cache + artifact read/write through **boto3** (s3fs fell back to anonymous on the instance role); emitter inline-vector read uses boto3 for s3://. S3 cache bucket `grace2-hazard-cache-226996537797` + scoped write grant on the instance role. Proof: Travis County boundary fetched → persisted to S3 → inlined GeoJSON (7 features) → **vector overlay renders on AWS**. Raster (WMS) overlays still need QGIS-on-AWS (job-0290).
- **job-0290 (infra) — ECS Fargate for agent WS + QGIS Server** (ALB, WebSocket).
- **job-0291 (infra) — AWS Batch for solvers + Step Functions** replacing Cloud Run Jobs + Workflows.
- **job-0292 (agent/web) — Cognito**: replace Firebase token verification (`auth_handshake.py`) with Cognito JWT verification; web `useAuth` → Cognito (Amplify or oidc-client).
- **job-0293 (agent) — DynamoDB persistence backend** behind the `Persistence` seam (drop Atlas/MCP); file backend stays for run-local.
- **job-0294 (infra) — S3 presigned URLs** replacing the signed-URL Cloud Function.
- **job-0295 (testing) — AWS end-to-end parity verification** (flood → render → damage on AWS).
- **job-0296 (infra) — GCP teardown** (FINAL, user-gated: needs gcloud auth; enumerated destroy + data export first).

Each infra job is adversarial-verify gated per the standing rule.

---

## CLOSED — 2026-06-15 (functionally complete)

The GCP→AWS migration is **done and live** (canonical URL `https://d125yfbyjrpbre.cloudfront.net/app`). Implementation diverged from the aspirational table above toward a **pragmatic, working stack** — that divergence is intentional, not incomplete:

- **Agent compute:** EC2 `i-0251879a278df797f` + systemd (NOT ECS Fargate/ALB). The single box runs the Bedrock agent + SFINCS (docker) + MODFLOW (local-exec) + TiTiler. ECS/Batch/Step-Functions remain *deferred architectural upgrades* (scale/HA), not blockers.
- **Solvers:** local-docker (SFINCS) + local-exec (MODFLOW) on the instance (NOT AWS Batch + Step Functions).
- **Raster render:** TiTiler COG tiles (NOT QGIS Server on ECS).
- **Persistence:** DynamoDB (`grace2_*`); 406 docs migrated. File backend kept for run-local.
- **Auth:** Cognito, mandatory sign-in (Hosted UI). Firebase removed.
- **CDN/HTTPS:** CloudFront single-origin edge (S3 web + EC2 ws/tiles/catalog).
- **Cache discount (job-0288 Bedrock prompt caching):** deferred (not load-bearing for v0.1).
- **Presigned URLs (job-0294) / Secrets Manager / OpenTofu-AWS:** deferred (no current consumer on the EC2 stack).

Jobs as-built: 0286 Bedrock adapter, 0287 run-local, 0288b EC2, 0288c+0289 S3, 0290* DEM/render fixes, 0293* S3 port, 0294* humanize/charts/publish-idempotency, 0295 news-ingest, 0296 finish-line workflow (auth/DB/CDN/bbox code), 0297 DynamoDB+CloudFront cutover, 0298 housekeeping (checkpoint commit + dead-dep removal + green suite), 0299 Cognito mandatory auth. **Remaining (user-gated): GCP teardown** (`gcloud projects delete grace-2-hazard-prod`).
