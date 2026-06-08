# Audit: `fetch_inaturalist_observations` atomic tool

**Job ID:** job-0088-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_inaturalist_observations.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="inaturalist",
)
def fetch_inaturalist_observations(taxon_id: int | str, bbox: tuple[float,float,float,float], quality_grade: str = 'research', days_back: int | None = None, max_records: int = 5000) -> LayerURI:
    """iNaturalist Tier-1 citizen-science observation point fetcher.

    Wraps the iNaturalist API v1 (https://api.inaturalist.org/v1/observations).
    quality_grade='research' means vetted observations. Bbox WGS84. Returns FlatGeobuf
    points with species/date/observer/photo_url properties.
    Returns LayerURI(layer_type="vector", role="context", units=None).
    """
```

**Implementation**:
- API endpoint: `https://api.inaturalist.org/v1/observations?taxon_id={tid}&swlat={s}&swlng={w}&nelat={n}&nelng={e}&quality_grade={q}&per_page=200&page={p}`
- If `taxon_id` is a str: resolve via `https://api.inaturalist.org/v1/taxa?q={name}` → `.results[0].id`
- Pagination: page=1, 2, 3 until `total_results` exhausted OR `len(records) >= max_records`
- `days_back` filter via `&d1={YYYY-MM-DD}`
- Cache key: SHA-256 of (taxon_id resolved, bbox, quality_grade, days_back, max_records)
- Output: FlatGeobuf with point geometry; properties: `id`, `observed_on`, `user_login`, `photo_url`, `species_guess`, `place_guess`
- Cache prefix: `cache/static-30d/inaturalist/<hash>.fgb`
- HTTP: `httpx` sync, timeout=30, typed errors `INatUpstreamError(retryable=True)` / `INatInputError(retryable=False)`

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked happy path: 200-record response
- Pagination: 400 records across 2 pages
- Name resolution: taxon_id='Trichechus manatus' resolves to manatee id
- Empty / quality-grade=any vs research
- Live (env GRACE2_TEST_LIVE_INAT=1): manatee taxon over FL Gulf coast → ≥1 feature

**Live verification**: fetch_inaturalist_observations('American alligator', (-81.5, 25.5, -80.5, 26.5)) → real FlatGeobuf; evidence/inat_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_inaturalist_observations.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_inaturalist_observations.py` (NEW)
- `reports/inflight/job-0088-engine-20260608/`


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

