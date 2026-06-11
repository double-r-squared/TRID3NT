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
- **job-0287 (agent) — server.py provider integration + run-local mode**: `GeminiSettings`→provider-neutral settings; client build skipped for bedrock; `MODEL_PROVIDER`/`AWS_REGION` env; a `make run-local` that boots agent (bedrock) + web + file persistence + anonymous auth, no cloud render dependency.
- **job-0288 (agent) — Bedrock prompt caching** (cachePoint) to restore the cache-discount economics the Gemini CachedContent path had.
- **job-0289 (infra) — S3 storage swap**: 4 buckets + a `storage` abstraction (gs:// → s3://) behind the existing cache/publish seams; CloudFront for the web.
- **job-0290 (infra) — ECS Fargate for agent WS + QGIS Server** (ALB, WebSocket).
- **job-0291 (infra) — AWS Batch for solvers + Step Functions** replacing Cloud Run Jobs + Workflows.
- **job-0292 (agent/web) — Cognito**: replace Firebase token verification (`auth_handshake.py`) with Cognito JWT verification; web `useAuth` → Cognito (Amplify or oidc-client).
- **job-0293 (agent) — DynamoDB persistence backend** behind the `Persistence` seam (drop Atlas/MCP); file backend stays for run-local.
- **job-0294 (infra) — S3 presigned URLs** replacing the signed-URL Cloud Function.
- **job-0295 (testing) — AWS end-to-end parity verification** (flood → render → damage on AWS).
- **job-0296 (infra) — GCP teardown** (FINAL, user-gated: needs gcloud auth; enumerated destroy + data export first).

Each infra job is adversarial-verify gated per the standing rule.
