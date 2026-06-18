# PySWMM Urban-Flood North Star — Build Plan

**Authored 2026-06-17** from scoping workflow `wf_ccbc1972` (5 agents) + mesh-tool research
(`a924`) + the SWMM-engine verification (`wf_88798825-738`). Engine DECIDED by NATE: PySWMM
(EPA SWMM5), quasi-2D node-link mesh. SFINCS is the separate coastal demo, NOT this.

## What the demo is
A 100-year design-storm urban flood: animated time-stepped water depth that flows AROUND
building footprints, blocked by user-drawn RED walls and passed one-way through GREEN flap
gates, white-dashed bbox = event extent. Replicates the PCSWMM-style lecture demo, headless.

## The key architectural finding (why this is mostly composition, not new code)
The solver dispatch + AWS Batch lane is already engine-agnostic. A new engine plugs in with
ONE workflow + ONE worker entrypoint + ONE Batch job-def; everything else is reused:
- `run_solver` / `wait_for_completion` (tools/solver.py) already branch on backend and accept
  any `solver` string on the aws-batch / local lanes; all three poll loops, S3-completion
  contract, cancel, and IAM are engine-agnostic.
- The MODFLOW engine quintet (contract + tool + run_ + postprocess_ + builder) is the
  structural template — inherits the confirmation hook, typed-error envelopes, cancel chain,
  LayerURI→map rendering for free.
- The Phase-1 per-frame-COG → Wave-1 scrubber path (landed `505fedc`) consumes SWMM output
  unchanged once node depths are rasterized.

## Confirmed open mesh tooling (the "link, don't build" answer)
~70% link / 20% glue / 10% custom. `pysheds tools/swmm.py SwmmIngester` (GPLv3, headless)
already builds DEM-cell nodes + inverts + adjacency conduits + storage + `.inp` writer.
Alternatives: GisToSWMM5 (MIT), Generate_SWMM_inp (GPL-2.0 QGIS plugin via `qgis_process`),
swmm-api (MIT). SWMM 5.2 has a native quasi-2D wide-channel flag; flap gates are native
(`CONDUITS FlapGate`).

## Phased plan (P0 is a GO/NO-GO gate)

| Phase | What | Effort | Owner | Deps |
|---|---|---|---|---|
| **P0** | Quasi-2D mesh-gen + headless-run **SPIKE** (synthetic DEM, no data fetch) — proves swmm-api writes a valid quasi-2D `.inp`, pyswmm runs it stable, RED-wall blocks + GREEN-flap-gate is one-way, captures the perf anchor. **GO/NO-GO.** | M | engine | — |
| P1 | `swmm_contracts` + SCS Type-II design-storm **hyetograph** builder (replaces uniform-rate) | S | schema | P0 |
| P2 | **DEM→node-link mesh builder** (the core hand-built Class-B piece): storage nodes, overland conduits w/ NLCD Manning, DROP building cells, barrier snap (red=omit / green=flap), adaptive-mesh budget re-fit from P0, mass-balance honesty gate | L | engine | P0,P1 |
| P3 | `postprocess_swmm`: node depth → per-frame depth COGs in the IDENTICAL shape `postprocess_flood` returns → rides Phase-1 scrubber path unchanged | M | engine | P0,P2 |
| P4 | `model_urban_flood_swmm` workflow + `run_swmm_urban_flood` tool (LOCAL lane first — pyswmm bundles SWMM5, no container for dev) | L | engine | P1,P2,P3 |
| P5 | Walls + flap-gates **DRAW UI** (terra-draw LineString + per-segment wall/flap tagging; reuse white-dashed bbox + the region-choice dual-surface bus) | L | web | P6 |
| P6 | Spatial-input contract extension (`mode='barrier_line'` + FeatureCollection w/ per-segment props) + `request_spatial_input` tool + real server resolve handler (currently a noop scaffold) | M | schema | P0 |
| P7 | AWS Batch lane: **per-solver job-def routing** (the one hard blocker — Batch has only ever run SFINCS) + SWMM worker entrypoint + Dockerfile + job-def + ECR | M | infra | P0,P4 |
| P8 | **E2E live acceptance** — 100-yr demo on a real 1m-LiDAR AOI; both local + Batch lanes proven | M | testing | P4,P5,P6,P7 |

## Agent vs user split (per NATE)
- **Agent (auto):** DEM (3DEP 1m → 10m fallback), OSM footprints, Atlas-14 100-yr depth,
  SCS hyetograph, the quasi-2D mesh (drop building cells so water routes around them, adaptive
  resolution under a cell budget, drain outfalls), snap barrier tags to edges, run pyswmm
  (local/Batch), mass-balance gate, postprocess → frames, publish + narrate real scalars.
- **User:** define AOI/extent, draw the barrier line + tag each segment (red wall / green flap),
  set flap direction (protected side), choose interactivity mode (auto/medium/strict).

## Honest caveat (must surface in narration)
This is QUASI-2D, not true 2D SWE — a diffusive/dynamic-wave NETWORK approximation (what PCSWMM
2D does under the hood). Valid + standard for urban pluvial, but not momentum-conserving,
mesh-orientation sensitive ("staircasing"), resolution-dependent. The run is gated on the SWMM
flow-routing continuity (mass-balance) error and refuses to publish if it exceeds threshold, so
a shown result is at least mass-conservative. True 2D SWE is reserved for the SFINCS coastal demo.

## Core risks
- Batch per-solver job-def routing is a HARD blocker (P7) — SWMM is the first non-SFINCS Batch user.
- The DEM→node-link mesh generator is 100% net-new; quasi-2D pitfalls (double-routing, staircase,
  CFL stability) are only proven controllable by the P0 spike.
- SWMM perf model is unknown and CANNOT reuse SFINCS constants — needs the P0 timing anchor.
- 1m 3DEP LiDAR is sparse; the demo AOI must have confirmed 1m or the mesh can't resolve
  individual footprints (the core visual).
- swmm-api flap-gate kwarg + cleanest flap element (conduit-flag vs orifice vs outlet) unpinned
  until P0.

## Open decisions for NATE (defaults adopted where sensible)
1. **Demo AOI (for P8):** confirm an urban block with 1m 3DEP LiDAR. Default: I pick + verify
   coverage on a highway-adjacent block (matches the lecture's sound-barrier setting).
2. **SRS amendment:** "user-drawn AOIs/annotations" is currently out-of-scope (05-out-of-scope.md:13).
   A specialist will draft the one-line amendment; only NATE lands it.
3. **Drain default:** AOI-boundary cells = FREE/NORMAL outfalls (drain out the edge) with a
   sealed-bathtub flag. Default adopted: drain-by-default; override anytime.

## Cross-check vs canonical practice (reference `wf_495d379c`, 2026-06-17)
The pipeline-practice reference (`reports/flood_pipeline_reference.md`) CONFIRMS the quasi-2D
PySWMM approach is the standard, valid way to do urban-pluvial depth-around-buildings — it is
what PCSWMM does. It also surfaced concrete improvements + one architecture fork.

IMPROVEMENTS (fold into the phases; they sharpen the plan, no path change):
- **Design storm: Atlas-14 NESTED hyetograph, not SCS Type-II** (Type-II now considered
  archaic by many agencies). P1 default → Atlas-14 nested.
- **Building representation is a modeler-must-decide choice — surface it, don't bias it
  silently** (drop/hole vs raise +0.3 m vs high-roughness; each gives materially different
  extent/depth/velocity). P2: expose as an agent decision (default = drop/hole).
- **Add INFILTRATION** (Green-Ampt / Horton / SCS-CN from soils — `fetch_statsgo_soils` /
  `fetch_gcn250_curve_numbers`). Absent from the plan today; matters for a 100-yr pluvial
  event (pervious vs impervious). New P2 sub-step.
- **Add a VALIDATION stage** — the reference's credibility gate AND the literal North-Star
  "computed-vs-observed" deliverable, SHARED with the coastal demo: gauge-hydrograph
  (NSE/RMSE/KGE), HWM scatter, extent-CSI-vs-SAR; fold the metric into the envelope so the
  render-honesty floor can read it.
- **Datum-aware legend labels**: "flood depth [m]" (ground-ref) vs "water level [m − NAVD88]"
  (datum-ref). Cheap, high-credibility; both demos.
- **Build the depth grid at fine-DEM resolution** in postprocess (resample WSE onto the fine
  DEM before subtracting bed) — P3.

ARCHITECTURE FORK (NATE's call — surfaced because catching sub-optimal paths was the research's
whole job):
- **Path A (recommended; current plan): pure PySWMM quasi-2D.** Build the DEM→node-link mesh;
  one engine; matches the PCSWMM lecture; honors "no SFINCS." Quasi-2D = the "budget but
  standard" option. P0 spike (running) proves it.
- **Path B: SWMM-1D pipes + SFINCS-2D overland (dual-drainage).** Reuse the EXISTING SFINCS
  rain-on-grid engine for the 2D depth field; SWMM for pipes + flap gates. The reference's
  "more optimal" true-2D path AND it avoids building the mesh generator — but reintroduces
  SFINCS into the urban demo (which NATE ruled out). NATE's demo screenshot (2026-06-17,
  `reports/pyswmm_urban_northstar_reference.png`; caption confirms "2D capabilities of PCSWMM",
  building obstruction layer, sound barrier with partial flap gates) CONFIRMS the target IS
  PCSWMM quasi-2D -> **Path A CONFIRMED**; Path B noted as future-only, not chosen.
- P0's SWMM-headless + flap-gate + swmm-api findings are needed for BOTH paths, so the spike
  is not wasted either way.

BONUS forward-notes from the critique (not urban-blocking, captured so they aren't lost):
- **Idaho Demo Case 3 needs rain-on-snow** — Atlas-14 → hyetograph silently assumes liquid
  precip; mountain-West design events are often rain-on-snow (SNODAS/SNOTEL + melt). Revisit
  before that demo.
- **Coastal low-confidence claims to primary-source before the SFINCS build**: SnapWave
  "XBeach-quality R²~0.96" and SFINCS-SSWE validity for supercritical/dam-break are research
  claims, not settled — verify in the coastal spike.

## PCSWMM fidelity note (video summary, 2026-06-17)
PCSWMM v20 "2D" = Dynamic 1D analysis INTEGRATED with a 2D overland mesh (dual-drainage).
Our quasi-2D node-link mesh IS the 2D-overland part (solved by SWMM's dynamic-wave engine).
The buried 1D storm-sewer network is the one OPTIONAL piece — a natural PURE-SWMM add
(couple a 1D pipe network to surface cells via inlets), NOT a new engine. v0.1 =
2D-overland-only reproduces the screenshot visual; the 1D network is a fast-follow fidelity
add (needs pipe data: user-supplied or OSM/inferred) — surface the choice, default to
2D-overland-only for the first demo. "Partial flap gates" confirmed = the red-wall/green-flap
mapping. Variable-size mesh = our adaptive budget. PCSWMM→Google-Earth animation; ours = web
map + per-frame COG scrubber. GENERALIZES (PCSWMM "advanced applications") to flood-hazard
mapping, river overbank, levee zones, and rural ponding — the urban demo is the beachhead for
a broad PySWMM flood capability.
