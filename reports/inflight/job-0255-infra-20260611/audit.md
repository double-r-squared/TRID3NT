# job-0255 — QGIS Server invoker-only + thin agent WMS proxy (FROZEN KICKOFF)

**Specialist:** infra (cross-cuts into the agent's HTTP surface per pinned ownership below)
**Sprint:** 13.5 Stage 2
**Model:** Opus
**Opened:** 2026-06-11
**Depends on:** job-0252 (DONE, panel 3/4) — satisfied. Runs while the job-0253 panel and the job-0254 design scout are out; NO file overlap exists with either.

## Binding context
- Manifest scope block (sprint-13-5-manifest.md § job-0255): flip QGIS Server Cloud Run from `--allow-unauthenticated` to invoker-only; add a thin streaming WMS proxy on the agent's HTTP surface; client tile URLs move from `https://<qgis-run-url>/...` to `<agent-http>/qgis-proxy?...`; proxy strips user credentials before forwarding.
- `reports/sprints/sprint-13-5-decisions.md` (Decisions 6/9/10) + standing quota rule: NO Gemini/Vertex generate calls.
- The dev demo is LIVE on the tailnet (web :5173 → agent ws 100.92.163.46:8765, catalog HTTP :8766, QGIS Cloud Run currently public). NOTHING you do may break it: all behavior changes env-gated OFF by default.

## Scope

### 1. Tofu: invoker-only QGIS Server (`infra/main.tf`)
- Replace the public-access IAM binding (`allUsers` → roles/run.invoker, however it is expressed today — read the current resource first) with invoker-only: grant `roles/run.invoker` on the QGIS service to the AGENT service's runtime SA (and keep any existing service-to-service grants that other components need — inventory first, document each).
- `tofu validate` + `tofu plan` evidence in the report. **DO NOT APPLY** — `tofu apply` is a USER step; append the exact apply command + a rollback one-liner (re-grant allUsers) to `reports/inflight/sprint-13-5-USER_UNBLOCK.md` as item 0255-A, plus a verification curl (direct QGIS URL → 403) as 0255-B. The live dev demo currently RENDERS via the public QGIS URL — note loudly in the UNBLOCK item that applying flips dev rendering to require the proxy path (sequencing: user applies only after the proxy path is verified end-to-end).

### 2. Agent-side proxy (`services/workers/qgis_proxy.py` new + `services/agent/src/grace2_agent/server.py` route wiring)
- `GET /qgis-proxy?<WMS params>` on the agent's EXISTING HTTP listener (the :8766 catalog server — read how routes are registered there and follow the same pattern).
- Forwards the query string verbatim to the QGIS Server base URL (env `QGIS_SERVER_URL`, already in use — verify the exact name in the code), attaching a Google-signed OIDC identity token for the QGIS Cloud Run audience (use `google.auth` / metadata server when on Cloud Run; in dev with no credentials and a public QGIS, forward WITHOUT a token — degrade gracefully, never crash).
- STREAM the response body (chunked relay — the contract lens will check the proxy never buffers whole tiles in memory; use an async HTTP client with streamed reads).
- Strip ALL inbound user credentials/headers before forwarding (Authorization, Cookie, any session identifiers) — QGIS Server must never see user identity. Pass through only WMS-relevant params and content-type/cache headers on the response.
- Env gate `QGIS_PROXY_ENABLED` default **"false"**: when off, the route 404s (or is unregistered) and NOTHING about today's behavior changes. job-0257 flips it in prod. Loud TODO(job-0257).
- Allowlist sanity: only forward to the configured QGIS base URL (no open-proxy: ignore/reject any param attempting to change the upstream host).

### 3. Web tile-URL derivation (MINIMAL, env-gated)
- Where the web client builds WMS tile URLs from LayerURI (Map.tsx WMS wiring), add the proxy-base substitution gated on `VITE_QGIS_PROXY_BASE` (absent → today's behavior byte-identical). Touch the minimum surface; if this exceeds ~30 lines of web change, STOP and flag it for a web job instead.

### 4. Tests
- Proxy: param passthrough; credential stripping (assert forwarded request has no Authorization/Cookie); streaming (large fake body relayed without full buffering — bound memory or assert chunked iteration); open-proxy rejection; disabled-by-default 404; dev-mode no-token graceful path; upstream 5xx relayed honestly.
- Tofu validate green; plan shows ONLY the IAM binding change (no service replacement — if the plan wants to replace the QGIS service, STOP and report).
- Full agent suite: only the 5 proven pre-existing failures allowed (3x test_data_fetch docstring-tier, 2x test_model_flood_scenario GCS). Web vitest green if §3 touched web.
- Live local proof (Gemini-free): run the proxy against the REAL dev QGIS Cloud Run URL (public today) — fetch one GetMap tile through it and pixel-check non-empty; evidence in `evidence/`.

## Hard constraints
- NO Gemini/Vertex calls. NO `tofu apply` / gcloud mutations — plan-and-document only. Do NOT restart the running dev agent; your suite runs and any throwaway local proxy instances must use fresh ports and be reaped.
- Files owned: `infra/main.tf` (IAM binding only), `services/workers/qgis_proxy.py` (new), `services/agent/src/grace2_agent/server.py` (route wiring only — keep the hunk minimal and isolated), the web tile-URL derivation site (gated, minimal), tests, USER_UNBLOCK additions.
- `git add` only files you touched; never `git add -A` (tree carries unrelated drift). Commit `job-0255: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
`reports/inflight/job-0255-infra-20260611/{report.md,STATE=IN_REVIEW}`; report documents: current vs new IAM expression, the proxy's header-stripping table, the streaming mechanism, the env-gate matrix (dev today / dev post-apply / prod), and USER_UNBLOCK 0255-A/B. 4-lens adversarial panel follows at orchestrator level.
