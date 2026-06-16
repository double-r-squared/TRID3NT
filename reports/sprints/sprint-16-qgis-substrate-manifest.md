# sprint-16 — QGIS compute substrate on AWS (HEC-HMS proof)

**Opened:** drafted 2026-06-16 (queued; not yet executing)
**Directive (user, 2026-06-16):** "we need QGIS Server and the compatible plugins were slotted to be tools the LLM could call to access other compute engines like HEC-HMS … this was the plan all along to utilize the built ecosystem around QGIS so we aren't hand-rolling tools that exist and are already documented and ready to use off the shelf as agentic tools — let's realign and lean into this come the next sprint."

**Decision spine set by user this turn:** (1) **re-host QGIS on AWS FIRST**; (2) first engine proof = **HEC-HMS** (headless + Linux); (3) engine selection criterion = **cloud-deployability (headless + Linux)**, not modeling power — **HEC-RAS deferred** (Windows-native + GUI-driven compute, not cloud-friendly).

Depends on landing the SRS amendment **Decision Q** (proposal: `reports/srs-amendment-qgis-compute-substrate-PROPOSAL.md`) — user lands into `docs/srs/*` + `make srs` before/early in the sprint.

---

## Why this sprint (the thesis)

The AWS migration (sprint-14-aws) shipped **TiTiler** for raster rendering and never ported the **QGIS Server + PyQGIS Processing worker** off GCP. That was an expedient narrowing, not the architecture: QGIS is the **integration substrate**. The QGIS **Processing framework** (GDAL/GRASS/SAGA/TauDEM + provider plugins) exposes algorithms with **stable IDs, typed params, and existing documentation** — a near-1:1 fit for LLM tool-calling. Wrapping that catalog as the agent's primary compute surface means **enabling a provider ≈ dozens of documented agentic tools for free**, and external engines (HEC-HMS, the MODFLOW family, …) slot in as **headless solvers** with QGIS doing GIS prep + result handling — exactly the SFINCS/HydroMT shape already proven.

Appendix E already scaffolds this (QGIS-HMS row, three-tier bake strategy). This sprint **elevates QGIS Processing from "informational / lean-to-Python-shim" to the primary compute surface** (Decision Q) and proves it end-to-end with HEC-HMS.

## Hard truths shaping the plan

1. **A is the spine.** Render parity (styled WMS + server-rendered vectors), the Processing worker, and the fix for the two open render bugs (NWS-vector-as-TiTiler-raster 500; small-flood framing) all come from standing QGIS back up on AWS. Nothing else fully lands without it.
2. **TiTiler stays, demoted.** It remains the cheap dynamic tiler for plain COG rasters; QGIS Server handles styled/vector/layout. `publish_layer` already branches on backend — keep both paths.
3. **Engine cloud-deployability is the gate.** HEC-HMS (headless Jython/CLI, Linux) and MODFLOW (already live, `mf6` local-exec) qualify. HEC-RAS does **not** (Windows GUI/MS-native) — explicitly out of this sprint.
4. **Don't hand-roll what QGIS already does.** Net-new bespoke tools are the exception; the default is "is there a Processing algorithm/provider for this?" (Decision Q). A deprecation pass retires overlaps.
5. **Standing rule:** every job demands live E2E evidence; every dependency edge is adversarial-verify gated (4-lens, ≥3-of-4 confirm). High-importance infra/agent-loop jobs get Opus verifiers per the no-cost-cap rule.

## Target shape

| Concern | Today (AWS) | sprint-16 target |
|---|---|---|
| Raster render | TiTiler COG tiles only | QGIS Server (styled WMS + vector + layout) **+** TiTiler (plain COG fast path) |
| QGIS Processing | tools registered, no AWS backend | headless Processing worker container on AWS; `qgis_process` live |
| Agent compute surface | ~hand-rolled atomic tools | generic **discover→describe→run** over the curated QGIS Processing catalog (primary) + bespoke where nothing off-the-shelf exists |
| Engines | SFINCS (docker), MODFLOW (local-exec) | **+ HEC-HMS** (headless container) via QGIS-prep → solve → render |

Host: EC2 `i-0251879a278df797f` to start (containers alongside the agent, mirroring SFINCS-docker); ECS/Batch remains the deferred scale upgrade.

---

## Staged jobs

> Kickoffs are frozen when handed to a specialist (per AGENTS.md). The lines below are the manifest-level scope; each becomes a `reports/inflight/<job-id>/audit.md` at dispatch.

- **job-0308 (infra) — QGIS Server + headless Processing worker on AWS.** Containerize `qgis/qgis-server` (FCGI/WMS) + a PyQGIS/`qgis_process` worker (re-home the GCP job-0062 worker), `QT_QPA_PLATFORM=offscreen`, S3-backed read/write via boto3 (the job-0289 instance-role lesson — NOT s3fs/`/vsis3/`). CloudFront `/ogc/wms` (or reuse `/cog` origin pattern) routes to QGIS Server. `publish_layer` AWS branch gains a QGIS-Server path for styled/vector layers; TiTiler stays for plain COGs. **Acceptance:** a styled vector layer (NWS warnings) + a styled raster render live on the HTTPS map (fixes the case-3 vector-as-raster 500); `qgis_process --version` + one native algorithm (`native:buffer` / `gdal:slope`) run headless on the box. Spine — everything below depends on it.
- **job-0309 (agent) — generic QGIS-Processing → tool adapter.** Promote `list_qgis_algorithms` / `describe_qgis_algorithm` / `qgis_process` into the primary compute surface: live provider-catalog discovery + **semantic search** + a **curated allowlist** (categories, not 800 raw algos) + typed-param passthrough sourced from the algorithm's own metadata/help. Follows the `project_generic_endpoint_architecture` pattern (discover→categorize→run), applied to compute. Can prototype against the existing GCP worker; lands on 0308. **Acceptance:** the LLM, given a task, discovers an algorithm, reads its auto-doc'd params, runs it on the AWS worker, and the result returns as a `LayerURI`/handle — no bespoke wrapper authored.
- **job-0310 (infra/engine) — HEC-HMS headless solver container on AWS.** Container like SFINCS: HEC-HMS Linux distribution, compute via Jython script / CLI (`HEC-HMS.sh -s …`), S3 deck in/out, `run_hechms` + `wait_for_completion` solver tools (FR-CE handle contract). Independent infra; needed by 0311. **Acceptance:** a hand-staged HMS basin+control deck computes a hydrograph headless on AWS, artifacts to S3.
- **job-0311 (engine) — `model_hechms_scenario` composer.** QGIS Processing (GRASS `r.watershed` / SAGA / TauDEM) does basin + subbasin delineation and parameters (CN from NLCD+soils, lag/slope) → builds the HMS basin/met/control model → `run_hechms` solves → hydrograph + peak-flow results → render. Mirrors `model_flood_scenario`/SFINCS; reuses the flood-first + handle-resolution + MemoryFile-lifetime fixes (jobs 0303–0307). Needs 0308 (QGIS prep) + 0310 (HMS). **Acceptance:** deterministic on-box run produces a real hydrograph + peak flow for a named watershed; layer renders.
- **job-0312 (testing) — live acceptance.** Real watershed, end-to-end through the agent on the HTTPS site: prompt → QGIS-prep tools (called generically) → HMS solve → rendered result + narrated peak flow. Drives the live agent (no inject seams) per `feedback_playwright_must_drive_live_agent`; uses the tightened render detector. **Acceptance:** screenshot of the hydrograph/result on the map + truthful narration.
- **job-0313 (engine/cross) — deprecation pass.** Inventory hand-rolled atomic tools against the now-live QGIS Processing catalog; retire/redirect overlaps (e.g. `compute_slope`/`compute_aspect`/`compute_hillshade` → `gdal:slope` etc. where parity holds), keep bespoke only where no off-the-shelf algorithm exists. Net: smaller hand-maintained surface, larger documented catalog. Needs 0309. **Acceptance:** a documented retire/keep table + green suite after redirects.

**Schema touchpoints (user-landed):** Decision Q + engine-cloud-deployability criterion (§2.1/§2.3), QGIS-Processing-surface FR (§3 FR-AS/FR-CE), Appendix E posture revision, milestone row (§7), history row (§8) — all in the amendment proposal.

## Dependency graph

```
0308 (QGIS on AWS) ──┬──> 0309 (Processing adapter) ──> 0313 (deprecation)
                     └──> 0311 (model_hechms) <── 0310 (HEC-HMS solver)
                                   └──> 0312 (live acceptance)
```

## Sequencing

1. **0308 first** (spine; also closes the open render bugs).
2. **0309 + 0310 in parallel** once 0308's worker is live (0309 can prototype earlier against GCP).
3. **0311** when 0308+0310 land; **0312** verifies; **0313** folds in after 0309.

Each edge adversarial-verify gated. Orchestrator audits at closure. GCP teardown remains separately user-gated and is NOT part of this sprint.
