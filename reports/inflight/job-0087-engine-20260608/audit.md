# Audit: `fetch_gbif_occurrences` atomic tool

**Job ID:** job-0087-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="gbif",
)
def fetch_gbif_occurrences(species_key: int | str, bbox: tuple[float,float,float,float], year_range: tuple[int,int] | None = None, max_records: int = 5000) -> LayerURI:
    """GBIF Tier-1 species occurrence point fetcher.

    Wraps the GBIF Occurrence Search API (https://api.gbif.org/v1/occurrence/search).
    Accepts species by GBIF taxonKey (int) OR scientific name (str — resolved via
    GBIF species API). Bbox in WGS84 (west, south, east, north). Returns a FlatGeobuf
    with each occurrence as a point feature carrying species/date/coordinates/uncertainty.
    Returns LayerURI(layer_type="vector", role="context", units=None).
    """
```

**Implementation**:
- API endpoint: `https://api.gbif.org/v1/occurrence/search?taxonKey={key}&decimalLongitude={west},{east}&decimalLatitude={south},{north}&hasCoordinate=true&limit=300&offset={off}`
- Pagination: keep fetching 300-record pages until `endOfRecords: true` OR `len(records) >= max_records`
- If `species_key` is a str: first call `https://api.gbif.org/v1/species/match?name={name}` → `.usageKey` → use that taxonKey
- Cache key: SHA-256 of (species_key resolved to taxonKey, bbox-rounded-6dp, year_range, max_records)
- Output: FlatGeobuf with point geometry; properties: `gbifID`, `eventDate`, `coordinateUncertaintyInMeters`, `basisOfRecord`, `species`
- Cache prefix: `cache/static-30d/gbif/<hash>.fgb`
- Year-range filter via `&year={start},{end}` URL param if provided
- HTTP: use `httpx` (sync) with timeout=30, raise typed `GBIFUpstreamError(retryable=True)` on 5xx; `GBIFInputError(retryable=False)` on bad species/bbox

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked happy path: 300-record response → 300 features in FlatGeobuf
- Pagination: 600 records across 2 pages
- Species-name resolution: `species_key="Puma concolor coryi"` calls match endpoint
- Empty bbox: returns empty FlatGeobuf without error
- Live (env GRACE2_TEST_LIVE_GBIF=1): Florida panther taxonKey 7193927 over Everglades bbox → ≥1 feature

**Live verification**: fetch_gbif_occurrences(7193927, (-81.5, 25.5, -80.5, 26.5)) → real FlatGeobuf with Florida panther points around Big Cypress; logged to evidence/gbif_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_gbif_occurrences.py` (NEW)
- `reports/inflight/job-0087-engine-20260608/`


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

