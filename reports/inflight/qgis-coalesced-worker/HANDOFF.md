# HANDOFF: coalesced QGIS Processing worker (turn-scoped warm Spot box)

**From:** tools session (built + proved the reference). **To:** Orchestrator (final wiring + Fargate deploy). **Reframes:** job-0308 (QGIS-on-AWS) from an **always-on QGIS Server** to a **turn-scoped warm Spot worker**.

## What's proven (local, real Boulder DEM, grace2 QGIS 3.40.3)
- **Coalescing** (`/tmp/qgis_proof/proof.py`): one persistent `QgsApplication` runs an 8-algo chain (slope/aspect/hillshade/contour/reclassify/polygonize/dissolve/buffer) in ONE init (~0.7-2.2s) -> total 1.9s, vs the current `docker run qgis_process`-per-call path ~10.7s (8 x ~1.2s init). **Saves ~77% of the turn's QGIS wall-time** = the avoided re-inits.
- **The reference server** (`processing_server.py`): import == one init (379 algos incl GRASS, `INIT_COUNT=1`); 4 `run_algorithm` calls each **stage `s3://` input -> `processing.run` -> upload output `s3://`**, all served by the single warm init. Outputs landed at `s3://grace2-hazard-runs-226996537797/runs/<id>/OUTPUT.{tif,gpkg}`.

## The deployable artifact
`processing_server.py` (in this dir) -- init-once + `POST /run {algorithm, params}` (s3 stage/run/upload) + `/healthz` + an **idle-watchdog** that `os._exit(0)` after `GRACE2_QGIS_IDLE_TTL_S` (the ~1-min turn tail -> task self-terminates -> scale to zero). Stateless per request (Spot-reclaim safe).

## Orchestrator's final wiring (the parts I do NOT own: services/workers + infra + main.py bind)
1. **Place** `processing_server.py` under `services/workers/qgis/`.
2. **Image** (`services/workers/qgis/Dockerfile`, exists): `FROM qgis/qgis:ltr` + grass/saga; ADD `fastapi uvicorn boto3`; **FIX the grass-binary path** -- `grassprovider` loaded but `grass:r.watershed` showed n/a locally (the provider needs `GISBASE`/grass on PATH). **Slim it** -- image pull dominates cold start; trim to the curated-allowlist providers to cut the ~1-2 min Fargate cold start.
3. **Fargate Spot** task: `uvicorn processing_server:build_app() --host 0.0.0.0 --port 8000`. Spot (bursty, short, retry-tolerant).
4. **IAM** (task role): s3 read on cache/cog/fgb buckets; s3 write on `GRACE2_RUNS_BUCKET`.
5. **Lifecycle** (the scale-to-zero): get-or-create keyed by session/Case -- spin up on the FIRST QGIS call, or **pre-warm on plan** (the agent knows the algos at plan time; overlap the ~1 min spin-up with the non-QGIS fetches). Reuse across the turn. End on **Spot-reclaim (-> re-spin on next call, cheap+stateless)** or **turn-done + ~1-min idle tail** (the server's watchdog). NOT always-on.
6. **Submitter bind**: replace the `docker run qgis_process` submitter with an agent-side **HTTP submitter** -- `POST <box_url>/run {algorithm, params}` -- bound via `set_worker_submitter` (the `_WORKER_SUBMITTER` seam in `passthroughs.py`). The box URL comes from the get-or-create. Tools session can supply the submitter function; the startup bind (main.py) is yours.

## Agent-side knowledge (tools session owns; small)
Tool descriptions teach **composition only** -- "chain dependent geospatial steps (pass one step's output layer into the next); the runtime coalesces them on one warm box." No box/Spot/TTL in the prompt. Determinism: one box per turn, always (no LLM-driven scheduling; the multi-box/parallel path was explicitly dropped).

## Categorization reminder
Wrapped QGIS algos route by FUNCTION (terrain/hydrology/geographic_primitives), never a "qgis" category -- per Decision Q (QGIS Processing is the primary compute substrate; the QGIS-ness is invisible to routing). Only the 3 meta-tools (`list/describe/qgis_process`) are the generic surface.
