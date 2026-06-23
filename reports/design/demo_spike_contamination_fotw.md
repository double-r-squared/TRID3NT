# Demo Spike: Contamination-plume x Fields-of-the-World affected-field readout

Demo spike for a GRACE-2 demo that ties the MODFLOW-GWT groundwater-contaminant
plume engine to Fields of The World (FTW / fiboa) agricultural field boundaries:
place a contaminant spill, run the plume, intersect the plume against the
individual FTW farm-field polygons, and report WHICH fields are affected + HOW
MUCH (per-field affected area + peak concentration, ranked). This is a USEFUL
DEMO, not a North Star and not an engine go/no-go. It is grounded against the
live GRACE-2 seam: the existing run_modflow_job (MODFLOW 6 GWF + MF6-GWT)
PlumeLayerURI path, the published-vector fetch_field_boundaries (FTW / fiboa on
Source Cooperative), the compute_zonal_statistics vector-zone primitive, the
clip_vector_to_polygon / clip_raster_to_polygon clip primitives, and the
compute_impact_envelope composer pattern (the per-feature -> aggregate ->
narrative template this analysis mirrors).

ASCII only. No em/en dashes, no unicode arrows; "->" for arrows. Status: design
+ verdict only, no code in this doc.

---

## 0. Verdict

**BUILD - mostly composition of existing tools (roughly 90 percent).**

This demo is NOT an engine evaluation with a go/no-go gate; it is a packaging of
existing GRACE-2 capability plus ONE genuinely net-new analysis tool and ONE
thin workflow. Every heavy piece already exists and is proven:

- The plume engine is wired and proven GREEN: run_modflow_job (job-0227) builds
  a MODFLOW 6 GWF + MF6-GWT deck, runs mf6 (AWS Batch, or local mf6 when
  GRACE2_MODFLOW_LOCAL=1), and returns a typed PlumeLayerURI carrying an
  EPSG:4326 plume concentration COG (mg/L) plus max_concentration_mgl +
  plume_area_km2. (run_modflow_tool.py + workflows/run_modflow.py +
  workflows/postprocess_modflow.py.)
- The field-boundary fetch is wired and live-probed: fetch_field_boundaries
  returns FTW / fiboa agricultural field polygons as a FlatGeobuf vector layer
  for an AOI (each feature carries a crop_name property), with an HONEST
  FIELDS_NO_COVERAGE typed error outside the published regions.
- The plume-x-field intersection is exactly the EXISTING compute_zonal_statistics
  vector-zone path: value raster = the plume COG (mg/L), zone vector = the FTW
  field FlatGeobuf -> a per-polygon by_zone dict (keyed by each field feature's
  id) plus a whole-area aggregate. The clip primitives
  (clip_vector_to_polygon, clip_raster_to_polygon) scope inputs to the AOI.
- The "per-feature -> aggregate -> ranked narrative" shape is already proven by
  compute_impact_envelope (flood layer -> NSI inventory -> Pelicun -> per-
  structure -> ImpactEnvelope + narration). The affected-field analysis is the
  same shape with the plume COG in place of the flood COG and FTW fields in
  place of the structure inventory.

The data path has NO access blocker: MODFLOW runs on the existing Batch /
local-mf6 island; FTW / fiboa is anonymous public GeoParquet on Source
Cooperative (no key); zonal stats + clip are pure local rasterio / geopandas.

The real gaps - none fatal, each tied to a named seam, and each is the REASON
this is a BUILD-with-gaps rather than a pure reuse:

1. **No single "affected fields" analysis tool.** compute_zonal_statistics
   returns a generic by_zone dict keyed by feature id with no field labels, no
   ranking, no "affected vs not" threshold split, and no headline narration.
   The net-new piece is a thin analysis tool that runs the zonal stats of the
   plume concentration over each FTW field, joins the crop_name + a stable
   field id back onto each result, filters to fields whose peak concentration
   exceeds the plume detection threshold, RANKS them (by affected area or by
   peak concentration), and emits a per-field readout + a headline string. This
   is the analogue of postprocess_pelicun's aggregation step, scoped to fields.

2. **No workflow that scopes the spill up-gradient of a chosen field.** Today
   run_modflow_job takes a spill point directly; nothing places that point
   relative to a target field, and nothing chains plume -> fields -> analysis.
   The net-new workflow fetches the FTW fields for the AOI, picks (or accepts) a
   spill point up-gradient of the chosen field along the regional flow
   direction, runs the plume, then runs the affected-field analysis, and emits
   the ranked readout - mirroring model_groundwater_contamination_scenario
   (Case 2) for the plume half and compute_impact_envelope for the analysis
   half.

3. **The FTW REGIONAL-coverage constraint must be surfaced honestly.** FTW /
   fiboa is NOT global: the published corpus covers the contiguous US (USDA Crop
   Sequence Boundaries), Japan, and Denmark. fetch_field_boundaries already
   raises FIELDS_NO_COVERAGE outside those, and on-demand global field-boundary
   inference from satellite imagery is a SEPARATE, not-yet-built tool. The
   workflow must NOT silently fabricate fields outside coverage; the safe
   default demo AOI is US cropland (CSB), where coverage is the headline
   guarantee. Pick a US agricultural AOI (Iowa / Nebraska / Central Valley) so
   the demo never trips the no-coverage path.

If those three are accepted, this is a cheap, high-visibility composition demo:
it reuses the entire MODFLOW plume engine, the FTW fetch, the zonal-stats +
clip primitives, and the impact-envelope narration shape, and adds one analysis
tool plus one workflow. The ordered build plan is section 7.

---

## 1. The demo in one paragraph

A user says "model a contaminant spill near <some farmland area> and tell me
which farm fields it reaches and how badly." The agent (1) resolves the AOI to a
US cropland bbox (the FTW-covered default) and fetches the FTW / fiboa
agricultural field polygons for it via fetch_field_boundaries (each field a
polygon with a crop_name); (2) places the spill point - either an explicit
coordinate the user gives, or a point up-gradient of a chosen field along the
regional groundwater flow direction - and runs the MODFLOW 6 GWF + MF6-GWT plume
via run_modflow_job, which returns a PlumeLayerURI with an EPSG:4326
concentration COG (mg/L) + max_concentration_mgl + plume_area_km2; (3) intersects
the plume concentration COG against each FTW field polygon using
compute_zonal_statistics (value raster = plume COG, zone vector = FTW fields),
producing a per-field max + mean concentration and an affected-area count; (4)
runs the net-new affected-field analysis that joins crop_name + field id back on,
splits fields into affected (peak concentration above the plume detection
threshold) vs untouched, RANKS the affected fields by affected area (or peak
concentration), and emits a headline ("N fields affected, M.MM km2 of cropland
over the detection threshold, worst-hit field <id> (<crop>) at X.X mg/L"); and
(5) renders the plume COG and the FTW field boundaries together on the map, with
the affected fields highlighted. Every narrated number reads off the typed plume
fields + the deterministic zonal-stats output - never invented (Invariant 1).

Two HARD requirements, both already PASS:
- DATA ACCESS (PASS): MODFLOW runs on the existing Batch / local-mf6 island; FTW
  / fiboa is anonymous public GeoParquet (no key); zonal + clip are local.
- COVERAGE HONESTY (PASS, with care): fetch_field_boundaries already raises
  FIELDS_NO_COVERAGE outside the FTW regions, so the demo cannot fabricate
  fields; the workflow defaults the AOI into US cropland to stay inside coverage.

---

## 2. Data + engine sources

| Source | What it gives | Access / auth | Use in this demo |
|--------|---------------|---------------|------------------|
| MODFLOW 6 GWF + MF6-GWT (FloPy deck; run_modflow_job, job-0227) | Steady-state flow (CHD regional gradient + NPF + DIS) + transient conservative-tracer transport (ADV TVD + DSP + MST, SRC point mass-loading, GWFGWT + SSM) -> final-timestep concentration field -> EPSG:4326 plume COG (mg/L) + max_concentration_mgl + plume_area_km2. | AWS Batch (default) or local mf6 (GRACE2_MODFLOW_LOCAL=1). Confirmation-before-consequence gated by the server hook. | The plume itself: the value raster the fields are scored against. |
| Fields of The World / fiboa (fetch_field_boundaries) | Published agricultural field-boundary POLYGONS per region as cloud-native GeoParquet on Source Cooperative; bbox-pruned via GeoParquet 1.1 row-group pushdown; each feature carries a crop_name. Returns a FlatGeobuf vector LayerURI (renders inline). | Anonymous public HTTPS (no key). LIVE-PROBED 2026-06-17. | The field polygons: the zones the plume is scored over. |
| FTW coverage (REGIONAL, NOT global) | US (USDA Crop Sequence Boundaries; the headline coverage), Japan, Denmark. Outside these: honest FIELDS_NO_COVERAGE typed error. On-demand global inference from imagery is a SEPARATE not-built tool. | n/a | The constraint: default the demo AOI into US cropland; surface no-coverage honestly. |
| compute_zonal_statistics (vector-zone path) | Per-polygon stats of a value raster within each vector feature (rasterizes each polygon onto the value grid; numpy aggregation), returning by_zone keyed by feature id + a whole-area aggregate + units. | Local rasterio + numpy. Cacheable (dynamic-1h). | The plume-x-field intersection primitive: value = plume COG, zone = FTW fields. |
| clip_vector_to_polygon / clip_raster_to_polygon | Clip a vector / raster to an arbitrary polygon mask (CRS-aware), cached static-30d. | Local geopandas / rasterio. | Scope the FTW fields to a tight AOI, or clip the plume COG to the field set, before scoring. |
| compute_impact_envelope (pattern reference) | Deterministic chain hazard-layer -> inventory -> per-feature damage -> aggregate ImpactEnvelope + narration string. | n/a (composition). | The shape the affected-field analysis + workflow mirror (per-feature -> ranked aggregate -> headline). |

VERIFIED FTW US coverage bbox (WGS84, from the registered dataset):
(-124.736342, 24.521208, -66.945392, 49.382808). Any US-cropland AOI (Iowa /
Nebraska / Central Valley) is inside it; the docstring's worked example is Ames,
Iowa (-93.70, 42.00, -93.60, 42.08), which returned 247 field polygons on the
live probe - a good default demo AOI.

---

## 3. The intersection in detail (the load-bearing mechanic)

The plume is a single-band concentration COG in mg/L (EPSG:4326), where pixels
above the plume detection threshold define plume_area_km2. The FTW fields are
WGS84 polygons. "Which fields are affected and how much" is therefore exactly a
zonal-statistics-over-vector-zones question:

    value_raster_uri = PlumeLayerURI.uri            (the plume concentration COG)
    zone_input_uri   = fetch_field_boundaries(...).uri   (the FTW field FlatGeobuf)
    statistics       = ["max", "mean", "count"]

compute_zonal_statistics rasterizes each field polygon onto the plume grid and
returns by_zone[<field feature id>] = {max, mean, count} plus a whole-area
aggregate. The net-new analysis tool then:
1. Joins crop_name + a stable field id back onto each by_zone entry (read the
   FTW FlatGeobuf's feature ids + crop_name; the zonal-stats zone id defaults to
   the feature's id property or its sequential index, so the join key is the
   field feature id).
2. Splits fields into AFFECTED (by_zone[field].max >= plume detection threshold)
   vs untouched. The threshold is the SAME detection threshold the plume COG +
   plume_area_km2 use, threaded from the plume run (do NOT re-invent a cutoff).
3. Computes each affected field's affected AREA (the in-field pixel count above
   threshold x pixel area; use a thresholded stat or a clip-then-area step) and
   its PEAK concentration (by_zone[field].max).
4. RANKS the affected fields (default by affected area; allow by peak
   concentration) and emits the per-field readout + a headline string.

Two implementation notes for the analysis tool:
- The threshold split wants per-field "pixels above threshold" not just the raw
  max/mean. Two equivalent routes: (a) call compute_zonal_statistics with the
  field vector AND pass a thresholded view, or (b) clip the plume COG to each
  field with clip_raster_to_polygon then count above-threshold pixels. Route (a)
  reuses one tool call; prefer it. (compute_zonal_statistics's zone_threshold
  applies to RASTER zones, not vector zones, so the affected-area math lives in
  the analysis tool, reading max/mean/count + a thresholded pass.)
- CRS hygiene: the plume COG is EPSG:4326 and the FTW FlatGeobuf is EPSG:4326
  (fetch_field_boundaries reprojects to WGS84 on the way out), so no reprojection
  is needed at the join; the clip primitives reproject defensively if a future
  source is not WGS84.

---

## 4. Coverage: HAVE vs GAP

HAVE (reuse, little-to-no change):
- The MODFLOW 6 GWF + MF6-GWT plume engine end to end: run_modflow_job ->
  PlumeLayerURI{uri (concentration COG, mg/L), max_concentration_mgl,
  plume_area_km2} (run_modflow_tool.py, workflows/run_modflow.py,
  workflows/postprocess_modflow.py; gwt_adapter FROZEN under job-0221).
- The FTW / fiboa field-boundary fetch with honest no-coverage:
  fetch_field_boundaries (FTW_DATASETS registry, GeoParquet 1.1 bbox pushdown,
  crop_name property, FIELDS_NO_COVERAGE typed error).
- The plume-x-field intersection primitive: compute_zonal_statistics vector-zone
  path (by_zone keyed by feature id + aggregate + units).
- The clip primitives: clip_vector_to_polygon (scope fields to AOI),
  clip_raster_to_polygon (clip plume COG to field-set or per-field).
- The per-feature -> aggregate -> narrative composition shape +
  workflow_dispatch registration: compute_impact_envelope (the analysis tool +
  workflow template) and model_groundwater_contamination_scenario (Case 2 -
  ingest -> derive forcing -> confirm gate -> run_modflow_job -> narrate).
- The map render path: PlumeLayerURI renders as a COG via TiTiler;
  fetch_field_boundaries renders inline as a vector overlay - both already paint.
- The honesty + granularity norms: the solver-confirm gate already brackets
  run_modflow_job; an empty-plume / no-affected-field result must read honestly
  (render-chokepoint / honesty floor).

GAP (new code):
- The affected-field analysis tool: compute_zonal_statistics returns a generic
  by_zone dict with no crop labels, no affected/untouched threshold split, no
  ranking, and no headline narration. NET-NEW: a thin analysis tool (the
  postprocess_pelicun analogue) that runs the zonal stats of the plume over each
  FTW field, joins crop_name + field id, filters to affected fields (peak >=
  plume detection threshold), ranks them, and emits the per-field readout + a
  headline string.
- The thin scope-and-chain workflow: nothing chains FTW fields -> spill point
  up-gradient of a chosen field -> run_modflow_job -> affected-field analysis ->
  ranked readout. NET-NEW: a workflow that fetches the fields, places (or
  accepts) the spill point up-gradient of the chosen field along the regional
  flow direction, runs the plume, runs the analysis, and emits the readout.
- The coverage-honesty default: the workflow must default the AOI into US
  cropland (the FTW headline coverage) and surface FIELDS_NO_COVERAGE honestly
  rather than ever fabricating fields. (No new fetch code - just the
  default-AOI + no-coverage handling in the workflow.)
- Optional: an up-gradient spill-placement helper. The regional flow direction
  in the demo deck is set by the CHD gradient; placing the spill "up-gradient of
  field X" needs the flow direction + a chosen field centroid. v0.1 can accept
  an EXPLICIT spill coordinate (no helper) and treat up-gradient auto-placement
  as an optional convenience.

---

## 5. Integration seam (how the new pieces slot in)

The analysis tool is a hand-written Class-B composition tool (every existing
compute_* / postprocess_* is a hand-written Python module); the workflow is a
hand-written workflow_dispatch composer. CANONICAL ADD-A-TOOL recipe (mirroring
compute_zonal_statistics job-0083 + postprocess_pelicun + compute_impact_envelope):

1. NEW MODULE services/agent/src/grace2_agent/tools/analyze_affected_fields.py
   (or workflows/ if it is composer-shaped). Module docstring = inputs + numbered
   strategy + cache-key formula + OQ notes. Typed-error hierarchy (one base
   RuntimeError subclass with error_code + retryable, then a subclass per failure
   mode: input-invalid, fields-empty, plume-read, no-affected). Module-level
   _METADATA = AtomicToolMetadata(name='analyze_affected_fields',
   ttl_class='dynamic-1h', source_class='affected_fields', cacheable=True). Pure
   helpers in __all__ for tests. Decorate with @register_tool imported from
   grace2_agent.tools. Signature: plume_layer_uri (the PlumeLayerURI.uri),
   fields_layer_uri (the FTW FlatGeobuf.uri), detection_threshold_mgl (threaded
   from the plume run), rank_by ('area' | 'peak'). Internally call
   compute_zonal_statistics (via TOOL_REGISTRY['compute_zonal_statistics'].fn,
   never import directly) with statistics=['max','mean','count'], read the FTW
   FlatGeobuf's crop_name + ids, join, split affected vs untouched, compute
   per-field affected area, rank, format the headline.
2. REGISTER: add exactly one eager-import line at the bottom of
   services/agent/src/grace2_agent/tools/__init__.py near the zonal-stats line.
3. SEMANTIC DISCOVERY: add the tool name to categories.py _TOOL_CATEGORY
   (-> a hazard-analysis / agriculture category) and add a
   data/tool_query_corpus.yaml entry so discover_dataset routes "which fields
   does the plume reach" to it.
4. CREDENTIALS: none (MODFLOW + FTW + zonal are key-free in this chain).
5. WORKFLOW: NEW workflows/model_contamination_affected_fields.py (or fold into
   model_groundwater_contamination_scenario as a downstream branch). Template the
   front half off model_groundwater_contamination_scenario (derive spill params,
   confirm gate, run_modflow_job) and the analysis + narration off
   compute_impact_envelope. Dispatch atomic tools via TOOL_REGISTRY[name].fn.
   Default the AOI into US cropland; surface FIELDS_NO_COVERAGE honestly. STOP at
   the existing solver-confirm gate before the MODFLOW solve (granularity /
   confirm gate). Run any heavy synchronous step (zonal rasterize, plume read) in
   asyncio.to_thread (no-loop-blocking norm).
6. TESTS: unit-test the pure helpers (the crop_name/id join, the affected/
   untouched threshold split, the ranking) + a geographic-correctness assertion
   (a synthetic plume COG that covers exactly K of N synthetic field polygons ->
   K affected fields with the expected ranking; mirrors the zonal-stats
   vector-zone tests).

FILES TOUCHED: NEW tools/analyze_affected_fields.py, NEW
workflows/model_contamination_affected_fields.py; EDIT tools/__init__.py (+1
import line), EDIT categories.py (category slot), EDIT data/tool_query_corpus.yaml;
reuse run_modflow_job + fetch_field_boundaries + compute_zonal_statistics +
clip_vector_to_polygon + clip_raster_to_polygon + the TiTiler render path + the
inline-vector render path unchanged.

---

## 6. Gotchas (carried into the jobs)

- THRESHOLD CONSISTENCY: the affected/untouched split MUST use the SAME plume
  detection threshold that defines plume_area_km2 in postprocess_modflow - thread
  it through, do NOT invent a second cutoff, or "N fields affected" will
  disagree with "plume_area_km2".
- ZONE-ID JOIN: compute_zonal_statistics keys by_zone on the feature's id
  property if present, else the sequential index. The FTW FlatGeobuf keeps only
  geometry + crop_name (no explicit id), so the join key is the sequential
  feature index - read the FTW features in the SAME order in the analysis tool
  to align crop_name + geometry with the by_zone index, or write a stable id
  onto the FTW vector first.
- VECTOR ZONE_THRESHOLD: compute_zonal_statistics's zone_threshold parameter
  applies to RASTER zones only, not vector zones, so the per-field affected-area
  (pixels above threshold) math lives in the analysis tool, not in the
  zonal-stats call. Use max/mean/count for the split + a thresholded area pass.
- FTW REGIONAL COVERAGE: outside US / Japan / Denmark, fetch_field_boundaries
  raises FIELDS_NO_COVERAGE - the workflow must default the AOI into US cropland
  and surface that error honestly, never fabricate fields. On-demand global
  inference is a SEPARATE not-built tool; do not promise it.
- EMPTY / NO-AFFECTED HONESTY: a plume that intersects ZERO fields, or an AOI
  with coverage but no cropland, is a VALID 0-affected-field result, not a
  success-with-fake-numbers and not an error - read it honestly (the
  honesty-floor norm: a modeled envelope with empty content never reads
  status=ok with invented content).
- CRS: plume COG is EPSG:4326 and the FTW FlatGeobuf is EPSG:4326, so no
  reprojection at the join; keep the clip primitives' defensive reproject for
  any future non-WGS84 source.
- DEMO AQUIFER CAVEAT: run_modflow_job's aquifer_k_ms / porosity default to demo
  values - narrate them as demo defaults (the existing tool already says so);
  the affected-field readout inherits that caveat.
- UP-GRADIENT PLACEMENT: "up-gradient of field X" depends on the deck's CHD flow
  direction; v0.1 should accept an EXPLICIT spill coordinate (deterministic,
  testable) and treat auto-placement up-gradient of a chosen field as an
  optional convenience layered on later.
- NO-SYNC-BLOCKING: the zonal rasterize + plume read + FTW parquet read are
  CPU/IO-bound; run them in asyncio.to_thread so they do not stall the WS
  keepalive (the no-sync-blocking-on-the-asyncio-loop norm).

---

## 7. Ordered minimal-integration job list (build plan, smallest-first)

Each step is single-owner, tied to a file/seam, smallest-first.

1. **agent (S1): analyze_affected_fields analysis tool.** NEW
   tools/analyze_affected_fields.py: inputs plume_layer_uri + fields_layer_uri +
   detection_threshold_mgl + rank_by; internally call compute_zonal_statistics
   (value = plume COG, zone = FTW fields, statistics=[max,mean,count]); join
   crop_name + field id; split affected (peak >= threshold) vs untouched;
   compute per-field affected area; rank; emit per-field readout + headline.
   Typed errors + AtomicToolMetadata + pure helpers + @register_tool +
   one __init__.py import line. Mirror postprocess_pelicun's aggregation +
   compute_impact_envelope's narration. (Smallest; the load-bearing net-new
   piece; unit-testable against synthetic plume + field fixtures.)

2. **agent (S2): semantic discovery wiring.** EDIT categories.py (add
   analyze_affected_fields to a hazard-analysis / agriculture category) + EDIT
   data/tool_query_corpus.yaml (route "which farm fields does the plume reach" /
   "affected fields" to it). (Tiny; makes the tool discoverable.)

3. **agent (S3): model_contamination_affected_fields workflow.** NEW
   workflows/model_contamination_affected_fields.py (or a downstream branch of
   model_groundwater_contamination_scenario): default the AOI into US cropland;
   fetch_field_boundaries for the AOI (surface FIELDS_NO_COVERAGE honestly);
   accept an explicit spill point (or place it up-gradient of a chosen field);
   STOP at the solver-confirm gate; run_modflow_job; analyze_affected_fields;
   emit the ranked readout + render plume + fields. Template the front half off
   model_groundwater_contamination_scenario, the analysis + narration off
   compute_impact_envelope. Dispatch via TOOL_REGISTRY[name].fn; heavy steps in
   asyncio.to_thread.

4. **agent (S4, optional): up-gradient spill-placement helper.** A small helper
   that, given a chosen field centroid + the deck's regional flow direction,
   returns a spill point up-gradient of the field. Optional convenience; v0.1
   ships with explicit-coordinate placement and adds this only if "up-gradient of
   this field" auto-placement is wanted.

5. **testing (S5): live acceptance.** Drive the recreate prompt (section 9) end
   to end against a US-cropland AOI (e.g. Ames, Iowa): fetch FTW fields -> place
   spill -> run MODFLOW plume -> analyze affected fields -> assert the ranked
   readout lists the expected fields with per-field peak concentration + affected
   area, the headline "N fields affected, M km2 over threshold, worst-hit field X
   (<crop>) at C mg/L" cites the typed plume + zonal numbers, the plume COG +
   FTW fields both render, and an out-of-coverage AOI returns FIELDS_NO_COVERAGE
   honestly (no fabricated fields).

Critical path: S1 -> S2 -> S3 -> S5. S4 only if up-gradient auto-placement is
wanted.

---

## 8. Cloud / Batch + AI drivability

The plume half runs on the EXISTING MODFLOW island (AWS Batch by default, or
local mf6 with GRACE2_MODFLOW_LOCAL=1) - NO new compute island. The analysis half
(zonal stats + clip + join + rank) is pure local rasterio / geopandas / numpy on
the agent path, the same class as compute_zonal_statistics + compute_impact_-
envelope - no Batch. To honor the no-sync-blocking-on-the-asyncio-loop norm, the
zonal rasterize + plume read + FTW parquet read run in asyncio.to_thread. The
plume COG publishes through the always-on TiTiler box exactly like other COGs, so
the rendered result serves 24/7 even with the agent box asleep; the FTW fields
render inline as a vector overlay.

AI drivability: HIGH. The whole flow is "model a spill near <area> -> fetch the
farm fields -> intersect -> tell me which fields are affected", a sequence of
existing-shaped tool calls the model already composes for the Case 2 plume + the
impact-envelope analysis. The one user lever is the spill location + AOI +
detection threshold, surfaced through the existing solver-confirm / granularity
gate.

---

## 9. Recreate-this prompt (paste-ready, natural)

Paste this into a GRACE-2 chat to drive the demo:

  Model a groundwater contaminant spill near the farmland around Ames, Iowa -
  say a solvent leak that releases for a few hours - and then tell me which farm
  fields the plume actually reaches. Pull the agricultural field boundaries for
  the area, run the groundwater plume, and rank the affected fields by how much
  of each field is over the contamination threshold, with the peak concentration
  in each one. Show me the worst-hit fields and what crops they are.

  Show me the spill location and the area before you run the solver so I can
  adjust them.

A one-liner variant: "Model a contaminant spill near Ames, Iowa, pull the
Fields-of-the-World farm-field boundaries, run the MODFLOW plume, and tell me
which fields it reaches - ranked by affected area with each field's peak
concentration and crop."

(Natural intent, derived from our own endpoints - run_modflow_job +
fetch_field_boundaries + compute_zonal_statistics; no external record is forced.
Ames, Iowa is the FTW US-cropland AOI from the live probe, so the demo stays
inside FTW coverage. Swap in any US-cropland area - Nebraska, Central Valley -
and it still resolves; an out-of-coverage area honestly returns no fields.)

---

## 10. Cross-links

- MODFLOW state + the river-seepage neighborhood:
  [[project_modflow_river_seepage_demo]] (run_modflow_job GWF+GWT wired + proven;
  PlumeLayerURI{max_concentration_mgl, plume_area_km2}; spill-location user gate;
  the string-coordinate coercion fix job-0317). This affected-field demo is the
  point-source plume that already works, plus the field-intersection readout - it
  does NOT need the not-yet-built RIV/SFR river-seepage variant.
- Building-footprints / FTW source posture: FTW / fiboa is the analogue of the
  OSM-footprint lesson in [[project_building_footprints_source]] - published
  vectors over whole-US parquet pitfalls; FTW is regional, surfaced honestly.
- The intersection + clipping primitives + geographic-clipping pattern:
  [[feedback_geographic_clipping_pattern]] (clip to a polygon, not a bbox;
  clip_raster_to_polygon / clip_vector_to_polygon).
- The per-feature -> aggregate -> narration shape:
  [[project_pelicun_impact_postprocessor]] (compute_impact_envelope +
  postprocess_pelicun - the template the affected-field analysis mirrors).
- Render + honesty floor: [[project_render_chokepoint_and_honesty_floor]] +
  [[project_layer_visibility_contract]] (a modeled envelope with empty content
  never reads status=ok; the 5 layer-visibility conditions).
- Data-source honesty: [[feedback_data_source_fallback_norm]] (FTW
  FIELDS_NO_COVERAGE is the honest typed error; never fabricate fields).
- User-controlled granularity + spill-location gate:
  [[feedback_user_controlled_granularity]] (the solver-confirm / granularity gate
  brackets the MODFLOW run; spill location is a user lever).
- Execution + scale-to-zero context: [[project_execution_architecture_norm]] +
  [[project_scale_to_zero_island_architecture]] (MODFLOW on the existing Batch /
  local island; analysis on the agent path; TiTiler always-on render).
- Class-A vs Class-B tool paradigm: [[project_tool_integration_paradigm]]
  (analyze_affected_fields is a hand-written Class-B composition tool, like the
  other compute_* / postprocess_* modules).

---

## 11. Sources (in-repo seam, cross-checked this session)

- services/agent/src/grace2_agent/tools/run_modflow_tool.py (run_modflow_job ->
  PlumeLayerURI{uri, max_concentration_mgl, plume_area_km2}; Batch / local mf6).
- services/agent/src/grace2_agent/workflows/run_modflow.py +
  workflows/postprocess_modflow.py (deck build/stage + UCN concentration ->
  EPSG:4326 plume COG + plume_area_km2 above the detection threshold).
- services/agent/src/grace2_agent/workflows/model_groundwater_contamination_scenario.py
  (Case 2 composer: ingest -> derive forcing -> confirm gate -> run_modflow_job
  -> narrate; the workflow template for the plume half).
- services/agent/src/grace2_agent/tools/fetch_field_boundaries.py (FTW / fiboa
  field-boundary fetch; FTW_DATASETS registry; GeoParquet 1.1 bbox pushdown;
  crop_name; FIELDS_NO_COVERAGE typed error; US/Japan/Denmark coverage).
- services/agent/src/grace2_agent/tools/compute_zonal_statistics.py (vector-zone
  path: by_zone keyed by feature id + aggregate + units; the intersection
  primitive).
- services/agent/src/grace2_agent/tools/clip_vector_to_polygon.py +
  tools/clip_raster_to_polygon.py (scope the fields / clip the plume COG).
- services/agent/src/grace2_agent/workflows/compute_impact_envelope.py +
  tools/postprocess_pelicun.py (the per-feature -> aggregate -> narration
  template the affected-field analysis mirrors).
- packages/contracts/src/grace2_contracts/modflow_contracts.py (MODFLOWRunArgs +
  PlumeLayerURI{max_concentration_mgl, plume_area_km2}).
- reports/design/demo_spike_goes_fire_animation.md (the demo-spike doc format
  this mirrors).
