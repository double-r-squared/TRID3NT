# Audit: `fetch_nifc_fire_perimeters` atomic tool

**Job ID:** job-0110-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_nifc_fire_perimeters.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",
    source_class="nifc_perimeters",
    supports_global_query=True,  # NEW Wave 1.5 metadata
)
def fetch_nifc_fire_perimeters(bbox: tuple[float,float,float,float] | None = None, status: str = 'active') -> LayerURI:
    """NIFC active wildland fire perimeters.

    Wraps NIFC (National Interagency Fire Center) ArcGIS REST service.
    Returns FlatGeobuf polygons of currently-active large wildfires."""
```

**Implementation**:
- Endpoint: `https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query`
- query: where=1=1&geometry={bbox}&geometryType=esriGeometryEnvelope&inSR=4326&outFields=*&outSR=4326&f=geojson
- status filter via attr if needed; v0.1 returns whatever NIFC has marked current
- Properties: poly_IncidentName, poly_FeatureCategory, poly_DateCurrent, attr_IncidentSize, attr_PercentContained
- supports_global_query=True (CONUS default; bbox=None means CONUS-wide)
- ttl_class="dynamic-1h" (active fires move)
- LayerURI(layer_type="vector", role="primary")

**Payload estimation**: estimate_payload_mb: ~1MB CONUS active fires (typically 20-200 features).

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked: 10-feature response → 10 polygons
- bbox=None → all CONUS active fires
- bbox filter to specific state
- Pagination if needed
- Live (env GRACE2_TEST_LIVE_NIFC=1): CONUS bbox returns ≥0 features

**Live verification**: fetch_nifc_fire_perimeters() → real FlatGeobuf with currently-active fires; evidence/nifc_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_nifc_fire_perimeters.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_nifc_fire_perimeters.py` (NEW)
- `reports/inflight/job-0110-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1.5 sibling owns one); all `workflows/`, `services/workers/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`. For schema/agent jobs, FROZEN is the inverse of their declared file ownership.

### Concurrency note (Wave 1.5 fan-out — 16 parallel)

~16 Wave 1.5 jobs in parallel. Idempotent-append works for `tools/__init__.py` + `main.py` + `packages/contracts/__init__.py` but Wave 1 produced 3 commit-label-swap patterns under load. **Required mitigation**: before `git commit`, run `git pull --rebase=true origin main 2>/dev/null || git stash && git pull --rebase && git stash pop` to handle sibling concurrent landings cleanly. If conflict on registration site, re-apply your import line.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: if your tool emits geometry, verify against actual geography (river mouth where it should be, not just bbox/URL consistency). Every fetcher's live test must check that emitted features fall inside requested bbox AND match the named place's actual outline if applicable.

2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.

### Acceptance criteria

- [ ] New tool/contract registered + visible at appropriate test surface
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness check where applicable
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

