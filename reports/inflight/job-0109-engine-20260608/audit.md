# Audit: `fetch_mtbs_burn_severity` atomic tool

**Job ID:** job-0109-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_mtbs_burn_severity.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="mtbs_burn_severity",
    supports_global_query=False,  # NEW Wave 1.5 metadata
)
def fetch_mtbs_burn_severity(bbox: tuple[float,float,float,float], year_range: tuple[int,int] | None = None) -> LayerURI:
    """MTBS (Monitoring Trends in Burn Severity) historic burn severity polygons.

    Wraps MTBS public ArcGIS REST service. Returns FlatGeobuf polygons with
    fire name, year, acres, burn severity class. Tier-1 free, no auth."""
```

**Implementation**:
- Endpoint: `https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/MTBS_BAreas/FeatureServer/0/query`
- Query: where=1=1&geometry={bbox}&geometryType=esriGeometryEnvelope&inSR=4326&outFields=*&outSR=4326&f=geojson
- year_range filter via where clause: `Ig_Year >= {start} AND Ig_Year <= {end}`
- Properties: Event_ID, Incid_Name, Incid_Type, Ig_Year, BurnBndAc (acres), BurnBndLat, BurnBndLon
- Pagination via resultOffset if >2000 features
- supports_global_query=False (bbox required; CONUS-only dataset)
- ttl_class="static-30d"
- LayerURI(layer_type="vector", role="primary")

**Payload estimation**: estimate_payload_mb: ~0.5MB per 1000 polygons; large states ~10MB.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked: 50-feature response → 50 polygons
- year_range filter narrows
- Empty bbox → 0-feature FlatGeobuf
- Pagination across 3000 features
- Live (env GRACE2_TEST_LIVE_MTBS=1): CA bbox → ≥1 known fire (e.g. Camp Fire 2018)

**Live verification**: fetch_mtbs_burn_severity((-122,38,-119,40), year_range=(2020,2023)) → real FlatGeobuf with CA fires; evidence/mtbs_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_mtbs_burn_severity.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_mtbs_burn_severity.py` (NEW)
- `reports/inflight/job-0109-engine-20260608/`


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

