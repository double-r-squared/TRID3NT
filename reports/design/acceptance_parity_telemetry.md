# Acceptance-Parity Live Telemetry

Design: make a live solve NATE drives surface the SAME run-level data the
`verify_*` acceptance drivers collect (run_id, layer count, the per-engine
output metric, solver status) PLUS the worker diagnostic signals that
root-caused real bugs (snapwave wavebnd, nodata sentinels, deck cell/refinement
counts, make-exit). Two delivery phases: Phase A is agent+web only and deploys
without a worker rebuild; Phase B rides the next worker image rebuild.

Status: design only. No code in this doc. Grounded against the 3 read-only
inventories and re-verified against live source (line refs below were spot-checked).

ASCII only. No em/en dashes, no unicode arrows.

---

## 0. Problem statement (the parity gap)

The 7 acceptance drivers each emit a clean one-liner:

```
READINESS_RESULT <engine> PASS run_id=<id> layers=<n> metric=<name>=<value>
```

(geoclaw verify_readiness_geoclaw.py:180, swmm :192, openquake :179, landlab
:180, river_seepage :186, pluvial :204; waves uses a free-form PASS/FAIL block,
verify_mexico_beach_waves.py:420).

A user driving the SAME scenario through the live agent sees NONE of that as a
structured readout. Confirmed three-tier reality:

1. Sim DIMENSIONS reach the UI today (grid_resolution_m / active_cell_count /
   vcpus / elapsed / eta) via the running PipelineCard solve readout
   (web/src/components/PipelineCard.tsx:119-138, rendered :1293-1310) and the
   Settings -> Tools solve_telemetry table (RoutingQualityDashboard.tsx:120-135).
   These are sim INPUTS, never the output metric.
2. Batch lifecycle STATUS reaches the UI (compute-card chip, verbatim
   DescribeJobs, mint_dispatch_and_sim_cards pipeline_emitter.py:2054-2107).
3. The acceptance OUTPUT METRIC reaches the UI for NO engine as a structured
   field. FLOOD metrics are typed onto the envelope (FloodMetrics on
   AssessmentEnvelope, envelope.py:153-183) but collapsed to a bare LayerURI in
   model_flood_scenario.py before the LLM/web ever see them; the happy-path
   summary (_summarize_published_scenario_layer adapter.py:1738-1782) drops every
   number. The other 5 engines stamp their metric on a typed LayerURI subclass
   that gets down-converted to a ProjectLayerSummary
   (pipeline_emitter.add_loaded_layer:1469-1494) carrying ONLY render metadata.
   There is NO FloodMetrics / AssessmentEnvelope type in web/src/contracts.ts at
   all (grep: zero hits, re-verified). The numbers survive only into chat
   narration prose.
4. Worker diagnostic SIGNALS (snapwave wavebnd, deck cell counts, sentinel masks,
   make-exit) live ONLY in CloudWatch LOG lines. The agent never calls
   get_log_events (grep across services/agent/src: zero hits); solver.py only
   calls batch.describe_jobs for control-plane STATUS (:2106-2207). No worker log
   signal can reach the UI by any path today.

The fix has a clean precedent. impact-envelope, chart-emission, and
code-exec-result are ALREADY emitted as extra session-scoped WS envelopes
ALONGSIDE the function_response (server.py:2122/2181/2191;
_maybe_emit_impact_envelope:7650-7700), routed in ws.ts (:1600-1658) to a panel.
We add ONE analogous envelope for solver metrics + diagnostics. The pattern is
proven; it is simply absent for solvers.

---

## 1. The exact acceptance METRICS to surface per engine, and where each already exists

The unifying wire type is a new `solve-result` envelope (section 3). The KEY
fact for build cost: 6 of 7 metrics ALREADY EXIST server-side post-run -- 1 on
the client envelope, 4 on a typed LayerURI subclass the agent holds in-process,
1 in a separate postprocess dict. None needs new computation in Phase A; only
landlab's metric also already lives in completion.json.

| Engine | Acceptance metric(s) | Where it exists post-run TODAY | Phase |
|---|---|---|---|
| PLUVIAL (flood) | max_depth_m, mean_depth_m, p95_depth_m, flooded_area_km2 | envelope.flood.metrics (FloodMetrics, envelope.py:153-183), built model_flood_scenario.py:2777-2789 from postprocess_flood.py:611-655. On the wire envelope but stripped before LLM/web. | A |
| COASTAL depth (waves) | max_depth_m + same flood quad | Same FloodMetrics path as pluvial. | A |
| COASTAL WAVES (peak hm0) | peak wave height (hm0 max over time), wave_frames count | postprocess_waves.py metrics dict under the REUSED key max_depth_m (:266,328); NOT threaded onto flood.metrics; wave layers emitted out-of-band. Acceptance driver only gets it by calling postprocess_waves DIRECTLY. | A (agent threads it) |
| GEOCLAW | max_depth_m, flooded_area_km2, scenario | GeoClawDepthLayerURI.max_depth_m/flooded_area_km2 (geoclaw_contracts.py; computed postprocess_geoclaw.py:288-306). On the typed LayerURI the agent returns; stripped by add_loaded_layer + _summarize_published_scenario_layer. | A |
| RIVER-SEEPAGE / MODFLOW | river_cell_count (primary), total_leakage_m3_day, gaining/losing_m3_day | SeepageLayerURI.* (modflow_contracts.py:192-224; computed compute_seepage_metrics postprocess_modflow.py:153-183). On the typed LayerURI. | A |
| OPENQUAKE | max_hazard_value (primary), n_sites, imt, return_period_years, units, hazard_area_km2 | SeismicHazardLayerURI.* (openquake_contracts.py; computed postprocess_openquake.py:236-259). On the typed LayerURI. | A |
| LANDLAB | unstable_area_fraction (primary), min_factor_of_safety, mean_probability_of_failure | LandlabSusceptibilityLayerURI.* (landlab_contracts.py:213-239; computed postprocess_landlab.py:108-154). ALSO already in completion.json result block (landlab/entrypoint.py:351-359, written :391-415). | A |
| SWMM | max_depth_m (primary), flooded_area_km2, n_buildings_affected | SWMMDepthLayerURI.* (swmm_contracts.py:271-301; computed postprocess_swmm.py:189-222). On the typed LayerURI. | A |

Universal fields on every `solve-result` (mirror the READINESS_RESULT line):

- run_id -- on the LayerURI as `layer.layer_id.rsplit('-',1)[-1]` (the exact
  source every driver uses, e.g. geoclaw:159, swmm:172) OR from
  solver_run_ids[-1] for the flood envelope path (pluvial:185).
- layers (n) -- count of layers the scenario published (1 by construction for
  all non-flood engines; len(layers) for flood).
- solver status -- PASS/FAIL parity: PASS iff a renderable layer + finite metric
  in range (each driver's gate, e.g. landlab finite + in [0,1] :170, openquake
  finite >=0 :169, river_seepage river_cell_count >=1 :171). The agent already
  knows the layer published successfully; the metric-range gate is a cheap
  re-check inside the scenario composer.
- solver_version -- flood carries it on FloodMetrics.solver_version
  (envelope.py); for the others it is the engine name + image tag (the scenario
  composer knows the job_definition). Optional; null where unknown.
- backend -- "aws_batch" vs local lane (already recorded in solve_telemetry,
  e.g. model_dambreak_geoclaw_scenario.py:144-173).

KEY POINT: the agent already HAS the typed LayerURI subclass in-process at the
end of every scenario composer (it is the return value before
add_loaded_layer strips it). Phase A is "read the metric fields off the object
you already have and emit them as a side envelope" -- NOT a new computation and
NOT a worker change. The lone subtlety is WAVES: the peak hm0 is in a separate
postprocess_waves dict; the live coastal composer must thread that scalar into
the solve-result the way the driver does by calling postprocess_waves, OR the
composer captures the already-computed wave metrics dict (it runs postprocess_
waves to emit the wave frames) and reads max_depth_m from it. No new compute.

---

## 2. Worker DIAGNOSTIC SIGNALS, split Phase A vs Phase B

Two distinct sources of worker signal, distinguished by deployability:

### Phase A diagnostics -- already in completion.json on S3, agent reads them, NO worker rebuild

These ride the `solve-result` envelope's `diagnostics` block. The agent already
fetches completion.json from S3 in wait_for_completion; Phase A just reads more
fields out of the dict it already has.

- MODFLOW: `converged` bool + the convergence note (entrypoint.py:333-357;
  _check_convergence:283-301 -> note flows into completion.json `error`;
  exit_code overridden to 2 on divergence). This is a real root-cause signal
  ("solver_diverged" / "mfsim.lst absent") and it is ALREADY on S3.
- LANDLAB: full result block (unstable_area_fraction / min_factor_of_safety /
  mean_probability_of_failure / resolution_m / grid_crs) -- the ONE worker that
  already surfaces its output metric in completion.json (entrypoint.py:391-415).
- SFINCS deckbuilder DECK block (provenance): nr_cells, nr_levels, budget_notes
  (budget cap / refinement disabled / nr_cells exceeds budget), and the mesh
  sub-block (n_total_cells / n_active_cells / n_features / decimated / empty).
  All in provenance -> completion.json `deck` (entrypoint.py:1347-1348,
  1779-1816, written :1910-1940). These are the deck cell/refinement counts NATE
  wants and they are ALREADY agent-readable.
- ALL workers: status / exit_code / output_uris / stdout_uri / stderr_uri /
  finished_at / error. The stdout/stderr URIs let the agent (and a future
  expander) deep-link the raw worker logs without a CloudWatch fetch path.

### Phase B diagnostics -- CloudWatch-ONLY today, need a completion.json addition + the next image rebuild

These are the signals that root-caused bugs but live ONLY as LOG.info/LOG.warning
lines. Surfacing them requires writing them into provenance/completion.json
inside the worker, which means a container rebuild. Spec the additions so they
ride the NEXT scheduled openquake/geoclaw/sfincs rebuild, not a special one.

- SFINCS deckbuilder snapwave mask counts: `active / wavebnd / inactive`
  (entrypoint.py:1455-1458). The literal "snapwave mask: active=.. wavebnd=0"
  signal. CHEAPEST high-value change: the values are already computed in-scope as
  locals (sw_active / sw_wavebnd / sw_inactive); it is a pure "put into the
  provenance dict" edit at ~:1458, no new computation.
- SFINCS deckbuilder seaward-boundary REPAIR outcome: the "snapwave mask
  REPAIRED via derived seaward boundary" log + the post-repair tuple + the
  "STILL empty after seaward derive" / "could not be derived" warnings
  (:1479-1494). Record a `snapwave_repair` enum (none / repaired / repair_failed)
  + the post-repair wavebnd.
- SFINCS deckbuilder "no SnapWave boundary points in spec -- deck has no wave
  forcing" (:1532). Record a `snapwave_boundary_present` bool.
- SFINCS deckbuilder sfincs mask counts: `active / wlbnd / inactive` (:1421-1424).
- SFINCS deckbuilder nodata/fill-sentinel masking: _mask_topobathy_sentinels
  masks 9999/-9999/1e20/float32-max to NaN; "topobathy has no valid pixels"
  warning at :868. The COUNT of masked cells is computed NOWHERE today -- Phase B
  must both compute the count AND record it (a true worker change, not just a
  dict insert).
- GEOCLAW make-exit / maketopo failures: "maketopo.py failed rc=%d", the
  "No rule to make target .output" failure mode, and the geoclaw exit/byte line
  (geoclaw/entrypoint.py ~237/246/266-267). Record a `make_exit` int +
  `maketopo_rc` int.
- OPENQUAKE / SWMM raw exit + stdout/stderr byte lines (openquake :276-277,
  swmm :279-280). Lowest value; include only if the rebuild is already happening.

Phase B spec for each worker's `_write_completion`: add a `diagnostics` sub-dict
(NOT new top-level fields -- keeps the completion.json shape byte-compatible for
older agents that ignore unknown keys). For SFINCS deckbuilder this is the bulk
of the value and the lowest cost (locals already in scope). Default every field
so an old worker image that has not added them yet produces a `solve-result`
with `diagnostics` partially null -- the web renders "n/a" rather than breaking.

CONTRAST -- what is NOT a worker change: the acceptance OUTPUT metrics for
flood / wave / river_cell_count / seismic max_hazard are computed in AGENT
postprocess from the output rasters/NetCDF, NOT in any worker (grep of worker
entrypoints for max_depth/hm0/zsmax returns only comment mentions). So those
metrics are agent-side data that already exists post-run; surfacing them is the
Phase A agent/web change, NOT a worker rebuild. Landlab is the lone engine whose
output metric is ALSO already in completion.json.

---

## 3. UI placement

Reuse the existing surfaces; add no new top-level chrome.

### 3a. The `solve-result` envelope (new, mirrors impact-envelope exactly)

- Contract: new `SolveResultPayload` in packages/contracts/src/grace2_contracts/
  ws.py (next to SolveProgressPayload:549 and ToolIoPayload:583). Fields:
  `run_id, solver, layers, status (pass|fail), solver_version|null, backend,
  metric_name, metric_value, metrics{...full per-engine dict...},
  diagnostics{converged|null, deck{nr_cells,nr_levels,budget_notes,mesh{...}},
  snapwave{active,wavebnd,inactive,boundary_present,repair}|null, ...},
  stdout_uri|null, stderr_uri|null}`. Every field defaulted/nullable so a partial
  Phase-A emit (no Phase-B diagnostics yet) is valid.
- Mirror type in web/src/contracts.ts (the file that has ZERO metric types today).
- Add "solve-result" to the SESSION_SCOPED_TYPES list in ws.ts (alongside
  impact-envelope :545, chart-emission :548, solve-progress :558) so it fans out
  to App.tsx's socket like the others -- the card it enriches can be on either
  wire (same rationale the solve-progress comment already states at ws.ts:557).
- Emitted server-side IN ADDITION to the function_response, via a new
  `_maybe_emit_solve_result` helper modeled byte-for-byte on
  `_maybe_emit_impact_envelope` (server.py:7650-7700), called at the same
  post-tool site (~server.py:2122) for the scenario composers.

### 3b. The sim/result card -- primary readout (RECOMMENDED home)

On the running PipelineCard sim card (PipelineCard.tsx:1293-1310, the same
sub-line that today shows "SFINCS . 100 m . ~46k cells . 8 vCPU . 1:12 . est
~70s" via formatSolveReadout:119-138): when the terminal `solve-result` arrives,
REPLACE the live dimension sub-line (which clears on terminal anyway) with the
acceptance readout, e.g.:

```
GEOCLAW . PASS . max_depth 3.42 m . 1.8 km^2 . run 7f3a9c
```

This is the live mirror of the driver's READINESS_RESULT line. Keep it one line,
keyed off `metric_name`/`metric_value`/`status`/`run_id`. Green tint on PASS,
red on FAIL (reuse the pipeline-card success/failure tint already in place).

### 3c. Diagnostics expander -- reuse the #168 raw-IO chevron pattern

The card already has a ToolIoPanel chevron (PipelineCard.tsx:1587-1618,
ToolIoPayload). Add a SECOND collapsed section ("Diagnostics") on the sim/result
card, rendered from `solve-result.diagnostics`, showing:

- The full per-engine metric dict (all secondary metrics: min_factor_of_safety,
  total_leakage_m3_day, n_sites, etc. -- not just the primary).
- Phase A worker diagnostics: MODFLOW converged + note, SFINCS deck nr_cells /
  nr_levels / budget_notes / mesh counts, landlab full result block.
- Phase B worker diagnostics (when present): snapwave active/wavebnd/inactive +
  boundary_present + repair outcome, sentinel masked-cell count, make_exit.
- Deep links to stdout_uri / stderr_uri (the worker raw logs on S3).

Reuse the existing chevron/expander visual exactly (same component family as
ToolIoPanel) -- this is the "diagnostics expander on the sim/result card" the
goal asks for, and it does not require the agent to fetch CloudWatch.

### 3d. Solve-telemetry dashboard -- a per-run METRIC column

Settings -> Tools solve_telemetry table (RoutingQualityDashboard.tsx:120-135,
fed by /api/telemetry/summary -> _aggregate_solve_telemetry tool_catalog_http.py
:740 reading /tmp/grace2_solve_telemetry.jsonl). Today the row is
{run_id, solver, grid_resolution_m, active_cell_count, vcpus, wall_clock_seconds,
backend, aoi_km2} -- dimensions + wall-clock only. Add `metric_name`,
`metric_value`, and `status` to build_solve_telemetry_record + the JSONL +
SolveTelemetryRow, so each solve has a persisted acceptance readout (parity with
the verify driver's PASS line, browseable after the run). This is the durable /
historical face; 3b is the live face.

---

## 4. Ordered, file-disjoint build jobs

Phase A first (agent emit + web render, NO worker rebuild, deploys without
disrupting a live driving session). Phase B rides the next worker image rebuild.
Jobs within a phase are file-disjoint so they can run in parallel.

PHASE A:

1. job-A1 (schema): add `SolveResultPayload` to
   packages/contracts/src/grace2_contracts/ws.py + a mirror type in
   web/src/contracts.ts + add "solve-result" to ws.ts SESSION_SCOPED_TYPES.
   File-disjoint: contracts + ws.ts type/list only (no render logic).
2. job-A2 (agent): per-engine metric extraction + `_maybe_emit_solve_result`
   helper in server.py (modeled on _maybe_emit_impact_envelope) + wire it at the
   post-tool site for the 7 scenario composers; thread the waves peak-hm0 scalar
   from the postprocess_waves dict the coastal composer already produces; read
   Phase-A completion.json diagnostics (MODFLOW converged, SFINCS deck block,
   landlab result) into the diagnostics sub-dict. Files: server.py + the scenario
   composer workflow files. Disjoint from A1 (consumes the contract) and A3.
3. job-A3 (web): render the terminal solve-result on the sim/result card
   (formatSolveReadout sibling) + the Diagnostics expander (ToolIoPanel sibling)
   in PipelineCard.tsx + route the envelope in ws.ts handler. Files:
   PipelineCard.tsx + ws.ts handler block. Disjoint from A1's type addition.
4. job-A4 (agent/web, optional same window): add metric_name/metric_value/status
   to build_solve_telemetry_record + the JSONL writer + SolveTelemetryRow +
   RoutingQualityDashboard column. Files: telemetry.py record builder +
   RoutingQualityDashboard.tsx. Disjoint from A2/A3.

PHASE B (rides the next worker image rebuild -- do NOT trigger a special build):

5. job-B1 (sfincs_deckbuilder worker): append snapwave active/wavebnd/inactive +
   boundary_present + repair-outcome + sfincs mask counts into provenance ->
   completion.json `diagnostics`. Cheapest high-value change (locals sw_active/
   sw_wavebnd/sw_inactive already in scope at ~:1458/:1483). File:
   services/workers/sfincs_deckbuilder/entrypoint.py.
6. job-B2 (sfincs_deckbuilder worker): compute + record the nodata/sentinel
   masked-cell COUNT (new computation in _mask_topobathy_sentinels). Same file as
   B1 -> sequence B2 after B1 (NOT parallel -- shared file).
7. job-B3 (geoclaw worker): record make_exit + maketopo_rc in completion.json
   diagnostics. File: services/workers/geoclaw/entrypoint.py. Disjoint from B1/B2.
8. job-B4 (openquake + swmm workers, optional): record exit + stdout/stderr byte
   counts in diagnostics IF the rebuild is already happening. Files:
   services/workers/openquake/entrypoint.py + services/workers/swmm/entrypoint.py.
   Disjoint from B1/B3.

The Phase A web/agent code consuming `diagnostics` defaults every Phase-B field
to null, so Phase A ships and renders correctly BEFORE any worker rebuild; when
B1-B4 land on the next rebuild, the snapwave/make-exit fields simply start
populating the already-built expander.

---

## 5. HARD deploy note (agent restart window)

Phase A's job-A2 changes the agent (server.py + composers). Deploying the agent
requires an agent process restart, which drops the WebSocket and causes a brief
client reconnect blip. This is the same WS-drop class NATE has repeatedly flagged
during live demos.

THEREFORE: deploy Phase A in a CLEAN WINDOW BETWEEN NATE's test/drive sessions,
never mid-drive. The web side (A1 type, A3 render, A4 dashboard) deploys via the
S3+CloudFront file-swap and does NOT drop a live socket, but it must not land
before the agent it depends on -- so gate the whole Phase A bundle behind a
single between-sessions agent restart, then push web. Follow the standing
continuous-deploy norm (deploy as work lands green) but honor the one-restart
clean-window constraint for the agent half. Phase B carries no extra restart cost
beyond the worker image rebuild it already rides (scale-to-zero Batch workers, no
live socket).

---

## 6. Recommendation

Build Phase A now and deploy it in the next between-sessions window: it gives
NATE the full acceptance-parity readout (run_id + layers + the per-engine output
metric + PASS/FAIL) plus the agent-readable worker diagnostics (MODFLOW
converged, SFINCS deck cell/refinement counts, landlab metrics) with ZERO worker
rebuild, because all 7 output metrics already exist server-side post-run (6 on a
typed LayerURI the agent holds in-process, landlab also in completion.json) and
the high-value deck diagnostics are already in completion.json on S3. It reuses
three proven patterns: the impact-envelope side-channel emit
(_maybe_emit_impact_envelope), the formatSolveReadout sim-card sub-line, and the
#168 ToolIoPanel chevron expander. Defer the CloudWatch-only signals to Phase B
and pin them to the NEXT scheduled openquake/geoclaw/sfincs image rebuild --
starting with the sfincs_deckbuilder snapwave mask tuple (B1), which is a pure
dict-insert of locals already in scope and the single most root-cause-relevant
signal NATE cited (wavebnd=0). Surface the live readout on the sim/result card,
the deep diagnostics in the card's Diagnostics expander, and a durable per-run
metric column in the solve-telemetry dashboard.
