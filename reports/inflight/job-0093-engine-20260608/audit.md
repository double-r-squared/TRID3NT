# Audit: `aggregate_claims_across_sources` atomic tool

**Job ID:** job-0093-engine-20260608, **Sprint:** sprint-12-mega Wave 1, **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/web_fetch.py` (sibling Wave 1 job — your input source)
- `services/agent/src/grace2_agent/tools/cache.py`

### Scope

NEW file `services/agent/src/grace2_agent/tools/aggregate_claims_across_sources.py`

```python
@register_tool(
    cacheable=False,  # LLM-reasoning output; do not cache across sources lists
    ttl_class="live-no-cache",
    source_class="claim_aggregator",
)
def aggregate_claims_across_sources(
    sources: list[dict],  # each {"url": str, "text": str, "fetched_at": str}
    claim_targets: list[str],  # e.g. ["location", "scale", "contaminant", "date"]
    confidence_threshold: float = 0.6,
) -> dict:
    """Cross-source claim aggregation for FR-HEP news/event ingest.

    For each claim_target, parse mentions across all sources, deduplicate +
    score by source agreement, return the best estimate + provenance.

    Returns:
        {
          "claims": {
            "<target>": {
              "value": str | float | dict | None,
              "confidence": float,  # 0-1
              "supporting_sources": [url, ...],
              "alternatives": [{"value": ..., "supporting_sources": [...]}],
            },
            ...
          },
          "stats": {"sources_consulted": int, "claims_resolved": int},
        }

    Targets specifically supported for v0.1:
        "location" — extract place name + reverse-geocode bbox via Nominatim
        "scale"    — extract magnitude/volume/area (numeric)
        "contaminant" — extract chemical/substance name (str)
        "date" — extract event date (ISO-8601)
        "casualties" — extract injury/death count (int)
    """
```

**Implementation**:
- This tool is NOT a standalone reasoning LLM call — it's a deterministic aggregator over LLM-extractable claims.
- Strategy v0.1 (deterministic, no LLM call): regex + keyword extraction per target type. Use `dateparser` for "date" target (already in `.venv-agent` likely). Use simple regex for numeric "scale" / "casualties". For "location" + "contaminant" — surface OQ-93-NEEDS-LLM-EXTRACTION for sprint-13 LLM-routed upgrade.
- Source-agreement scoring: if 2+ sources agree on a value, confidence = 0.8 + 0.05/extra-source (cap at 0.99); single-source confidence = 0.5
- Output: dict; not cacheable
- Typed errors: `ClaimAggError(retryable=False)` for bad input shape

**Tests** (≥6 unit):
- Single source with date → confidence 0.5
- 2 sources agreeing on date → confidence ≥0.85
- Sources disagreeing → both surface in `alternatives`
- Empty sources list → empty claims dict, no error
- claim_targets=['location'] with mocked sources mentioning "Longview, Texas" → location.value="Longview, Texas"
- claim_targets=['casualties'] with "3 injured" → casualties.value=3

**Live verification**: invoke with 3 hand-curated source dicts about a real (or fixture) spill event → returns claims dict with confidence scores; evidence/aggregate_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/aggregate_claims_across_sources.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_aggregate_claims_across_sources.py` (NEW)
- `reports/inflight/job-0093-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1 sibling has its own file ownership); all `workflows/`, `services/workers/`, `packages/contracts/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`.


### Concurrency note (Wave 1 fan-out)

~15 Wave 1 jobs run concurrently. Each owns its own NEW tool file but ALL share `tools/__init__.py` + `main.py` registration sites. The idempotent-append pattern from sprint-11 Stage 1 (which handled 6 concurrent additions cleanly) applies: ADD your import line at the end of each file; if your line conflicts with a sibling's, do `git pull --rebase` style re-apply; do NOT remove other tool imports.


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".


### Acceptance criteria

- [ ] New tool registered + visible at `--startup-only` (count = entering_count + 1)
- [ ] ≥4 unit tests + 1 live test (with appropriate env-var guard)
- [ ] Live verification with real upstream response captured to evidence/
- [ ] Geography correctness check per the codified job-0086 lesson (where applicable)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence paths + any OQs

