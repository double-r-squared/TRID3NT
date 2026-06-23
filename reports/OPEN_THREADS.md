# GRACE-2 — Open Threads (living backlog)

Single index of every live thread so none is orphaned. Maintained by the orchestrator;
updated as threads open/close. Status: ACTIVE (in flight) | QUEUED (approved, not started) |
BLOCKED | IDEATING (not yet scoped) | DONE (kept briefly for traceability, then pruned).

Last updated: 2026-06-23.

## ACTIVE (in flight this session) — 2026-06-23 fleet, 3 background agents

- **[Focus 1: SFINCS North Star] SnapWave empty/static wave root-cause** — agent `w1wvvm3tc` RUNNING.
  Two symptoms to separate: (a) frames identical across time, (b) ~0.8% sparse field. On report ->
  launch wave-FIX agent (SFINCS worker sfincs_reader wave var + deck/forcing). NOT green-lit until BOTH
  the field is covered AND it animates. See [[project_sfincs_snapwave_not_ready]].
- **[NATE complaint] 3D mode rework** — agent `af548fb1` RUNNING (worktree). Current 3D = hillshade only
  because applyTerrain3d never pitches the camera. Fix: P1 auto-pitch + dramatic terrain (v4, must-land),
  P2 MapLibre v5 + globe projection (terra-draw adapter peerDep >=4 so v5 ok). Commit, NOT deploy; I
  review + merge + deploy + NATE live-verifies.
- **[Focus 2: Engine full-coverage levers] foundation STEP 1-2** — agent `a9191a0f` RUNNING (worktree).
  Shared cog_io.py + frames.py (pure refactor) + OutputQuantitySpec registry + publish_quantities agent
  executor + EngineRunArgsMixin (temporal_mode/advanced_physics) + physics_registry. Plan:
  reports/design/engine_coverage_levers_plan.md. On land -> STEP 3 fan-out (5 non-SFINCS engines).

## DEPLOYED / COMMITTED THIS SESSION (prune after verify)
- **Mobile polish-2 batch** — 946d263, web DEPLOYED (S3 + CF invalidation I6AGMYV4OONMT9KFAG25LMMKTK).
  ErrorBoundary (white-screen fix), vertical legend + bottom-X, single bbox, scrubber contained/hidden
  over drawer. Plus bugs 1-3 (26464af) shipped in the same deploy. NATE to live-verify on 390x844.
- **Offload phase-4 (agent register-only)** — 5cc7faa committed, HELD from deploy. Agent reads worker
  publish_manifest -> register-only with clean fallback. Activation (worker rebuild + agent redeploy)
  BATCHED with the SnapWave wave fix so the SFINCS worker rebuilds ONCE.

## QUEUED (approved, not started)
- **polish-3 mobile batch** — per-frame-scan-suppression, drop cases-list gradient, peak-off-on-auto-
  animate, hide-group-hides-all-frames + hide-scrubber. GATED behind the 3D agent merge (both touch
  App.tsx/Map.tsx) to avoid a web merge conflict; do on main after 3D lands.
- **Engine-levers STEP 3** — 5 non-SFINCS engine migrations (MODFLOW/OpenQuake/Landlab/GeoClaw/SWMM).
  GATED on the foundation (a9191a0f) landing. STEP 0 (GeoClaw topo-handoff fix) precedes GeoClaw only.
- **Engine-levers STEP 4** — SFINCS + SWAN producers + MANIFEST_SCHEMA_VERSION 1->2. GATED on BOTH the
  offload thin-out (done) AND the SFINCS wave fix committing.
- **Box downsize to t3.small** — after offload activation removes on-box postprocess RAM bursts.
- **Copy SWAN + SFINCS demo cases into NATE's account** (ULID 01KV9HCACDXMRE7D976XDA55BZ) — DynamoDB
  copy (new _id, re-own, not transfer). Gated on SFINCS layer landing. SWAN case ready: 01KVSSNXAFCT3EQ6F69W4D1EV5.
- **SWAN viz set** (NATE approved all 3, 2026-06-23) — (1) DIR direction arrows/streamlines (couples to
  deck.gl wave #169), (2) nonstationary Hs animation (temporal scrubber), (3) RTP/Tp companion raster.

## QUEUED (approved, not started)
- **Copy SWAN + SFINCS demo cases into NATE's account** (ULID 01KV9HCACDXMRE7D976XDA55BZ) — DynamoDB
  copy (new _id, re-own, not transfer). Gated on SFINCS layer landing. SWAN case ready: 01KVSSNXAFCT3EQ6F69W4D1EV5.
- **SWAN viz set** (NATE approved all 3, 2026-06-23) — (1) DIR direction arrows/streamlines (couples to
  deck.gl wave #169), (2) nonstationary Hs animation (temporal scrubber), (3) RTP/Tp companion raster.
  ONE coherent engine+web pass (all touch postprocess_swan.py; do not parallelize across that file).
- **Engine FULL-COVERAGE audit** (NATE goal 2026-06-23, [[feedback_engine_full_coverage]]) — coverage
  matrix per wired engine (SWAN/SFINCS/PySWMM/MODFLOW/GeoClaw/Landlab/OpenQuake): exposed surface vs
  wired, gap-ranked. Output reports/design/engine_coverage_audit.md. SWAN viz set is phase 1 of this.
- **Profile tool + digitizer QGIS plugins** — high-priority wraps (subset of the plugin-wrapping research).
- **Tool-retrieval RAG** (NATE 2026-06-23) — tool list is ~126 (past 100); the full catalog in every
  prompt bloats context + dilutes selection accuracy. OPEN this thread when SFINCS+SnapWave demo lands.
  See [[project_tool_retrieval_rag_for_local_models]] (prior stance: RAG is for the OFFLINE build; for
  cloud-Bedrock the cachePoint handles COST, selection accuracy is the real lever -- revisit with that).
- **Mesh visibility (all run types incl pluvial) + granularity SLIDER** (finer floor than the chips) —
  Central Park run was too coarse. Queued from 2026-06-23.
- **Contamination-plume tool clean redo** — original worktree a43b2858 uncommitted/untested/stale; redo on main.
- **Engine-test cluster** — OpenQuake -> Landlab -> GeoClaw -> MODFLOW-seepage live drives.
- **AI/ML model-zoo build tracks** — canopy-height (CPU Batch), DeepForest (CPU), then ONE GPU Batch CE
  for SamGeo/Prithvi/footprints; OPERA (_earthdata + DSWx); see [[project_geospatial_ai_model_zoo]].
- **EurOtop overtopping** — mean/design toggle + tolerable-discharge hazard classification
  (research done, reports/references/eurotop_overtopping_research.md).
- **3D mode** — bundled with deck.gl wave #169.
- **Fire dual-product blend + temporal harmonization/interpolation** — minimal blend done; the generic
  align+interpolate-heterogeneous-timeseries pattern is the differentiator ([[project_timeseries_harmonize_blend]]).

## IDEATING (NATE floated, not yet scoped)
- **Wrap-popular-QGIS-plugins-as-tools PATTERN** — thin-logic plugins -> python-shim agent tool; heavy-UI
  plugins -> harder/defer; exercises QGIS-server/plugin path (ties job-0308). Rubric from the research Workflow.
- **grep/read/glob/edit generalist agent tools** — NATE's question; likely yes for a sandbox/file tool tier.
- **Codebase modularization vs throughput** — design answered 2026-06-23 (modularize along ownership seams +
  append-only registries for shared hubs + worktree isolation + scope-time dependency mapping).

## DONE (recent, prune after a few sessions)
- **SWAN nearshore wave engine** — 5 stacked bugs fixed; live-verified raster paints (COG 46.56% valid).
  worker grace2-swan:5, agent deployed. Commits 8d25dc8 + 772c5ef on main.
- **Tool-card render hardening (web-side)** — landed (pipeline_emitter resilience + PipelineCard labels).
  NOTE: NATE recalled "tool card visibility?" 2026-06-23 — verify nothing else outstanding here.
