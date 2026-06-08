# Report: ``fetch_nifc_fire_perimeters`` atomic tool

**Job ID:** job-0110-engine-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** engine
**Task:** Author the ``fetch_nifc_fire_perimeters`` atomic tool — wrapping the
NIFC WFIGS Interagency Perimeters Current ArcGIS REST FeatureService —
returning a FlatGeobuf ``LayerURI`` of currently active wildland fire
perimeters; CONUS sweep by default, optional bbox filter; dynamic-1h cache;
≥4 unit tests + ≥1 live test with geographic-correctness gate; register in
``tools/__init__.py`` + ``main.py``.
**Status:** ready-for-audit

## Summary

Landed the ``fetch_nifc_fire_perimeters`` atomic tool: a Tier-1 NIFC active
wildfire perimeter fetcher that wraps the WFIGS Interagency Perimeters Current
ArcGIS REST FeatureService at
``https://services3.arcgis.com/T4QMspbfLg3qTGWY/.../FeatureServer/0/query``,
returning a ``LayerURI`` pointing at a FlatGeobuf in the dynamic-1h cache
bucket. ``bbox=None`` runs a CONUS+AK+HI sweep with no ``geometry`` filter on
the wire; passing a bbox sends an ``esriGeometryEnvelope`` server-side
spatial filter with ``inSR=4326``. The tool is registered with
``supports_global_query=True`` (the Wave 1.5 schema field landed by job-0114
during this job's execution) so the catalog/discovery layer can route
nationwide queries here without a forced bbox.

## Changes Made

- **NEW** ``services/agent/src/grace2_agent/tools/fetch_nifc_fire_perimeters.py``
  - ``fetch_nifc_fire_perimeters(bbox=None, status='active') -> LayerURI``,
    decorated with ``@register_tool(AtomicToolMetadata(name=...,
    ttl_class='dynamic-1h', source_class='nifc_perimeters', cacheable=True,
    supports_global_query=True))``.
  - ``_build_nifc_url(bbox)``: builds the FeatureServer query URL. When
    ``bbox=None``, omits ``geometry``/``geometryType``/``inSR`` and lets the
    service return the full active-perimeter set. When set, sends ArcGIS
    envelope semantics.
  - ``_fetch_nifc_geojson(url, params)``: httpx GET with descriptive User-Agent,
    30 s timeout, full typed-error mapping for network failure, HTTP ≥400,
    non-JSON body, ArcGIS 200-body error envelopes, and non-FeatureCollection
    responses → ``NIFCFireUpstreamError(retryable=True)``.
  - ``_geojson_to_fgb(geojson)``: GeoJSON → FlatGeobuf via geopandas/pyogrio.
    Preserves 13 properties (``poly_IncidentName``, ``poly_FeatureCategory``,
    ``poly_DateCurrent``, ``poly_GISAcres``, ``attr_IncidentSize``,
    ``attr_PercentContained``, ``attr_IncidentName``,
    ``attr_FireCauseGeneral``, ``attr_FireCause``, ``attr_POOState``,
    ``attr_IrwinID``, ``attr_UniqueFireIdentifier``, plus ``OBJECTID``).
    Drops null-geometry rows and emits a valid header-only FGB on empty input.
  - Typed errors: ``NIFCFireError`` (base), ``NIFCFireInputError``
    (retryable=False), ``NIFCFireUpstreamError`` (retryable=True),
    ``NIFCFireEmptyError`` (reserved for future strict-mode).
  - Cache key composed via the FR-DC-3 ``read_through`` shim with params
    ``{bbox: list[float] | None, status: str}`` against the ``dynamic-1h``
    TTL bucket — the CONUS-default call and a bbox call hash to distinct
    cache paths.
  - FR-TA-3 docstring (Use this when / Do NOT use this for / params / returns /
    cache / external-API resilience / source-tier / payload).

- **NEW** ``services/agent/tests/test_fetch_nifc_fire_perimeters.py``
  - 24 unit tests + 2 live tests (env-gated ``GRACE2_TEST_LIVE_NIFC=1``).
  - Registration test asserts ``supports_global_query is True``.

- **APPEND** ``services/agent/src/grace2_agent/tools/__init__.py`` — 1 import line.
- **APPEND** ``services/agent/src/grace2_agent/main.py`` — 1 import line in
  ``_import_tools_registry``.

## Decisions Made

- **Decision:** ``supports_global_query=True`` set on the tool's
  ``AtomicToolMetadata``.
  - **Rationale:** the field was a Wave 1.5 schema amendment originally
    surfaced as OQ-0110-GLOBAL-QUERY-FIELD because it did not exist on
    ``AtomicToolMetadata`` at the start of this job. Sibling job-0114
    landed the field during execution; setting it correctly here keeps
    the tool's discovery semantics aligned (``bbox=None`` truly is a global
    query) and avoids a one-line follow-up later.
  - **Alternatives:** leave unset (would mean the discovery layer can't
    safely route nationwide queries here). OQ-0110-GLOBAL-QUERY-FIELD
    resolved in-flight.

- **Decision:** ``status`` parameter validated + cache-keyed but currently
  no-op on the wire.
  - **Rationale:** NIFC's "Current" FeatureService exposes only active
    perimeters; reserving the parameter keeps the signature stable for a
    future archive-fetching variant. Surfaced as OQ-0110-STATUS-FILTER-NO-OP.

- **Decision:** Null-geometry rows dropped at conversion.
  - **Rationale:** a perimeter POLYGON layer cannot render null-geom rows;
    dropping is honest. Empty input still produces a valid header-only FGB.

- **Decision:** 13 preserved properties (vs the kickoff's 5).
  - **Rationale:** added ``OBJECTID``, ``poly_GISAcres``, ``attr_IncidentName``,
    ``attr_FireCauseGeneral``, ``attr_FireCause``, ``attr_POOState``,
    ``attr_IrwinID``, ``attr_UniqueFireIdentifier`` because downstream
    summary / claim-aggregation tools will want fire-cause + POO state for
    narration. FlatGeobuf cost is negligible (~50 bytes/feature).

## Invariants Touched

- **Invariant 1 (Determinism boundary):** preserves — typed ``LayerURI``;
  no prose-number return; FlatGeobuf carries narration metrics.
- **Invariant 2 (Deterministic workflows):** preserves — no LLM call.
- **Invariant 3 (Engine registration):** preserves — added via
  ``@register_tool``; no agent-core modification.
- **CRS hygiene:** preserves — explicit ``inSR=4326`` and ``outSR=4326``,
  ``crs="EPSG:4326"`` on the GeoDataFrame.
- **External-API resilience (NFR-R-1):** preserves — every failure mode
  mapped to typed errors with ``retryable`` flag.
- **Geographic-correctness gate (job-0086 codified):** preserves — synthetic
  (13-feature mixed CONUS+AK+HI) and live (52 real CONUS perimeters; 52/52
  inside envelope) tests assert centroids fall inside the (-180, 13, -65, 72)
  US-fires envelope.

## Open Questions

- **OQ-0110-GLOBAL-QUERY-FIELD:** RESOLVED in-flight — sibling job-0114
  landed the ``supports_global_query`` field on ``AtomicToolMetadata``
  during this job's execution; flag set to ``True`` on the tool.
- **OQ-0110-STATUS-FILTER-NO-OP:** ``status`` parameter accepted + validated
  + cache-keyed but no wire effect (NIFC "Current" scope is fixed). Tentative
  recommendation: defer until a real use case asks for historic perimeters
  — those are better served by a dedicated archive fetcher.

## Dependencies and Impacts

- **Depends on:** job-0031 (cache bucket layout), job-0032 (tool registry +
  ``read_through`` shim) — both ``complete/``. Sibling-completed job-0114
  enabled the ``supports_global_query`` flag.
- **Affects:** sibling Wave 1.5 jobs converging on the same registration
  sites (handled idempotently with pre-commit rebase mitigation). Downstream
  discovery / catalog layer can route global "all active wildfires" queries
  here via the ``supports_global_query=True`` flag.

## Verification

- **Tests run:**
  - ``services/agent/tests/test_fetch_nifc_fire_perimeters.py`` — 24 unit
    tests passed in 0.31 s; 2 live tests passed under
    ``GRACE2_TEST_LIVE_NIFC=1`` in 5.86 s.
  - Full agent test suite (run pre-supports_global_query update) — **702
    passed, 33 skipped, 4 warnings, no failures** in 380 s. No regressions.
  - Tool registry boot: ``main._import_tools_registry()`` returns 46
    registered tools; ``fetch_nifc_fire_perimeters`` present with expected
    metadata (``ttl_class='dynamic-1h'``, ``source_class='nifc_perimeters'``,
    ``cacheable=True``, ``supports_global_query=True``).

- **Live E2E evidence (verbatim transcript):** ``evidence/nifc_live.txt``
  - ``[LIVE NIFC] CONUS sweep returned 52 active perimeter(s)``
  - Top incidents: Shell (2822 acres), Russian bend (34), Hwy 82 (22419),
    Woodlawn (632), Rose Bay Canal (433).
  - Geographic gate: inside=52, outside=0 (100% inside the US-fires envelope).
  - ``[LIVE NIFC] Western-US bbox returned 27 active perimeter(s)`` — bbox
    correctly narrows the CONUS sweep.

- **Results:** pass.
