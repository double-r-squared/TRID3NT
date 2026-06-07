# Audit: catalog_search + catalog_fetch atomic tools + generic OGC adapter (Stage B)

**Job ID:** job-0047-engine-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0045 (APPROVED):** CatalogEntry pydantic + D.11/D.12 MongoDB collections + Appendix A envelope shapes
- **job-0046 (APPROVED):** `public_data_source_catalog.yaml` v0.1.0 with 30 vetted entries
- job-0032 + job-0033 (cache shim + atomic-tool pattern); job-0044 (NLCD WCS Tier 2 OGC adapter reference implementation)

**SRS references** (narrow file loading only):
- `docs/srs/F-data-sources-discovery-secrets.md` (§F.1 + §F.1.1 + §F.1.2 — binding)
- `docs/srs/03-functional-requirements.md` (FR-TA-2, §3.9 FR-DC, FR-CE-8)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `public_data_source_catalog.yaml` — the catalog you're querying + fetching from
- `reports/complete/job-0044-engine-20260607/report.md` — NLCD WCS Tier 2 adapter pattern (your generic OGC adapter mirrors this)
- `reports/complete/job-0033-engine-20260606/report.md` — cache-shim integration pattern (read_through, source_class, ttl_class)

### Scope

1. **`services/agent/src/grace2_agent/tools/catalog.py`** (NEW). Implements two atomic tools per §F.1.2 Mode 1:
   - **`catalog_search(topic: str, location: tuple[float,float,float,float] | None = None, source_filter: str | None = None) → list[CatalogEntry]`** — Queries the `public_data_source_catalog.yaml` (and the future MongoDB `catalog_entries` collection from D.11). Filter by topic match (against `description` + `how_to_use`), optional bbox-overlap if location given, optional source_class. Returns ranked matches.
     - Register as `cacheable=True`, `ttl_class="semi-static-7d"` (the catalog itself changes weekly when curators update), `source_class="catalog_search"`.
   - **`catalog_fetch(entry_id: str, params: dict) → LayerURI | dict`** — Generic dispatcher. Reads `entry.access_tier` from the catalog and dispatches to the appropriate fetch path:
     - **Tier 1 (STAC+COG):** STAC item query → byte-window read → cache write
     - **Tier 2 (OGC service):** dispatch to the generic OGC adapter (see step 2)
     - **Tier 3 (HTTPS+Range):** `/vsicurl/` windowed read → cache write
     - **Tier 4 (region download):** two-stage cache per OQ-37-COUNTRY-FILE-CACHING-STRATEGY
     - Register as `cacheable=True`, `ttl_class=` per the entry's declared class, `source_class=` per the entry's declared class.

2. **`services/agent/src/grace2_agent/tools/ogc_adapter.py`** (NEW). Generic Tier-2 OGC adapter — single implementation that any WMS/WMTS/WCS/WFS catalog entry can route through:
   - Function: `fetch_ogc_layer(url, layer_name, bbox, crs="EPSG:3857", service_type="WMS|WCS|WFS", format="image/geotiff", version="1.0.0") → bytes`
   - Uses `owslib` or direct HTTP if owslib is too heavy. Pin choice in pyproject.toml.
   - **Refactor `fetch_landcover` (post-job-0044 WCS) to call into this adapter** — same Tier 2 path, shared implementation. Don't duplicate WCS logic across tools.
   - Routes through `read_through` per FR-CE-8.

3. **Catalog backing store for v0.1: YAML file load.** Read `public_data_source_catalog.yaml` from the repo root at agent startup; cache in-memory; expose a `_get_catalog_entry(entry_id)` helper. **Future MongoDB-backed**: when D.11 catalog_entries is populated, the loader switches to MongoDB query; YAML stays as fallback. v0.1 = YAML only; document the migration path.

4. **Wire `catalog_search` + `catalog_fetch` into `main.py`** via the eager import pattern (mirror `data_fetch` + `qgis_discovery`). Registry should show ≥16 tools at `--startup-only` (14 + 2 new).

5. **Tests** in `services/agent/tests/test_catalog_tools.py` (NEW). At least 8 tests:
   - `catalog_search` returns ranked entries by topic match
   - `catalog_search` with bbox filter
   - `catalog_search` with source_filter
   - `catalog_fetch` dispatches correctly per access_tier (mocked Tier 1/2/3/4 paths)
   - `catalog_fetch` cache-shim integration (read_through hit + miss)
   - Generic OGC adapter — WMS GetMap mocked
   - Generic OGC adapter — WCS GetCoverage mocked (mirror NLCD)
   - Generic OGC adapter — WFS GetFeature mocked

6. **Live evidence** in `evidence/`:
   - `catalog_search(topic="flood zones", location=fort_myers_bbox)` returns FEMA NFHL entry
   - `catalog_fetch(entry_id="fema_nfhl", params={bbox: fort_myers_bbox})` returns a real flood-zone layer via the OGC adapter
   - `catalog_search(topic="DEM")` returns 3DEP entry; `catalog_fetch` retrieves a real DEM

### File ownership (exclusive)
- `services/agent/src/grace2_agent/tools/catalog.py` (NEW)
- `services/agent/src/grace2_agent/tools/ogc_adapter.py` (NEW)
- `services/agent/src/grace2_agent/tools/data_fetch.py` — ONLY the refactor of `fetch_landcover` to call into the generic OGC adapter; do not refactor any other fetcher
- `services/agent/src/grace2_agent/main.py` — eager import additions only
- `services/agent/pyproject.toml` — add `owslib` if chosen
- `services/agent/tests/test_catalog_tools.py` (NEW)
- `reports/inflight/job-0047-engine-20260607/`

### FROZEN
- All other tools/*.py files
- All workflows/*.py files (concurrent job-0054 owns sfincs_builder.py)
- packages/contracts/**, infra/**, web/**, docs/srs/**, styles/**, services/workers/**, reports/complete/**

### Acceptance criteria
- [ ] `catalog_search` + `catalog_fetch` registered; ≥16 tools at startup
- [ ] Generic OGC adapter implementation single-source-of-truth for Tier 2; `fetch_landcover` refactored to use it
- [ ] All 4 access tiers dispatch correctly (mocked tests for each)
- [ ] Cache-shim integration verified
- [ ] Live evidence for FEMA NFHL flood zones fetch + 3DEP DEM fetch
- [ ] ≥8 new tests; full agent suite green; contracts unchanged
- [ ] No edits to FROZEN paths
