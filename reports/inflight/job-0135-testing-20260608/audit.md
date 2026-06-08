# Audit: Case 2 partial acceptance — news/event ingest demo

**Job ID:** job-0135-testing-20260608, **Sprint:** sprint-12-mega Wave 3, **Specialist:** testing

**Required reads:**
- `services/agent/src/grace2_agent/workflows/model_news_event_ingest.py` (job-0119)
- `services/agent/src/grace2_agent/tools/{web_fetch,aggregate_claims_across_sources,fetch_nws_event,fetch_storm_events_db}.py`

### Scope

Run the Case 2 partial composer end-to-end demonstrating news → derived spill parameters (sprint-12-mega STOPS HERE; sprint-13 picks up with MODFLOW).

1. **Run live**: invoke `model_news_event_ingest`:
   - sources: 2 source dicts pointing at real news URLs about a real spill event (e.g. Longview-style — or pick a recent real incident; if hard to find a public real spill article, use 2 NWS storm events for a flood event)
   - target_event_type: "spill" OR "flood"
2. **Validate result**:
   - EventIngestResult.event_type matches input
   - derived_params populated with confidence scores
   - bbox derived from location
   - presentation_text reads naturally
3. **Capture demo flow**:
   - Use Playwright dev-injection to simulate the user-facing presentation: chat shows "Ingested 2 sources... derived event_type=spill, location='Longview, TX' (confidence 0.80), scale='15000 gal' (confidence 0.75), contaminant='vinyl chloride' (confidence 0.85). Proceed to model groundwater plume? [Yes] [No]" — show that the workflow STOPS here as designed.
   - Screenshot the chat flow + provenance citations
4. **Acceptance**: EventIngestResult contract round-trips; no solver dispatch leaked; presentation_text references all provided sources

**Output**:
- Evidence dir: `reports/inflight/job-0135-testing-20260608/evidence/`
- Screenshots: `case2_ingest_chat.png`
- `case2_event_ingest_result.json`: full EventIngestResult dump
- `case2_acceptance.md`: write-up + the sprint-13 hand-off note (user reviews this, says yes, then MODFLOW kicks in)

### File ownership (exclusive)

- `reports/inflight/job-0135-testing-20260608/`

### FROZEN

- All implementation files


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 3 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: pixel-level evidence required for any "screenshot captured" claim. Verify actual content where it's supposed to be — wettest pixels at the river mouth, per-species layers in different colors, etc.
2. **Kickoff-front-loaded design**: execute scope, surface OQs, don't redesign.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: use Persistence.* — no custom CRUD wrappers.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] Geographic-correctness / pixel-level / behavioral verification per kickoff
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

