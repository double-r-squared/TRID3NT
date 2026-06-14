# job-0245 ROUND 3 — Adversarial verify, EVIDENCE INTEGRITY lens

Verdict: **CONFIRM** (runner verdict=PARTIAL is faithfully supported by the raw artifacts).
Severity of residual concerns: **minor** (one stale-but-internally-consistent live artifact; one harness-metric quirk that does not affect the conclusion).

## Method
Re-derived every load-bearing claim from raw artifacts (not the prose). Where possible, independently
re-probed the live infra (gcloud at /home/nate/tools/google-cloud-sdk/bin, curl to the live QGIS Server).

## Critical claims, re-derived

### 1. "publish_layer succeeded end-to-end" — CONFIRMED
- `scenarioA_agent_chain.log:38-43`: dispatch Cloud Run Job grace-2-pyqgis-worker (04:46:48) →
  `execution completed state=CONDITION_SUCCEEDED` (04:49:48) → `publish_layer succeeded` →
  `run_modflow_job complete ... uri=<WMS>` → `case2 complete` → `function-response queued iter=5
  ... summary_keys=['result','status','tool']` (SUCCESS shape, not error).
- Gemini-free GCS proof INDEPENDENTLY re-verified live this session:
  `gcloud storage objects describe gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` →
  generation **1781092174312777**, size **180502** — EXACT match to `qgs_layer_proof.txt`.
  `gcloud storage cat ... | grep` → layer `plume-concentration-01KTRNPCV4NEN0RRQ3H0QMZQY6` PRESENT;
  `<maplayer` count = **11** — exact match. The worker really did write the layer into the served .qgs.

### 2. plume map screenshot shows NO Idaho overlay + empty layer panel — CONFIRMED
- `A05_final_plume_map.png` (visually inspected): CONUS-zoom OSM basemap, NO plume overlay over Idaho,
  layer panel ("Untitled Case") has no plume entry. Matches findings.json
  `A.final_map.in_idaho_bbox=false`, `layer_ids=[qgis-basemap,osm-fallback-basemap]`, `layer_panel=[]`,
  `plume.materialized=false`. The FAIL on case2_render is honest and visually corroborated.
- Honest-narration mismatch is REAL and end-user-facing: the chat bubble in A05 reads
  "The plume layer has been added to the map for visualization." while no layer shows.
  Matches `A.narration.claims_map_add=true`. Report flags this correctly (does not hide it).

### 3. gate-before-dispatch ordering held (regression) — CONFIRMED
- `scenarioA_agent_chain.log`: solver-confirm gate emitted 04:46:42,252 (line 29) →
  tool-payload-confirmation accepted decision=proceed 04:46:43,341 (line 30) →
  run_modflow_local solve started/exit=0 04:46:45 (lines 33-34). Gate strictly precedes the solve.
  findings.json `A.ordering.gate_before_dispatch=true`. Confirmation-registry acceptance
  (warning_id=01KTRNPAEBSE38AWFETJ3F5W1J → decision=proceed) is logged and matches the WS frame at
  t_rel_ms=45268 (SENT:tool-payload-confirmation). HOLDS.
- Minor metric quirk: findings.json `modflow_dispatch_rel_ms=null`. This is because the run dispatched
  as the COMPOSER (`run_model_groundwater_contamination_scenario`), so no raw `run_modflow_job`
  pipeline-state frame appeared for the harness to key on. Does NOT undermine ordering — the gate frame
  (t_rel_ms=44179) and the composer running-frame (t_rel_ms=45269) are both present and correctly ordered.

### 4. ImpactPanel numbers match narration — N/A (no ImpactPanel produced; p5_impact BLOCKED) — CONFIRMED
- `B02_no_impact_panel.png` (visual): groundwater confirmation card + red
  "CONFIRMATION_TIMEOUT ... run_model_groundwater_contamination_scenario ... gate timed out". No panel.
- WS frames: B1 user-message "Model flood damage for Fort Myers..." (t=639402) → tool-payload-warning
  run_modflow_job Twin-Falls/TCE (t=658543) → error CONFIRMATION_TIMEOUT (t=958552, exactly 300s ttl
  after the gate) → honest cancellation narration. The Fort Myers prompt was mis-routed to the prior
  TCE groundwater composer (context-carryover). No SFINCS/Pelicun/ImpactPanel ever ran. BLOCKED is honest;
  no impact numbers to falsify. Cross-check: zero impact/chart/damage artifacts in evidence/ (only the
  negative-proof B02 screenshot).

### 5. chart replay matches original — N/A (chart_emission/chart_replay BLOCKED, depend on B1) — CONFIRMED
- No chart/gallery/histogram/damage_distribution artifacts exist in evidence/. Consistent with BLOCKED.

### 6. code-exec-request before sandbox spawn — N/A this round (sandbox_gate_live BLOCKED) — CONFIRMED honest
- Round-3 live session (01KTRNN09HZ5BWVQXE5D9C9Y3Q): grep for `code-exec-request emitted` = **0 hits**.
  C numpy prompt mis-routed AGAIN to the Twin-Falls groundwater gate (WS t=1687041; agent log 05:14:05).
  `C01_no_sandbox.png` shows TWO groundwater confirmation cards, NO SandboxCard. findings.json
  `C.sandbox_request_present=false`, `code_exec_request_frame=false`. BLOCKED is honest.
- HONESTY CHECK on the runner's own footnote: the report's parenthetical (report.md:56) admits a
  leftover round-2-era driver session DID exercise the local sandbox earlier. Re-derived: that run is in
  session **01KTRMGNNFHT2GD042DVP6ZDC6** (a DIFFERENT session) at 04:44:29 →
  `sandbox local run ... executor.py (cap=60s)` → `code-exec-result emitted status=error`
  (agent_log_round3.log:149-150). It was status=ERROR and correctly NOT counted as a PASS. No fabrication.

### Context-carryover mis-route (the actual blocker) — CONFIRMED reproducible
- `context_carryover_misroute.txt` + agent log + WS frames: in the reused WS session, the B1 Fort Myers
  flood prompt and the C numpy prompt both emitted `run_modflow_job` gates for trichloroethylene /
  Twin Falls, Idaho — identical tool_args (lat/lon 42.5558542,-114.4700684, total_mass_kg 66320.41...)
  to Scenario A's TCE turn. Cached prompt prefix = 90,811 tokens (constant across all iters in the log).
  Genuine, reproduced on 2 consecutive turns.

## Residual concern (minor — does NOT change the verdict)
- I independently re-probed the LIVE QGIS Server NOW and it returns a valid PNG for the round-3 layer and
  GetCapabilities advertises BOTH `plume-concentration-01KTRNPCV4...` AND `plume_smoke_job0244`. This
  DIFFERS from the captured `getmap_layernotdefined.xml` (LayerNotDefined) + the report's claim that
  GetCapabilities listed only the older layer at test time. This is NOT an integrity violation: the
  divergence is exactly what the report's own diagnosis predicts (OQ-0245-QGIS-PROJECT-CACHE — a cold
  instance eventually re-parsed the .qgs in the hours since the test, self-healing the stale cache). The
  captured artifact is internally consistent with the report's narrative AT TEST TIME; the later self-heal
  corroborates (rather than contradicts) the stale-cache root cause. Worth flagging only because the live
  state is now transiently "passing" — the underlying cache-invalidation gap is real and unfixed.

## Bottom line
Every load-bearing claim in the PARTIAL verdict is supported by raw artifacts; the one independently
re-checkable Gemini-free claim (.qgs generation + layer presence + maplayer count) matched to the byte.
No PASS is fabricated for any BLOCKED scenario. The honest-narration mismatch and the earlier ERROR
sandbox run are both disclosed, not hidden. CONFIRM.
