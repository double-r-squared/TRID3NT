# Report: QGIS Server invoker-only IAM flip + thin streaming agent WMS proxy

**Job ID:** job-0255-infra-20260611
**Sprint:** sprint-13.5 (Stage 2)
**Specialist:** infra
**Task:** Flip QGIS Server Cloud Run from `allUsers` invoker to invoker-only (Tofu, plan-and-document, NO apply); add a thin streaming `/qgis-proxy` on the agent's existing HTTP listener (OIDC token, credential stripping, no-open-proxy, env-gated OFF by default); minimal env-gated web tile-URL substitution; tests + live Gemini-free proof.
**Status:** ready-for-audit

## Summary

The QGIS Server Cloud Run invoker binding is flipped in Tofu from `allUsers` to the agent-runtime SA only (`infra/qgis-server.tf`); `tofu validate` is green and `tofu plan` shows a clean IAM-only change (allUsers DESTROYED, agent invoker CREATED, service updated **in-place** — zero replacement triggers). A new thin streaming WMS proxy (`grace2_agent/qgis_proxy.py`) is wired onto the agent's existing :8766 HTTP listener (`tool_catalog_http.py`) at `GET /qgis-proxy`: it forwards the query string verbatim to the fixed configured QGIS base, attaches a Google-signed OIDC token on Cloud Run (graceful no-token in dev), strips ALL inbound user credentials, streams the response chunk-by-chunk (never buffering a whole tile), rejects open-proxy attempts, and is env-gated OFF by default (`QGIS_PROXY_ENABLED=false` => route 404s — today's behavior unchanged). The web client (`Map.tsx`) gets a 29-line env-gated proxy-base rewrite (`VITE_QGIS_PROXY_BASE` absent => byte-identical). Proven live: one real GetMap basemap tile (256x256 RGBA PNG, 79,350 bytes) fetched THROUGH the proxy from the dev QGIS Cloud Run URL with fake user credentials attached and stripped.

## Changes Made

- **`infra/qgis-server.tf`** (IAM binding only): replaced `qgis_server_public_invoker` (`member="allUsers"`) with `qgis_server_agent_invoker` (`member=serviceAccount:${google_service_account.agent_runtime.email}`); updated the service header comment (ingress=ALL + invoker-only = standard Cloud Run private posture). Inventoried every accessor before changing (table below).
- **`services/agent/src/grace2_agent/qgis_proxy.py`** (NEW): `qgis_proxy_enabled()` / `qgis_server_base_url()`; `fetch_oidc_token(base)` (id_token against the service ROOT audience, None+graceful in dev); `stream_qgis_response(...)` (requests stream=True in a worker thread bridged to a BOUNDED asyncio queue => O(chunk) memory; status+filtered-headers before first byte; honest 4xx/5xx relay); `STRIPPED_REQUEST_HEADERS` / `PASSTHROUGH_RESPONSE_HEADERS` allowlists.
- **`services/agent/src/grace2_agent/tool_catalog_http.py`** (route wiring, minimal/isolated): `/qgis-proxy` branch in `_handle_http` BEFORE the buffered paths (env-off => falls through to existing 404); `_handle_qgis_proxy()` bridges to the asyncio writer (head, chunks, 502 on unreachable, owns drain+close); `_format_streaming_head()` for streamed responses + CORS; added `502:"Bad Gateway"` to the reason map.
- **`web/src/Map.tsx`** (env-gated, 27 insertions): `applyQgisProxy(wmsUrl)` rewrites scheme+host+path to `VITE_QGIS_PROXY_BASE`, preserving the WMS query string; absent => byte-identical. Applied at `WMS_BASE_URL` (basemap) and inside `buildWmsTileUrl` (overlays).
- **Tests** (NEW): `tests/test_qgis_proxy.py` (16), `tests/test_qgis_proxy_http_route.py` (4), `web/src/Map.qgisProxy.test.tsx` (4).
- **`reports/inflight/sprint-13-5-USER_UNBLOCK.md`**: appended 0255-A (apply + rollback + loud sequencing warning) and 0255-B (verification curls).

## IAM: before / after

| | Before | After |
|---|---|---|
| Resource | `qgis_server_public_invoker` | `qgis_server_agent_invoker` |
| Role | `roles/run.invoker` | `roles/run.invoker` |
| Member | `allUsers` | `serviceAccount:agent-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` |
| Effect | direct browser->QGIS = 200 (public) | direct browser->QGIS = 403; only the agent SA (via /qgis-proxy) invokes |
| Ingress | `INGRESS_TRAFFIC_ALL` | `INGRESS_TRAFFIC_ALL` (unchanged) — gating moves from none to IAM |

**Accessor inventory (checked before changing):** PyQGIS worker (Cloud Run **Job**) + SFINCS/MODFLOW jobs write `.qgs`/layers to **GCS** via their own SAs; they do NOT invoke the QGIS **service** (rendering != writing, Inv. 4) — no binding. The `qgis-server` runtime SA is the service's OWN identity (callee) — no binding. Web client post-flip calls the agent proxy, not QGIS — no grant. Agent-runtime SA is the ONLY invoker now.

**`tofu validate`:** green (`evidence/tofu_validate.txt`). **`tofu plan` (IAM excerpt, `evidence/tofu_plan_iam_only.txt`):**
```
  # google_cloud_run_v2_service.qgis_server will be updated in-place
  # google_cloud_run_v2_service_iam_member.qgis_server_agent_invoker will be created
      + member   = "serviceAccount:agent-runtime@grace-2-hazard-prod.iam.gserviceaccount.com"
  # google_cloud_run_v2_service_iam_member.qgis_server_public_invoker will be destroyed
      - member   = "allUsers"
```
- **Zero** `forces replacement`/`must be replaced` for the QGIS service (`evidence/tofu_plan_qgis_iam.txt`, grep=0). Service "updated in-place"; the `client/client_version->null` diff is pre-existing GCP metadata noise.
- **NOT APPLIED** — apply is USER step 0255-A. The 4 `google_project_service.enabled[firebase*]` adds in the targeted plan are **job-0250's queued auth APIs** (transitive depends_on), NOT this job's change.

## Proxy header-stripping table

| Direction | Header | Disposition |
|---|---|---|
| Inbound | `Authorization` | **stripped** (proxy forwards none of client's headers; sets its OWN OIDC bearer) |
| Inbound | `Cookie` / `Set-Cookie` | **stripped** |
| Inbound | `X-Firebase-AppCheck`, `X-Firebase-Auth` | **stripped** |
| Inbound | `X-Goog-Authenticated-User-{Id,Email}`, `X-Goog-IAP-Jwt-Assertion` | **stripped** |
| Inbound | `X-Forwarded-Authorization`, `Proxy-Authorization` | **stripped** |
| Inbound | `X-Grace2-Session`, `X-Grace2-User` | **stripped** |
| Forwarded | `User-Agent: grace-2-agent-qgis-proxy/0.1` | set by proxy |
| Forwarded | `Authorization: Bearer <OIDC>` | set by proxy ONLY (Cloud Run; absent in dev) |
| Response | `Content-Type/Length/Encoding`, `Cache-Control`, `Expires`, `Last-Modified`, `ETag` | relayed |
| Response | `Set-Cookie`, `Server`, any auth echo | **dropped** (allowlist) |

Strip is **by construction**: the proxy never copies inbound headers — it builds the forward set itself (UA + optional OIDC). `test_credential_stripping_no_inbound_headers_forwarded` asserts the forwarded dict == `{"user-agent":"..."}` in dev.

## Streaming mechanism

`requests` (existing dep, no new dependency) is blocking, so open+chunked-read runs in a daemon worker thread. Each `iter_content(chunk_size)` piece is pushed onto a **bounded** `asyncio.Queue(maxsize=4)` via `run_coroutine_threadsafe`; the worker BLOCKS on `fut.result()` when full => producer/consumer lockstep => resident memory O(chunk_size) ~= 64 KiB, never O(tile). Status + filtered headers go out before the first body byte. `test_streaming_large_body_chunked` relays 3 MB, asserts >1 chunk, every chunk <= chunk_size, `stream=True` passed.

## Env-gate matrix

| Scenario | `QGIS_PROXY_ENABLED` | QGIS IAM | `VITE_QGIS_PROXY_BASE` | Behavior |
|---|---|---|---|---|
| **dev today** (no apply) | unset/false | allUsers (public) | unset | /qgis-proxy 404s; web hits QGIS directly; **byte-identical to before** |
| **dev post-apply** (local) | true | invoker-only | `https://<agent>/qgis-proxy` | direct QGIS=403; web->proxy->QGIS(OIDC)->tile (NOT recommended for local demo, see 0255-A) |
| **prod** (job-0257) | true | invoker-only | `https://<agent-prod>/qgis-proxy` | hardened: direct QGIS=403; all tiles via proxy |
| **proxy on, no ADC** | true | (either) | (either) | OIDC token=None => forward unauthenticated => works vs public QGIS; never crashes |

Two independent gates => no flag-day: agent route + web rewrite each default to today's behavior; the IAM apply is a separate USER step sequenced after the prod proxy path verifies.

## USER_UNBLOCK items added

- **0255-A** — `tofu apply` (-target'ed to the two IAM members) + rollback one-liner (`gcloud run services add-iam-policy-binding ... allUsers`) + LOUD sequencing warning (applying breaks dev rendering until the proxy path is live; apply only after end-to-end verification; do NOT apply for the local tailnet demo).
- **0255-B** — verification curls: direct QGIS GetCapabilities => expect **403**; agent /qgis-proxy GetMap => expect **200** image/png.

## Decisions Made

- **Proxy module at `grace2_agent/qgis_proxy.py`, not `services/workers/qgis_proxy.py` (kickoff path).** Rationale: the proxy runs IN the agent process on its :8766 listener and MUST be importable by it; the agent venv has `services/agent/src` on path, `services/workers/` is NOT importable (verified `import services.workers` fails). The named path would ship dead, unimportable code. Alternatives: path-surgery on the agent (rejected — breaks the workers-in-own-containers boundary) or inline into tool_catalog_http.py (rejected — contract lens wants a discrete streaming unit).
- **Route wiring in `tool_catalog_http.py`, not `server.py`.** The kickoff's "EXISTING HTTP listener (the :8766 catalog server)" IS `tool_catalog_http.py` (`_handle_http` dispatch); `server.py` only mounts it via `serve_catalog_http` and has no HTTP path-dispatch surface (it's the WebSocket server). The mount point is untouched.
- **Forward only the fixed configured base; client query string is all that transits.** No-open-proxy by construction. `test_open_proxy_rejected_inbound_host_ignored` proves a malicious absolute-URL param doesn't change the upstream netloc.
- **OIDC audience = service ROOT (`https://<host>`), not `/ogc/wms`.** Cloud Run validates `aud` against the root URL.

## Invariants Touched

- **4. Rendering through QGIS Server:** preserves — Tier B still reaches the browser only via QGIS Server; the proxy is a transparent forwarder.
- **5. Tier separation:** strengthens — client no longer touches QGIS directly; goes through the agent (sole invoker grant). Buckets stay private (PAP unchanged).
- **9. Confirmation before consequence / no cost theater:** N/A — no user-facing cost surface; deployment-side hardening only.

## Open Questions

- **OQ-0255-PROXY-MODULE-PATH (TENTATIVE — resolved in-scope):** kickoff named `services/workers/qgis_proxy.py`, but that path is not importable by the agent process that runs the proxy. Placed at `services/agent/src/grace2_agent/qgis_proxy.py`. Functionally identical, correct package boundary. Flagging for audit confirmation — it's the only way the proxy can actually run.
- **OQ-0255-ROUTE-FILE (TENTATIVE — resolved in-scope):** route wiring is in `tool_catalog_http.py` (the actual listener), not `server.py` (WS server, which only mounts it). `server.py` needs no change.
- **OQ-0255-CATALOG-HTTP-UNTRACKED:** `tool_catalog_http.py` was untracked in git (`??`) from a prior wave — pre-existing tree drift unrelated to this job. Since this job's route wiring lives in it, it is staged in this commit; only the `/qgis-proxy` branch + `_format_streaming_head` + `_handle_qgis_proxy` + the `502` reason are mine.
- **OQ-0255-WEB-OWNERSHIP (resolved):** the tile-URL site is `web/src/Map.tsx`, NOT `App.tsx` (job-0253b's). Touched only Map.tsx (+ new sibling test). No overlap.

## Dependencies and Impacts

- **Depends on:** job-0252 (DONE). The agent-runtime SA the new binding references (`google_service_account.agent_runtime`) is in `infra/gcp.tf`.
- **Affects:** job-0257 (prod agent must set `QGIS_PROXY_ENABLED=true` + `QGIS_SERVER_URL`); job-0256 (prod web must set `VITE_QGIS_PROXY_BASE`); User (0255-A/B apply+verify — sequence after end-to-end verification).

## Verification

- **Tests:** proxy unit+route `pytest test_qgis_proxy.py test_qgis_proxy_http_route.py` => **28 passed**. Full agent suite `pytest tests/ -q --ignore=tests/live` => **4387 passed, 5 failed, 72 skipped, 1 xfailed** — the 5 are EXACTLY the proven pre-existing set (3x test_data_fetch docstring-tier + 2x test_model_flood_scenario GCS); zero new failures. Web `vitest run Map.qgisProxy.test.tsx Map.test.tsx` => **56 passed** (4 new + existing Map suite unaffected). `tofu validate` green; `tofu plan` IAM-only, no service replacement (grep=0).
- **Live E2E (Gemini-free):** `evidence/live_proxy_proof.txt` + `evidence/proxied_tile.png` — a throwaway in-process catalog+proxy server on an ephemeral port (reaped) fetched a real GetMap basemap tile THROUGH /qgis-proxy from the dev QGIS Cloud Run URL with `Authorization: Bearer FAKE-USER-TOKEN` + `Cookie` attached (stripped): `HTTP 200 content-type=image/png bytes=79350`, `is_png=True`, `distinct_byte_values=256`, pixel-checked non-empty (renders the FL panhandle/Gulf basemap). The running dev agent was NOT restarted.
- **Results:** pass.
