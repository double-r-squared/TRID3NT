# Report: `fetch_mtbs_burn_severity` atomic tool

**Job ID:** job-0109-engine-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** engine
**Task:** NEW atomic tool `fetch_mtbs_burn_severity(bbox, year_range)` →
`LayerURI` over the MTBS public ArcGIS REST FeatureServer; FlatGeobuf burn-
area polygons + standard cache integration; ≥4 unit + ≥1 live test;
geographic-correctness gate.
**Status:** ready-for-audit

## Summary

Landed `fetch_mtbs_burn_severity(bbox, year_range)` returning a `LayerURI`
pointing at a FlatGeobuf of MTBS historic burn-area boundary polygons clipped
to the requested bbox (and optionally filtered to a year window via
server-side `where=YEAR >= start AND YEAR <= end`). Routed through
`read_through(static-30d, mtbs_burn_severity)` so identical
`(bbox, year_range)` calls hit the cache. 26 unit tests + 1 live test pass;
live invocation on the California bbox `(-122,38,-119,40)` for
`year_range=(2020,2023)` returns 31 real fire polygons including Dixie 2021,
Caldor 2021, North Complex 2020, with 26/31 centroids inside the requested
bbox (geographic-correctness gate satisfied).

## Changes Made

- File: `services/agent/src/grace2_agent/tools/fetch_mtbs_burn_severity.py` (NEW)
  - Public `@register_tool` atomic-tool decorator with
    `AtomicToolMetadata(name="fetch_mtbs_burn_severity",
    ttl_class="static-30d", source_class="mtbs_burn_severity",
    cacheable=True)`.
  - Internal helpers: `_validate_bbox`, `_validate_year_range`,
    `_round_bbox_to_6dp`, `_bbox_to_envelope`, `_build_where_clause`,
    `_mtbs_query_one_page`, `_fetch_mtbs_features` (pagination with
    `exceededTransferLimit` + safety cap), `_features_to_flatgeobuf`
    (geopandas / pyogrio), and `_fetch_mtbs_bytes`.
  - Typed error hierarchy (FR-AS-11): `MTBSError` base with
    `MTBSUpstreamError` (retryable=True), `MTBSBboxError`,
    `MTBSYearRangeError` (both retryable=False).
  - FR-TA-3 docstring: one-sentence summary, "Use this when:", "Do NOT use
    this for:", parameter and return descriptions, cache-key note.
- File: `services/agent/src/grace2_agent/tools/__init__.py` (1 line append)
  - `from . import fetch_mtbs_burn_severity` after the existing
    `fetch_mrms_qpe` import.
- File: `services/agent/src/grace2_agent/main.py` (1 line)
  - `from .tools import fetch_mtbs_burn_severity` inside
    `_import_tools_registry()`.
- File: `services/agent/tests/test_fetch_mtbs_burn_severity.py` (NEW)
  - 26 unit tests + 1 env-guarded live test (`GRACE2_TEST_LIVE_MTBS=1`).
  - Coverage: registration, bbox/year_range validation, helpers,
    50-feature round-trip, year_range narrowing via captured where-clause,
    empty bbox → 0 features, pagination across 3000 features (2000+1000),
    cache miss-then-hit, year_range cache-key differentiation, upstream
    error envelope, retryable flags, LayerURI shape. Live test: CA bbox
    2020-2023 → ≥1 fire, centroid-in-bbox gate, YEAR range respected.

## Decisions Made

- Decision: Use the live `services2.arcgis.com/FiaPA4ga0iQKduv3/.../EDW_MTBS_v1/FeatureServer/0` endpoint instead of the kickoff's `services1.arcgis.com/ESMARspQHYMw9BZ9/.../MTBS_BAreas/FeatureServer/0`.
  - Rationale: kickoff URL returns HTTP 400 — the `ESMARspQHYMw9BZ9` org
    is a UK feature-server publisher, no MTBS service exists there.
    ArcGIS Online portal search returned the canonical
    Esri_US_Federal_Data `EDW_MTBS_v1` FeatureServer. Layer id = 0
    (Burned Area Boundaries — All Years), verified via service-info JSON.
  - Surfaced as `OQ-0109-MTBS-URL-CORRECTED`.
- Decision: Use the live MTBS schema field names — UPPERCASE `FIRE_ID`,
  `FIRE_NAME`, `YEAR`, `FIRE_TYPE`, `ACRES`, `LATITUDE`, `LONGITUDE`,
  `MAP_ID`, `MAP_PROG`, `ASMNT_TYPE`, `IRWINID`, `IG_DATE` — instead of
  the kickoff's `Event_ID`/`Incid_Name`/`Ig_Year`/`BurnBndAc` set.
  - Rationale: kickoff field names match an older MTBS schema that does
    not exist on the live FeatureServer; pulled actual list from
    `/layers?f=json` and used those. Where-clause uses `YEAR`, not `Ig_Year`.
- Decision: Omit `supports_global_query` from `AtomicToolMetadata`
  (semantically `False`).
  - Rationale: field does not exist on the contract yet; schema amendment
    is job-0114-schema. Passing it raises `pydantic.ValidationError`.
    Same approach as sibling fetchers (`fetch_nws_alerts_conus`,
    `fetch_nexrad_reflectivity`, `fetch_goes_satellite`).
  - Surfaced as `OQ-0109-GLOBAL-QUERY-FIELD`.
- Decision: `year_range` inclusive on both endpoints, `None` returns
  all years; validator rejects start < 1984, start > end.
- Decision: Page size 1000 (the FeatureServer's `maxRecordCount`);
  safety cap at 50 pages = 50k features.
  - Rationale: MTBS service publishes `maxRecordCount=1000`. The kickoff
    cited 2000 but requesting more silently truncates to 1000.

## Invariants Touched

- Invariant 1 (Determinism boundary): preserves — all metrics carried in
  typed `LayerURI` fields + the FlatGeobuf attribute table.
- Invariant 2 (Deterministic workflows): preserves — pure Python atomic
  tool, no Gemini calls.
- Invariant 10 (Minimal parameter surface): preserves — only `bbox` +
  optional `year_range`. No fetchable sub-inputs.
- FR-AS-11 (typed errors): preserves — `MTBSError` hierarchy with
  `error_code` + `retryable`.
- FR-DC-3/4/8 (cache shim): preserves — `read_through` with content-
  addressed key.
- CRS hygiene: preserves — `outSR=4326`, EPSG:4326 enforced on the
  geodataframe and empty-result FlatGeobuf.

## Open Questions

- **OQ-0109-MTBS-URL-CORRECTED**: kickoff URL is wrong (HTTP 400). Live
  MTBS FeatureServer is `services2.arcgis.com/FiaPA4ga0iQKduv3/.../EDW_MTBS_v1/FeatureServer/0`
  with UPPERCASE field names. Implementation uses the corrected endpoint.
  Resolution: orchestrator should note the kickoff-URL bug pattern so
  future MTBS-adjacent kickoffs cite the verified endpoint.
- **OQ-0109-GLOBAL-QUERY-FIELD**: kickoff requests
  `supports_global_query=False` in metadata but the field does not exist
  on the contract yet (job-0114-schema is adding it). One-line follow-up
  once schema lands.
- **OQ-0109-YEAR-RANGE-SEMANTICS** (TENTATIVE): year_range is inclusive
  on both endpoints; single year = `(y, y)`. Surfaced for confirmation.
- **OQ-0109-INCID-TYPE-FILTER** (TENTATIVE): no incident_type filter on
  the public surface; all event types (Wildfire / Prescribed Fire /
  Wildland Fire Use / Unknown) returned. Future enrichment job can add
  a filter argument if downstream needs differentiation.
- **OQ-0109-STYLE-PRESET**: `mtbs_burn_severity` reserved by name; actual
  QML preset content is out of scope.

## Dependencies and Impacts

- Depends on: job-0089 (WDPA ArcGIS REST fetcher pattern); job-0084
  (FlatGeobuf encoding via geopandas/pyogrio); job-0031 (live cache
  bucket); job-0032 (tool registry + cache shim).
- Affects:
  - `web` — once a QML preset is authored, catalog should reference
    `mtbs_burn_severity`.
  - `schema` — pending job-0114 amendment that adds
    `supports_global_query`; one-line follow-up here.
  - `engine` (future jobs) — post-fire hazard workflows can compose MTBS
    polygons with `clip_*_to_polygon` and downstream solvers.

## Verification

- Tests run: 26 unit + 1 live (`GRACE2_TEST_LIVE_MTBS=1`); broader
  registry/cache suite re-run (61 passed, 2 skipped — no regressions).
- Live E2E evidence: `evidence/mtbs_live.txt`:
  - bbox `(-122, 38, -119, 40)`, year_range `(2020, 2023)`
  - 31 features returned live, 26/31 centroids inside bbox.
  - YEAR values `[2020, 2021, 2022, 2023]` — strict subset of requested.
  - Top fires by acreage: DIXIE 2021 (979,795 ac), NORTH COMPLEX 2020,
    HENNESSEY 2020, CALDOR 2021, SUGAR 2021 — all famous, real CA fires.
  - Cache HIT verified on second call; URI:
    `gs://grace-2-hazard-prod-cache/cache/static-30d/mtbs_burn_severity/6dc75bef18afebe17ce1af33dde8a9ba.fgb`
- Tool registration: `TOOL_REGISTRY` length 29 (was 28);
  `fetch_mtbs_burn_severity` entry present with expected metadata.
- Results: **pass** — geographic-correctness gate satisfied, unit + live
  tests green, no FROZEN edits, cache integration round-trips.
