# Engine Coverage Levers -- Synthesized Implementation Plan

Date: 2026-06-23
Status: DESIGN (read-only spike). Input: reports/design/engine_coverage_audit.md (57 gaps, 6 cross-engine levers).
Goal: full coverage of every numerical engine's exposed inputs / algorithms / outputs ([[feedback_engine_full_coverage]]).
Engines: SWAN, SFINCS, PySWMM/SWMM5, MODFLOW 6 (GWF+GWT), GeoClaw, Landlab, OpenQuake.

This doc folds the three lever designs into ONE implementation-ready plan for an implement fleet:
which lever first, which engines first, the new shared modules, and the sequencing that avoids
colliding with the two in-flight streams.

---

## 0. The three levers, and how they compose (not compete)

| # | Lever | Bookend | What it generalizes |
|---|---|---|---|
| 1 | Generic Output-Quantity Publisher | OUTPUT (engine -> layers[]) | The just-landed publish_manifest: a declarative per-engine OutputQuantitySpec registry drives BOTH the offload worker AND the on-box postprocess. Adding a published quantity = ONE registry row, not a new postprocess_*.py branch. |
| 2 | Unified Forcing Abstraction | INPUT (fetcher -> deck) | The proven sfincs_forcing_adapter path: fetcher -> normalized field -> engine writer, plus a CI tripwire that fails the build on any contract forcing URI with a dead half-seam. |
| 3 | Steady/Transient + Physics toggle | RIDES the manifest | Two thin additive conventions: a uniform temporal_mode toggle + shared frames.py emit helper, and an advanced_physics overrides bag validated by a per-engine PHYSICS_REGISTRY. |

They are ONE family, not three subsystems:

- Lever 1 is the spine. Its on-box executor (publish_quantities.py) and worker executor
  (run_quantity_publish) both terminate in the SAME publish_manifest.json layers[] the existing
  agent registrar already consumes.
- Lever 3's frames.py IS the timeseries emit path Lever 1 calls (emit_timeseries_layers). A
  quantity registers reader -> the SAME helper, so 1 and 3 share an emit path rather than forking it.
- Lever 2 is the input bookend; it never touches layers[] or the COG/style path, so it cannot
  collide with Lever 1. publish_manifest stays output-only; the forcing registry is input-only.

The four shared design idioms they read as one family: (1) a declarative typed registry as single
source of truth; (2) a schema_version lockstep gate; (3) role+preset/quantity keys decoupling wire
format from consumer; (4) a plain-dict worker contract the worker build context (no packages/contracts)
can author.

---

## 1. Why Lever 1 first, and why the NON-SFINCS engines first

Recommended first lever: Lever 1 (Output-Quantity Publisher), specifically its on-box executor +
shared cog_io, migrating the NON-SFINCS engines first.

Rationale (validated against live code):

1. Highest gap-unblock count: the audit ranks it the single highest-leverage abstraction; it unblocks
   ~12 of the ranked backlog gaps (SWAN DIR/TM01/SETUP, SFINCS velocity/arrival, MODFLOW head +
   concentration-timeseries, GeoClaw eta/speed/fgmax, OpenQuake curves/UHS, Landlab
   drainage_area/slope/discharge).
2. Direct generalization of a JUST-LANDED contract (zero new subsystem). The manifest layers[]
   schema (services/workers/_raster_postprocess/manifest.py + packages/contracts/.../publish_manifest.py)
   and the agent REGISTRAR (register_published_manifest.register_manifest_layers /
   register_swan_wave_layers + publish_layer._TITILER_STYLE_REGISTRY + style_params_from_band_stats)
   are ALREADY quantity-agnostic -- verified: they consume layers[{name, role, style_preset, units,
   cog_uri, band_stats, metrics}] without caring what physical quantity produced them. The lock-in is
   entirely PRODUCER-side. The lever replaces the bespoke producers; it does NOT touch the registrar.
3. Confirmed zero-collision starting front. The first concrete work lives entirely in the five on-box
   postprocess_*.py files (swmm/modflow/geoclaw/landlab/openquake), which are touched by NEITHER
   in-flight stream (see section 4). The fleet can start immediately.
4. The struct already exists. sfincs_reader.FieldFrame already carries
   (role, name, dest_filename, style_preset, nodata_threshold_m, extra_metrics) -- that IS the
   per-frame projection of an OutputQuantitySpec, so the spec formalizes a struct already in the code.

---

## 2. New shared modules (the deliverables)

| Module | Side | Purpose | Source it consolidates |
|---|---|---|---|
| workflows/cog_io.py | agent | One COG-write / reproject-4326 / CRS-round-trip-guard / upload / bbox helper. | The near-identical _write_*_cog_4326 / _reproject_field_cog_4326 / _upload_cog* / _cog_bbox_4326 confirmed duplicated across all five on-box postprocess_*.py. |
| workflows/frames.py | agent | Owns peak-as-layers[0] + "<quantity> step N" naming + select_frame_time_indices + MAX_FLOOD_FRAMES + corrupt-frame-degrades-to-peak-only guard. | Lifted in-place from postprocess_flood.py (SFINCS keeps importing it). |
| (worker-importable) output_quantity.py | shared | OutputQuantitySpec dataclass + FieldResult union (RasterField/TimeseriesField/ScalarField) + per-engine OUTPUT_QUANTITIES registry + get_output_registry(engine). MUST live in a worker-importable plain-dataclass module (worker build context tars services/workers only -- it cannot import packages/contracts). | Mirrors the manifest.py / publish_manifest.py two-definitions-one-schema_version split. |
| workflows/publish_quantities.py | agent | On-box executor: walk registry -> run readers -> cog_io/frames -> build in-memory PublishManifest -> register_manifest_layers (the ONE registrar). Collapses the five on-box postprocess_*.py. | -- |
| services/workers/_raster_postprocess/postprocess.run_quantity_publish | worker | Worker executor: generalizes run_postprocess's depth/waves branch (lines 235-256) into "for spec in get_output_registry(engine)", reusing _encode_one/_build_tasks/_run_encode (bounded ProcessPool), band_stats, cog.write_field_cog, select_frame_time_indices. Same return type. | -- |
| workflows/physics_registry.py | agent | PHYSICS_REGISTRY (per-engine key -> type/range/default/deck_target) + validate_and_resolve_physics + applied_physics_delta. (Lever 3) | -- |
| workflows/forcing/{normalized,registry}.py | agent | ForcingHydrograph / ForcingGrid / ForcingLocations + ForcingBinding registry + materialize_forcing. (Lever 2) | Promotes sfincs_forcing_adapter.py internals. |

Contract additions (additive, in packages/contracts/src/grace2_contracts/common.py):
EngineRunArgsMixin with temporal_mode: TemporalMode = "steady" (default each engine to its CURRENT
behavior -- opt-IN, never silently 2-10x cost), output_frames: int = 24, and
advanced_physics: dict | None = None (None => byte-identical demo deck). SWAN keeps its mode field as
a back-compat alias property.

Manifest contract bump (LAST shared change): MANIFEST_SCHEMA_VERSION 1 -> 2, in LOCKSTEP in both
manifest.py and publish_manifest.py. ADDITIVE: quantity_key (provenance) per layer + top-level
physics_applied + temporal_mode. The agent reader is extra="ignore", so a v1 worker still parses and
an un-bumped agent ignores the new keys. Agent accepts {1, 2} for one release window so an un-rebuilt
worker degrades cleanly to the (now registry-driven) on-box path -- no behavior loss.

---

## 3. Implementation order

STEP 0 (pre-req, separate job, BLOCKS GeoClaw only): land the GeoClaw topo-handoff correctness fix
(audit item 1 -- a COG .tif is staged as topo.asc and declared topotype-3 but the worker entrypoint
does no conversion). Independent of all three levers; building GeoClaw eta/speed/fgmax on garbage topo
is wasted work. Does NOT block the other six engines.

STEP 1 (shared substrate, ONE job, NO engine behavior change): create cog_io.py + frames.py as pure
refactors. Dedupe the five on-box helpers into cog_io, preserving each per-engine nuance (MODFLOW RIV
positive=losing sign convention; SWMM max-total-depth-step vs per-cell max peak selection) as DECLARED
params, NOT flattened. Lift the frame machinery out of postprocess_flood.py (extract-in-place; SFINCS
keeps importing from frames.py). Land GREEN with all existing paths calling the new modules via thin
shims -- byte-identical layers[].

STEP 2 (spec type + executors + mixin, ONE schema+engine job): define OutputQuantitySpec + FieldResult
+ per-engine registry scaffold + get_output_registry. Add the agent on-box executor publish_quantities.py.
Add the EngineRunArgsMixin (temporal_mode/advanced_physics) and PHYSICS_REGISTRY at the same time
(additive). The SPEC dataclass goes in the worker-importable plain module.

STEP 3 (migrate the four non-SFINCS/non-SWAN engines, PARALLEL fan-out, one job each + SWMM), order by
leverage:
- MODFLOW: concentration_timeseries (all UCN steps -- data already saved, audit item 11) +
  head/water_table (one more .hds read, item 31) + seepage (existing). Worker rebuild.
- OpenQuake: hazard_curves + UHS (the scalar/chart emitter branch; classical run already computes them,
  it is a job.ini export + a CSV reader, item 5). Scope to scalar->metrics + the conversational-chart
  path; defer a true non-raster product table.
- Landlab: drainage_area/slope/relative_wetness/discharge/factor_of_safety (the component_chain already
  computes these grids, item 41 -- smallest per-quantity cost once the executor exists).
- GeoClaw: eta/surface (q[3]) + speed (from hu/hv,h, item 38) + fgmax (item 20). AFTER step 0.
- SWMM: flooding_losses + ponded_volume + conduit flow/velocity (item 4; Output API already open);
  generalize scatter_node_depths_to_grid to any NodeAttribute/LinkAttribute. Plus the OPTIONS physics
  merge (highest-value physics surface).
Each engine adds its additive _TITILER_STYLE_REGISTRY rows (the existing additive pattern) + the
advanced_physics merge into its existing deck-write seam. CI test: every SPEC.style_preset resolves in
_TITILER_STYLE_REGISTRY (a SPEC referencing an unregistered preset silently falls through to
percentile rescale -- functional but physically wrong colormap, e.g. wave DIR rendered viridis not
cyclic).

STEP 4 (GATE: after offload thin-out + SFINCS wave fix commit), migrate SFINCS + SWAN:
- SFINCS: convert sfincs_reader extract_depth/extract_waves into registry readers; the worker executor
  run_quantity_publish generalizes postprocess.run_postprocess's depth/waves branch. Add velocity (from
  native vmax -> the existing-but-None max_velocity_m_s contract field, audit item 2) + arrival_time.
  Lowest risk -- it already produces the manifest.
- SWAN: add the SWAN registry (hsign + DIR/TM01/TM02/DSPR/SETUP -- deck_builder already requests/allow-
  lists these, items 9, 50) + the worker executor so the SWAN worker FINALLY emits a manifest, flipping
  the dormant register_swan_wave_layers branch in model_wave_scenario live. WaveFieldLayerURI's four
  narration scalars become a spec metrics_fn so register_swan_wave_layers keeps working unchanged.
- Bump MANIFEST_SCHEMA_VERSION 1->2 atomically here (one window, grep the box per the SWMM off-box
  deploy lesson).

STEP 5 (Lever 2, Forcing -- DEAD LAST): promote sfincs_forcing_adapter.py internals into workflows/forcing/,
add ForcingGrid (the genuinely-new intermediate), close the three dead/half seams (SWAN wind writer,
GeoClaw surge writer, MODFLOW river-DEM sampler), and add the CI dead-seam tripwire
(test_every_forcing_uri_has_fetcher_and_writer + test_no_orphan_contract_uri with a curated
FORCING_URI_ALLOWLIST) into the packages/contracts/tests lane. Last because it refactors the only
working coastal-forcing path (Mexico Beach North Star) and touches Fortran-facing deck templates
(worker rebuild + live re-verify).

---

## 4. Sequencing vs the in-flight work (the load-bearing part)

Two in-flight streams own the collision surface:

A. The postprocess-offload THIN-OUT (worker->agent publish_manifest). State (verified): SFINCS-first
   is landing; SWAN is forward-ready -- model_wave_scenario already has the register_swan_wave_layers
   branch but the SWAN worker does NOT emit a manifest yet, so it ALWAYS falls back today. Touches:
   packages/contracts (publish_manifest mirror), model_flood_scenario + model_wave_scenario, the publish
   path (register_published_manifest + publish_layer + _TITILER_STYLE_REGISTRY), and
   services/workers/_raster_postprocess + sfincs_reader.py.

B. The SFINCS WAVE FIX, in run_swan.py + services/workers/swan/deck_builder.py (idla=3 / DEPMIN /
   bare-COMPUTE not "COMPUTE STATIONARY" / boundary-inward / SwanBathyCoverageError guard).

Verified-safe front: the five on-box postprocess_*.py (postprocess_{swmm,modflow,geoclaw,landlab,
openquake}.py) are touched by NEITHER stream.

Sequencing rules:

1. Build the shared substrate (cog_io.py + frames.py + OutputQuantitySpec/registry + agent
   publish_quantities) and migrate the five NON-SFINCS/NON-SWAN engines FIRST. These edits live
   entirely in the safe-front files + the additive contracts mixin + additive style-registry rows, so
   they run fully PARALLEL to both in-flight streams with no shared-file contention.

2. HARD GATE before touching SFINCS/SWAN producers: do not start the SFINCS worker reader conversion
   or the SWAN worker manifest emit until BOTH (A) has committed its
   sfincs_reader/postprocess/model_*_scenario/register_published_manifest changes AND (B) has committed
   its run_swan/deck_builder changes. Otherwise the fleet rebases on a moving manifest contract + a
   moving SWAN deck.

3. The SWAN registry work is the natural CONTINUATION of the offload thin-out (the offload made SWAN
   forward-ready; the publisher's worker executor is exactly what makes the SWAN worker emit the
   manifest that flips the dormant branch live). Coordinate it as the offload's next phase, not a
   competing edit.

4. The MANIFEST_SCHEMA_VERSION 1->2 bump is the LAST shared-contract change. Land it atomically in
   worker + agent in one window (grep the box to confirm deployed==HEAD per [[project_swmm_offbox_deploy_state]]),
   only after offload schema_version==1 is fully deployed. Agent accepts {1, 2} for one release.

5. Lever 2 (Forcing) is DEAD LAST -- it refactors the same sfincs_forcing_adapter the coastal North Star
   depends on and touches Fortran deck templates. Keep it off the critical path until the publisher
   fleet has drained.

---

## 5. Risks carried from the source designs (and mitigations)

- Worker cannot import packages/contracts (build context tars services/workers only). OutputQuantitySpec
  MUST live in a worker-importable plain-dataclass module; getting it wrong reintroduces the
  two-definitions drift the manifest split already solved. Mitigation: mirror the manifest.py /
  publish_manifest.py pattern exactly.
- Style-preset coupling: a SPEC referencing an unregistered _TITILER_STYLE_REGISTRY key silently
  renders a physically-wrong colormap. Mitigation: CI test asserting every SPEC.style_preset across all
  engine registries exists in the agent table.
- Non-raster quantities (OpenQuake curves/UHS, gauges, his/obs) do not fit cog/cog_timeseries cleanly.
  Mitigation: scope v1 to cog + cog_timeseries; route scalars to manifest metrics + the
  conversational-chart path; defer a true non-raster product table.
- On-box unification could regress five passing engine paths (bespoke CRS guards, sign conventions,
  peak-selection nuances). Mitigation: migrate one engine at a time behind the registry; keep the old
  postprocess_* as the fallback until the registry path is live-proven per engine.
- temporal_mode default discipline: a careless "transient" default silently 2-10x cost/output.
  Mitigation: default each engine's class-level temporal_mode to its CURRENT behavior; the #154
  granularity gate still governs frame count via MAX_FLOOD_FRAMES.
- advanced_physics is an LLM-filled open dict: without strict per-engine PHYSICS_REGISTRY validation it
  could no-op or produce wrong-but-status-ok layers (honesty-floor violation). Mitigation: typed,
  range-checked, unknown-key-rejected at compose time + the physics_applied manifest echo so the agent
  narrates exactly which non-default physics ran.
- ForcingGrid is genuinely new code (no exemplar); ERA5's current single-band time-mean COG must be
  re-fetched as a true hourly [t,y,x] stack or wind forcing stays physically flat even after the seam
  closes -- a silent-wrong-physics trap the all-dry guards will NOT catch. Mitigation: keep this in
  Lever 2 (last), with real-timestamp normalization (let each writer anchor; do not pre-anchor the
  SFINCS synthetic window onto GeoClaw/SWAN clocks).
- GeoClaw topo-handoff blocker is independent of all three levers and MUST precede any GeoClaw
  quantity/forcing work (STEP 0).
- CI orphan-URI test relies on a r"_(uri|file)$" heuristic + allowlist -- will false-positive on
  geometry/topo inputs (dem_uri, tsunami_dtopo_uri). Mitigation: curate FORCING_URI_ALLOWLIST
  deliberately or the test becomes noise that gets disabled.
