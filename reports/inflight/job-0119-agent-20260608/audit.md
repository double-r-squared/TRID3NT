# Audit: `model_news_event_ingest` workflow — Case 2 composer

**Job ID:** job-0119-agent-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** agent

**Required reads:**
- `services/agent/src/grace2_agent/tools/{web_fetch,fetch_nws_event,fetch_storm_events_db,aggregate_claims_across_sources}.py` (Wave 1)
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py`
- Memory: `project_post_sprint_10_roadmap` (Case 2 acceptance criteria)

### Scope

NEW file `services/agent/src/grace2_agent/workflows/model_news_event_ingest.py`

This is the Case 2 partial composer — takes EITHER a news URL OR an NWS alert ID and produces derived event parameters that the user reviews BEFORE any solver runs (sprint-13 will continue with MODFLOW). This workflow STOPS BEFORE downstream modeling.

```python
@register_workflow(name="model_news_event_ingest", description="Ingest news/alert sources and derive event parameters (location, scale, contaminant) for user review before downstream modeling.")
async def model_news_event_ingest(
    sources: list[dict],  # each {"type": "url"|"nws_alert"|"storm_event", "identifier": str}
    target_event_type: str = "spill",  # "spill"|"flood"|"wildfire"|"hurricane"
    pipeline_emitter: PipelineEmitter,
) -> EventIngestResult:
    """Compose news/alert ingest → claim aggregation → derived event params.
    1. For each source: dispatch web_fetch / fetch_nws_event / fetch_storm_events_db
    2. Pass all extracted text + alert descriptions to aggregate_claims_across_sources(claim_targets=["location","scale","contaminant","date","casualties"])
    3. Resolve "location" claim → geocode → bbox
    4. Compose presentation envelope: derived_event_params + provenance + confidence per field
    5. Return result; STOP — sprint-13 picks up with MODFLOW

    LLM guidance: the agent can choose target_event_type from natural language
    ("Longview spill" → spill; "Hurricane Idalia" → hurricane).
    """
```

**Implementation**:
- Use the agent's LLM-routing facility to invoke the underlying atomic tools in sequence (each tool call is a real registered tool invocation; do NOT bypass the registry)
- `EventIngestResult` (new contract dataclass): `event_type`, `derived_params: dict` (location/scale/contaminant/date/casualties — each with value + confidence), `provenance: list[dict]` (per-source citations), `bbox: tuple[float,float,float,float] | None`, `presentation_text: str` (human-readable summary for the user review modal)
- This workflow is REVIEW-GATED: emit a `case2-event-ingest-result` envelope that the web UI displays for user approval BEFORE solver dispatch (sprint-13 MODFLOW reads this envelope to start)
- Pipeline emitter at each step

**Demo flow it enables (Case 2 acceptance)**:
```
user: "Model the Longview spill event"
agent.model_news_event_ingest(
  sources=[{"type": "url", "identifier": "https://news.example.com/longview-spill-2024"}, 
           {"type": "nws_alert", "identifier": "..."}],
  target_event_type="spill"
) → EventIngestResult{ location="Longview, TX", scale=15000 gal, contaminant="TCE", confidence=0.85 }
agent presents derived_params to user in chat with provenance + asks "Proceed to model groundwater plume?"
[user says yes; sprint-13 MODFLOW kicks in]
```

**Tests** (≥6 unit + 1 live):
- Mocked source ingest: 3 sources with mixed types → aggregated derived params
- Single URL source → workflow handles single-source case
- target_event_type='hurricane' selects different claim_targets
- Provenance includes all source URLs with citation snippets
- Empty sources → typed input error
- Live (env GRACE2_TEST_LIVE_CASE2=1): real news URL fixture about a real event → produces real derived params

### File ownership (exclusive)

- `services/agent/src/grace2_agent/workflows/model_news_event_ingest.py` (NEW)
- `services/agent/src/grace2_agent/workflows/__init__.py` — append registration (idempotent w/ job-0118)
- `packages/contracts/src/grace2_contracts/case_results.py` — append EventIngestResult (idempotent w/ job-0118)
- `packages/contracts/src/grace2_contracts/__init__.py` — append exports
- `services/agent/tests/workflows/test_model_news_event_ingest.py` (NEW)
- `reports/inflight/job-0119-agent-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 2 job's exclusive files; `reports/complete/**`; `docs/SRS_v0.3.md` monolith (regenerated only); all Wave 1/1.5 atomic tool files (additive use only — don't modify their signatures).

### Concurrency note (Wave 2 fan-out — 16 parallel)

Same idempotent-append pattern + `git pull --rebase` pre-commit mitigation as Wave 1.5. Files all land correctly in HEAD; only commit-message labels may drift. Use marker commits if your changes get swept into a sibling's commit hash.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: verify against real geography, not URL/render consistency.
2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: ALL CRUD goes through `Persistence.*`. Do NOT design custom collection wrappers. If your job needs a new method on Persistence, ADD it (additive) rather than bypassing.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness / behavioral-correctness verified
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

