# Audit: Sprint-08 acceptance + sprint close (Stage D)

**Job ID:** job-0059-testing-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** testing

**Note on numbering:** the sprint-08 manifest reserved `job-0051-testing-20260607` as the Stage D testing slot. The counter advanced past 0051 during the mid-sprint hotfix chain (0052/0053/0054/0055/0056/0057/0058) so the actual issued Stage D job is `job-0059-testing-20260607`. The reservation in `reports/sprints/sprint-08.md` for 0051 is superseded by this kickoff; mention in the report when closing.

**Prerequisites (ALL APPROVED):**
- job-0045 (schema): CatalogEntry pydantic + D.11 + D.12 + 4 envelope payloads
- job-0046 (research): public_data_source_catalog.yaml v0.1.0 with 30 vetted entries
- job-0047 (engine): catalog_search + catalog_fetch + generic OGC adapter; 16 tools at startup
- job-0048 (agent): FR-FR-3 MAX_TURNS_PER_SESSION=25 cap
- job-0049 (infra): hydromt-sfincs install
- job-0052 (engine): yaml.safe_load fix
- job-0053 (engine): manning_roughness kwarg fix
- job-0054 (engine): comprehensive 1.2.x API migration audit
- job-0055 (engine): drop setup_river_inflow
- job-0056 (infra): pandas 2.2.3 pin
- job-0057 (engine): manifest.json emission for SFINCS worker
- job-0058 (engine): postprocess_flood squeeze + PRODUCTION M5 SUCCESS (flood_depth_peak.tif)

Plus orchestrator-direct decision doc: `docs/decisions/layer-emission-contract.md` (2026-06-07).

**SRS references** (narrow file loading only):
- `docs/srs/F-data-sources-discovery-secrets.md` §F.1 + §F.1.1 + §F.1.2
- `docs/srs/03-functional-requirements.md` FR-DC + FR-FR + FR-TA-2
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/sprints/sprint-08.md` — manifest + exit criteria
- `reports/complete/job-0058-engine-20260607/report.md` — the M5 SUCCESS evidence baseline

### Scope

Sprint-08 has substantially exceeded its original plan (Mode 1 catalog substrate AS PLANNED + comprehensive hydromt-sfincs 1.2.x migration THROUGH-LINE that wasn't planned + first PRODUCTION M5 SUCCESS achieved mid-sprint). This Stage D job verifies the cumulative substrate AND closes the sprint with an honest retrospective.

1. **Verify Mode 1 catalog substrate end-to-end** (job-0047 deliverable):
   - Live `catalog_search(topic="flood zones", location=fort_myers_bbox)` returns FEMA NFHL in rank-1.
   - Live `catalog_fetch(entry_id="fema-nfhl-flood-zones", params={bbox: fort_myers_bbox, layer_id: 28})` returns the real ≥1 MB GeoJSON polygon body (per job-0047 evidence baseline = 8.04 MB).
   - Live `catalog_fetch(entry_id="usgs-3dep-elevation-image-service", params={bbox: fort_myers_bbox})` returns the real ≥1 MB GeoTIFF body (per job-0047 evidence baseline = 4.2 MB).
   - Document any per-entry deviation from the baseline (network reliability, throttling, etc.).
   - Confirm 16 tools at startup via `--startup-only`.

2. **Verify FR-FR-3 MAX_TURNS cap** (job-0048 deliverable):
   - Drive a session past 25 turns and assert `session-state.status` flips to `"max_turns_reached"`.
   - Verify the closing `agent-message` summary fires on cap hit.
   - Verify further tool calls are refused on the same session.

3. **Verify PRODUCTION M5 SUCCESS holds end-to-end** (sprint-08 capstone):
   - Re-run the full Fort Myers / Hurricane Ian / 100-yr 24-hr Atlas 14 design storm chain via the existing `reports/complete/job-0058-engine-20260607/evidence/smoke_demo.py` harness (copy to `reports/inflight/job-0059-testing-20260607/evidence/`).
   - Confirm AssessmentEnvelope.outcome=SUCCESS, layer_uris populated with the COG GCS URI, flood_max_depth_m > 0.
   - rasterio.open the COG live and verify bounds/CRS/max-depth match job-0058 baseline shape.
   - **Capture and report the new GCS URI** so the orchestrator can surface a sprint-08 closing screenshot.

4. **Spot-check Invariant 7 NLCD validation gate still passes live** (substrate-level win from sprint-7 carried forward) — should fire PASS branch on the M5 re-run.

5. **Full M1+M2+M3+M4+M5 regression** — run the existing agent test suite (`PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q`). All 165 tests baseline (per job-0058) must still pass. Contracts suite `PYTHONPATH=packages/contracts/src .venv-agent/bin/python -m pytest packages/contracts/tests/ -q` must still pass (142 baseline).

6. **Sprint-08 retrospective**:
   - Honestly disclose: planned scope (Mode 1 catalog + small carry-forwards) vs actual scope (catalog + complete 1.2.x migration + first M5 SUCCESS + production COG path).
   - Cost telemetry: total tokens across all sprint-08 jobs from `reports/cost_tracking.json` — break out Opus vs Sonnet ratio (Sonnet routing wins per cost discipline rule were ≥5 in this sprint).
   - Acknowledge the **`docs/decisions/layer-emission-contract.md`** decision as the architectural pin going into sprint-09.
   - Open carry-forward list:
     - **OQ-59-FLOOD-COG-CRS-LABEL-VS-COORDS** (postprocess writes UTM-17N coords with EPSG:3857 tag; sprint-9 housekeeping)
     - **OQ-47-OWSLIB-CHOICE** (we shipped direct requests over owslib for Mode 1; document the decision in SRS housekeeping)
     - **OQ-47-CATALOG-YAML-SECRET-REFS** (2 of 30 entries declare credential_tier:2 but lack api_key_secret_ref)
     - **OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING** (no Dockerfile exists for agent service yet; production deploy pends)
     - **OQ-48-PARALLEL-SCHEMA-FILE-OWNERSHIP** (sprint process review)
     - **OQ-W-26 / OQ-33 / OQ-35 / OQ-36 / OQ-41-COMPUTE-CLASS-NAMING / OQ-44 / OQ-45 D-numbering** (carried forward from earlier sprints)
   - Sprint-09 opening hand-off: the three jobs identified by the layer-emission-contract decision (atomic publish_layer tool via PyQGIS worker + IAM grant on runs bucket + agent emission change). DO NOT scope sprint-09 in this report; just confirm the hand-off.

### File ownership (exclusive)
- `reports/inflight/job-0059-testing-20260607/` — your report + evidence
- `reports/sprints/sprint-08.md` — Retrospective section + Exit criteria checkboxes ONLY (you may add a "Notes on sprint-08 vs originally planned scope" note)

### FROZEN
- All source files (no code edits in a testing-acceptance job)
- All prior approved reports in `reports/complete/`
- `docs/decisions/layer-emission-contract.md` (just-landed; decision is final)
- `reports/PROJECT_LOG.md` — only the orchestrator appends (close note will be orchestrator-direct)

### Acceptance criteria
- [ ] Live catalog_search + catalog_fetch demo runs cleanly with documented byte counts.
- [ ] MAX_TURNS cap fires correctly via integration test.
- [ ] Re-run M5 chain produces SUCCESS envelope + real COG + rasterio verification.
- [ ] Sprint-08 acceptance screenshot capturable (new run_id GCS URI captured).
- [ ] Full agent suite 165+/165+ green; full contracts suite 142+/142+ green.
- [ ] Sprint-08 retrospective addresses planned vs actual scope, Sonnet routing wins, and the layer-emission-contract decision.
- [ ] Open OQ carry-forward list complete with sprint-9 hand-off notes.
- [ ] Single commit (`testing: job-0059 sprint-08 acceptance + close note`).
- [ ] No edits to FROZEN paths.
