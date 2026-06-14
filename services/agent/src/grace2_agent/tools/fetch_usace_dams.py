"""``fetch_usace_dams`` atomic tool — USACE National Inventory of Dams (job-A5).

Wraps the U.S. Army Corps of Engineers (USACE) National Inventory of Dams
(NID) public ArcGIS REST FeatureService. Returns FlatGeobuf POINT geometries
of dam infrastructure together with the canonical NID attribute payload —
NIDID, name, owner type, dam type, primary purpose, dam height, year
completed, hazard potential classification, and assorted spillway / storage /
condition fields downstream tools (Pelicun damage assessment, flood-routing
workflows, levee/dam infrastructure overlays) consume.

The canonical NID FeatureServer at ``geospatial.sec.usace.army.mil`` requires
an USACE token for direct REST query. We therefore use the publicly mirrored
ESRI Living Atlas feature service that the NID program ships for
unauthenticated public consumption (verified 2026-06-09):

    https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/
        NID_v1/FeatureServer/0/query

Layer 0 is the dam point inventory; ``geometryType=esriGeometryPoint``,
``maxRecordCount=2000``. The schema preserves both the dam-condition rollup
(``HAZARD_POTENTIAL``, ``CONDITION_ASSESSMENT``, ``EAP_PREPARED``) and the
physical-structure fields the SFINCS / dam-break / inundation engines need
(``DAM_HEIGHT``, ``NID_STORAGE``, ``MAX_DISCHARGE``, ``DRAINAGE_AREA``).

Query parameters used:
    where=1=1
    geometry={bbox}            (omitted when bbox is None — CONUS sweep)
    geometryType=esriGeometryEnvelope
    inSR=4326
    outFields=<allow-list>
    outSR=4326
    f=geojson

Cache: ``static-30d`` (NID is a regulatory inventory; updates are quarterly
at fastest — a 30-day TTL matches FR-DC-2 static-state semantics).
``cacheable=True``; ``source_class="usace_nid_dams"``.

``supports_global_query=True`` (Wave 1.5 schema amendment): the bbox=None
semantics return the CONUS+AK+HI dam population (~91k features today),
which exceeds a single FeatureServer page. Pagination is implemented via
``resultOffset`` + ``resultRecordCount`` per the kickoff. The
catalog/discovery layer can route "show every dam in the US" queries here
without forcing a bbox parameter, although in practice the agent should
prefer narrowing by bbox or state to keep payload tractable — see
``estimate_payload_mb`` and the Wave-1.5 chat-warning gate, which fires
on the global sweep.

FR-DC-3/4: routed through ``read_through`` so identical bbox calls reuse
the cached FlatGeobuf. FR-AS-11: ``USACEDAMSError`` / sub-classes carry
``error_code`` + ``retryable`` for the agent's retry/clarify/fallback
surface. FR-TA-2 / FR-AS-3 docstring discipline applies.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from typing import Any

import httpx

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = [
    "fetch_usace_dams",
    "USACEDAMSError",
    "USACEDAMSInputError",
    "USACEDAMSUpstreamError",
    "USACEDAMSEmptyError",
    "estimate_payload_mb",
    "_build_nid_url",
    "_bbox_to_envelope",
    "_validate_bbox",
    "_round_bbox_to_6dp",
    "_fetch_nid_geojson_page",
    "_fetch_nid_all_features",
    "_geojson_to_fgb",
    "_fetch_nid_bytes",
    "CONUS_BBOX",
    "PRESERVED_PROPERTIES",
]

logger = logging.getLogger("grace2_agent.tools.fetch_usace_dams")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class USACEDAMSError(RuntimeError):
    """Base class for fetch_usace_dams failures.

    ``error_code`` maps to the WebSocket A.6 error frame emitted by the agent
    surface. ``retryable`` guides FR-AS-11 retry/clarify/fallback logic.
    """

    error_code: str = "USACE_DAMS_ERROR"
    retryable: bool = True


class USACEDAMSInputError(USACEDAMSError):
    """Caller passed an invalid bbox or unsupported parameter."""

    error_code = "USACE_DAMS_INPUT_INVALID"
    retryable = False


class USACEDAMSUpstreamError(USACEDAMSError):
    """USACE NID ArcGIS REST query failed (network, HTTP, or parse error)."""

    error_code = "USACE_DAMS_UPSTREAM_ERROR"
    retryable = True


class USACEDAMSEmptyError(USACEDAMSError):
    """NID returned an empty FeatureCollection — informational, not retryable.

    NOT raised by the tool body (we serialize an empty FGB instead — an empty
    bbox over open ocean / Antarctica is LEGITIMATE), but kept available for
    future strict-mode opt-in.
    """

    error_code = "USACE_DAMS_EMPTY"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# Public ESRI Living Atlas mirror of the USACE NID feature service. The
# authoritative ``geospatial.sec.usace.army.mil`` REST endpoint requires a
# token, but the NID program publishes this unauthenticated mirror for public
# consumption. Verified 2026-06-09.
_NID_BASE = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
    "NID_v1/FeatureServer/0/query"
)

# Properties preserved from each NID feature. The kickoff named the canonical
# NID dam-identification + physical + regulatory fields; we keep an explicit
# allow-list so future NID schema growth doesn't quietly bloat the wire
# payload, and so the FlatGeobuf column set is stable across versions.
PRESERVED_PROPERTIES: tuple[str, ...] = (
    # Identification.
    "OBJECTID",
    "NIDID",
    "FEDERAL_ID",
    "NAME",
    "OTHER_NAMES",
    "STATE",
    "COUNTYSTATE",
    "CITY",
    "LATITUDE",
    "LONGITUDE",
    "RIVER_OR_STREAM",
    "CONGDIST",
    # Ownership / regulation.
    "OWNER_TYPES",
    "PRIMARY_OWNER_TYPE",
    "STATE_REGULATED",
    "STATE_JURISDICTION",
    "STATE_REGULATORY_AGENCY",
    "PRIMARY_SOURCE_AGENCY",
    # Physical / structural.
    "PRIMARY_PURPOSE",
    "PURPOSES",
    "PRIMARY_DAM_TYPE",
    "DAM_TYPES",
    "DAM_HEIGHT",
    "HYDRAULIC_HEIGHT",
    "STRUCTURAL_HEIGHT",
    "NID_HEIGHT",
    "DAM_LENGTH",
    "DAM_VOLUME",
    "YEAR_COMPLETED",
    # Reservoir / hydrology.
    "NID_STORAGE",
    "MAX_STORAGE",
    "NORMAL_STORAGE",
    "SURFACE_AREA",
    "DRAINAGE_AREA",
    "MAX_DISCHARGE",
    "SPILLWAY_TYPE",
    "SPILLWAY_WIDTH",
    # Hazard / inspection.
    "HAZARD_POTENTIAL",
    "CONDITION_ASSESSMENT",
    "CONDITION_ASSESS_DATE",
    "EAP_PREPARED",
    "EAP_LAST_REV_DATE",
    "LAST_INSPECTION_DATE",
    "INSPECTION_FREQUENCY",
    "OPERATIONAL_STATUS",
    "OPERATIONAL_STATUS_DATE",
    "DATA_UPDATED",
)

# User-Agent — ESRI tracks unauthenticated clients; identify GRACE-2 clearly
# so the NID team can attribute traffic.
_USER_AGENT = (
    "grace-2/0.1 (Hazard Modeling Agent; "
    "https://github.com/double-r-squared/GRACE-2; agent@grace-2.dev)"
)

# Request timeout. NID's ArcGIS cluster usually responds <2s for a state-sized
# bbox; 30s matches the envelope we give other ArcGIS REST fetchers.
_HTTP_TIMEOUT_S = 30.0

# Server-enforced max page size on the NID FeatureService.
_NID_PAGE_SIZE = 2000

# CONUS+AK+HI envelope used as default bbox when caller passes None. Generous
# on the AK/HI side; matches the envelope used by ``fetch_nifc_fire_perimeters``.
CONUS_BBOX: tuple[float, float, float, float] = (-180.0, 13.0, -65.0, 72.0)

# Hard cap on number of features paginated in one call. The NID is ~91k
# features nationwide; we cap at 50k to keep the FlatGeobuf payload + GCS
# write tractable. Callers wanting larger sweeps should narrow by bbox.
_MAX_TOTAL_FEATURES = 50_000


# ---------------------------------------------------------------------------
# Payload estimator hook (Wave 1.5 / FR-DC-9).
# ---------------------------------------------------------------------------

# Empirical sizing: each NID feature serializes to ~1 KB of FlatGeobuf
# (point geometry + ~30 scalar attributes). A typical county-sized bbox
# pulls 20-200 dams (~0.05-0.2 MB); a state-sized bbox pulls 500-5000 dams
# (~0.5-5 MB); the CONUS sweep pulls ~91k dams (~90 MB before pagination
# cap). The estimator returns a scale-aware upper bound.
_BYTES_PER_FEATURE_ESTIMATE = 1024

# CONUS area (sq deg) used to scale the estimator by bbox area.
_CONUS_AREA_DEG = (CONUS_BBOX[2] - CONUS_BBOX[0]) * (CONUS_BBOX[3] - CONUS_BBOX[1])
_CONUS_FEATURE_COUNT_ESTIMATE = 91_000


def estimate_payload_mb(**args: Any) -> float:
    """FR-DC-9 / Wave-1.5 payload estimator (called by chat-warning gate).

    Scales the estimate by bbox area relative to CONUS. A None / missing
    bbox returns the CONUS sweep estimate (~90 MB before pagination cap,
    reported as ~50 MB to reflect the ``_MAX_TOTAL_FEATURES`` cap).

    The signature accepts ``**args`` to match the Wave-1.5 estimator
    convention (the chat-warning gate passes the tool's kwargs unchanged).
    """
    bbox = args.get("bbox")
    if bbox is None:
        # CONUS sweep — pagination cap of 50k features × 1KB ≈ 50 MB.
        return float(_MAX_TOTAL_FEATURES * _BYTES_PER_FEATURE_ESTIMATE) / (1024 * 1024)
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        # Caller passed garbage; bail out with the CONUS-sweep estimate
        # rather than raising — the estimator is advisory only.
        return float(_MAX_TOTAL_FEATURES * _BYTES_PER_FEATURE_ESTIMATE) / (1024 * 1024)
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return float(_MAX_TOTAL_FEATURES * _BYTES_PER_FEATURE_ESTIMATE) / (1024 * 1024)
    area = max(0.0, (max_lon - min_lon)) * max(0.0, (max_lat - min_lat))
    if _CONUS_AREA_DEG <= 0:
        return 0.1
    fraction = min(1.0, area / _CONUS_AREA_DEG)
    est_features = max(1, int(_CONUS_FEATURE_COUNT_ESTIMATE * fraction))
    est_bytes = est_features * _BYTES_PER_FEATURE_ESTIMATE
    return float(est_bytes) / (1024 * 1024)


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
#
# ``supports_global_query=True`` because the bbox=None semantics genuinely
# return the CONUS+AK+HI dam population. The Wave-1.5 chat-warning gate
# uses ``estimate_payload_mb`` to warn the user before a large sweep is
# committed.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_usace_dams",
    ttl_class="static-30d",
    source_class="usace_nid_dams",
    cacheable=True,
    supports_global_query=True,
    payload_mb_estimator_name="estimate_payload_mb",
)


# ---------------------------------------------------------------------------
# bbox helpers.
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Raise ``USACEDAMSInputError`` if bbox is invalid."""
    if len(bbox) != 4:
        raise USACEDAMSInputError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    min_lon, min_lat, max_lon, max_lat = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise USACEDAMSInputError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise USACEDAMSInputError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise USACEDAMSInputError(f"bbox lat out of [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise USACEDAMSInputError(
            f"bbox is degenerate (min must be < max on both axes): {bbox!r}"
        )


def _round_bbox_to_6dp(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Round bbox coordinates to 6 decimal places (~0.1m) for cache-key stability."""
    return tuple(round(v, 6) for v in bbox)  # type: ignore[return-value]


def _bbox_to_envelope(bbox: tuple[float, float, float, float]) -> str:
    """Format a bbox as an ArcGIS ``geometryType=esriGeometryEnvelope`` string.

    ArcGIS REST envelope format is the literal ``xmin,ymin,xmax,ymax`` —
    no JSON wrapping when ``geometryType=esriGeometryEnvelope`` is set.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    return f"{min_lon},{min_lat},{max_lon},{max_lat}"


# ---------------------------------------------------------------------------
# URL building.
# ---------------------------------------------------------------------------


def _build_nid_url(
    bbox: tuple[float, float, float, float] | None,
    *,
    result_offset: int = 0,
    result_record_count: int = _NID_PAGE_SIZE,
) -> tuple[str, dict[str, str]]:
    """Build the NID FeatureServer query URL + params dict.

    When ``bbox`` is None, the query omits the geometry filter and returns the
    full CONUS+AK+HI dam population (paginated).  When a bbox is given, it is
    converted to ``esriGeometryEnvelope`` + ``inSR=4326`` server-side spatial
    filter.

    ``result_offset`` + ``result_record_count`` drive the pagination loop in
    ``_fetch_nid_all_features``. The NID FeatureServer enforces
    ``maxRecordCount=2000``; requesting more is silently truncated.
    """
    out_fields = ",".join(PRESERVED_PROPERTIES)
    params: dict[str, str] = {
        "where": "1=1",
        "outFields": out_fields,
        "outSR": "4326",
        "f": "geojson",
        "resultOffset": str(result_offset),
        "resultRecordCount": str(min(result_record_count, _NID_PAGE_SIZE)),
        # orderByFields gives the pagination a stable cursor — without it,
        # ArcGIS occasionally drops rows across page boundaries.
        "orderByFields": "OBJECTID ASC",
    }
    if bbox is not None:
        params["geometry"] = _bbox_to_envelope(bbox)
        params["geometryType"] = "esriGeometryEnvelope"
        params["spatialRel"] = "esriSpatialRelIntersects"
        params["inSR"] = "4326"
    return _NID_BASE, params


# ---------------------------------------------------------------------------
# NID HTTP fetch — single page.
# ---------------------------------------------------------------------------


def _fetch_nid_geojson_page(
    url: str,
    params: dict[str, str],
) -> dict[str, Any]:
    """GET one page of the NID FeatureServer query and return parsed GeoJSON.

    Raises:
        ``USACEDAMSUpstreamError``: network / 5xx / non-JSON / error-envelope /
        non-FeatureCollection response.
    """
    logger.info(
        "fetch_usace_dams: GET %s offset=%s count=%s",
        url,
        params.get("resultOffset"),
        params.get("resultRecordCount"),
    )
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
    except httpx.HTTPError as exc:
        raise USACEDAMSUpstreamError(
            f"USACE NID request failed url={url}: {exc}"
        ) from exc

    if resp.status_code >= 400:
        raise USACEDAMSUpstreamError(
            f"USACE NID returned HTTP {resp.status_code} url={url}: {resp.text[:500]!r}"
        )

    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise USACEDAMSUpstreamError(
            f"USACE NID returned non-JSON url={url}: {exc}"
        ) from exc

    if not isinstance(body, dict):
        raise USACEDAMSUpstreamError(
            f"USACE NID response is not a JSON object url={url}: "
            f"type={type(body).__name__!r}"
        )

    if "error" in body:
        raise USACEDAMSUpstreamError(
            f"USACE NID query returned error envelope url={url}: {body['error']}"
        )

    if body.get("type") != "FeatureCollection":
        raise USACEDAMSUpstreamError(
            f"USACE NID response is not a GeoJSON FeatureCollection url={url}: "
            f"type={body.get('type')!r}"
        )

    return body


# ---------------------------------------------------------------------------
# Pagination loop.
# ---------------------------------------------------------------------------


def _fetch_nid_all_features(
    bbox: tuple[float, float, float, float] | None,
    *,
    max_features: int = _MAX_TOTAL_FEATURES,
) -> dict[str, Any]:
    """Page through the NID FeatureService, accumulating up to ``max_features``.

    Returns a single GeoJSON FeatureCollection assembled from all pages.
    Stops when a page returns fewer features than the page size, when
    ``max_features`` is reached, or when the cumulative feature count
    exceeds the cap.

    Raises ``USACEDAMSUpstreamError`` if any page errors.
    """
    accumulated: list[dict[str, Any]] = []
    offset = 0
    while True:
        url, params = _build_nid_url(
            bbox,
            result_offset=offset,
            result_record_count=_NID_PAGE_SIZE,
        )
        page = _fetch_nid_geojson_page(url, params)
        page_features = page.get("features") or []
        accumulated.extend(page_features)
        logger.debug(
            "fetch_usace_dams: page offset=%d returned %d features (total %d)",
            offset,
            len(page_features),
            len(accumulated),
        )
        if len(page_features) < _NID_PAGE_SIZE:
            # Last page (server returned a short page).
            break
        if len(accumulated) >= max_features:
            logger.warning(
                "fetch_usace_dams: hit max_features=%d cap; truncating sweep",
                max_features,
            )
            accumulated = accumulated[:max_features]
            break
        offset += _NID_PAGE_SIZE
    return {"type": "FeatureCollection", "features": accumulated}


# ---------------------------------------------------------------------------
# GeoJSON -> FlatGeobuf conversion.
# ---------------------------------------------------------------------------


def _geojson_to_fgb(geojson: dict[str, Any]) -> bytes:
    """Convert a NID GeoJSON FeatureCollection to FlatGeobuf bytes.

    Preserves ``PRESERVED_PROPERTIES``. Features without a point geometry
    are dropped (NID is by definition a point inventory; null-geom rows
    are junk for this layer). Always emits a valid FlatGeobuf — an empty
    input yields a header-only FGB.
    """
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise USACEDAMSUpstreamError(
            f"geopandas not available for FlatGeobuf conversion: {exc}"
        ) from exc

    features = geojson.get("features", []) or []

    cleaned: list[dict[str, Any]] = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry")
        if geom is None:
            continue
        props = feat.get("properties") or {}
        row_props: dict[str, Any] = {}
        for key in PRESERVED_PROPERTIES:
            v = props.get(key)
            # Coerce non-scalar values to JSON strings — FlatGeobuf needs
            # scalar column types per field.
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            row_props[key] = v
        cleaned.append({
            "type": "Feature",
            "properties": row_props,
            "geometry": geom,
        })

    if not cleaned:
        gdf = gpd.GeoDataFrame(
            {k: [] for k in PRESERVED_PROPERTIES},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )
    else:
        gdf = gpd.GeoDataFrame.from_features(cleaned, crs="EPSG:4326")
        gdf = gdf.dropna(subset=["geometry"]).copy()

    tmp_fgb: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".fgb", delete=False, prefix="grace2_usace_dams_"
        ) as f:
            tmp_fgb = f.name
        try:
            gdf.to_file(tmp_fgb, driver="FlatGeobuf", engine="pyogrio")
        except Exception as exc:  # noqa: BLE001
            raise USACEDAMSUpstreamError(
                f"FlatGeobuf write failed for {len(gdf)} features: {exc}"
            ) from exc

        with open(tmp_fgb, "rb") as f:
            fgb_bytes = f.read()

        logger.info(
            "fetch_usace_dams: FlatGeobuf = %d bytes (%d feature(s))",
            len(fgb_bytes),
            len(gdf),
        )
        return fgb_bytes
    finally:
        if tmp_fgb is not None:
            try:
                os.unlink(tmp_fgb)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# End-to-end fetcher (pagination → GeoJSON → FGB bytes).
# ---------------------------------------------------------------------------


def _fetch_nid_bytes(
    bbox: tuple[float, float, float, float] | None,
) -> bytes:
    """Run pagination + conversion: bbox → FlatGeobuf bytes."""
    geojson = _fetch_nid_all_features(bbox)
    return _geojson_to_fgb(geojson)


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(
    _METADATA,
    # Annotations: readOnlyHint=True (read-only; no state mutation),
    # openWorldHint=True (calls external public API endpoint),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_usace_dams(
    bbox: tuple[float, float, float, float] | None = None,
    # Reserved for future hazard / state filter wire-side narrowing. v0.1
    # keeps the surface minimal — the bbox is the canonical narrow knob.
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """USACE National Inventory of Dams (NID) as a FlatGeobuf point layer.

    What it does:
        Fetches U.S. Army Corps of Engineers National Inventory of Dams
        records as point features with full NID attribute payload —
        identification, ownership, physical structure (height, length,
        storage, drainage area), hazard potential classification,
        condition assessment, and emergency-action-plan status.
        Wraps the public ESRI Living Atlas mirror of the NID FeatureService;
        the authoritative ``geospatial.sec.usace.army.mil`` REST endpoint
        requires a USACE token and is NOT used at v0.1.

    When to use:
        - User asks about dams in a region ("what dams are upstream of X?",
          "show me every high-hazard dam in California").
        - Flood-modeling workflow needs upstream dam locations / spillway
          capacity / storage volume to gauge dam-break or controlled-release
          scenarios.
        - Damage / risk assessment needs to overlay critical infrastructure
          (high-hazard-potential dams) on hazard footprints.
        - Pelicun building / asset analysis needs dam infrastructure context.

    When NOT to use:
        - DO NOT use for levees — use a future ``fetch_usace_nld_levees``
          tool (National Levee Database is a sibling but separate inventory).
        - DO NOT use for building structures — use ``fetch_usace_nsi``
          (National Structure Inventory) for Pelicun assets.
        - DO NOT use for downstream hydrologic routing — query NHD via
          ``fetch_river_geometry`` or NWM streamflow forecasts separately.
        - DO NOT use for non-US dams — NID is US-only.
        - DO NOT use for live reservoir operations data — NID is a static
          inventory; CWMS / USGS NWIS handle real-time reservoir levels.

    Parameters:
        bbox: Optional ``(min_lon, min_lat, max_lon, max_lat)`` envelope in
            EPSG:4326. Type: 4-float tuple, lon/lat ordered min-then-max
            on each axis. Example: ``(-82.5, 26.0, -81.0, 27.0)`` for the
            Fort Myers / Cape Coral area returns ~10-20 dam features.
            When None, the tool sweeps the full CONUS+AK+HI dam population
            (capped at 50k features); the Wave-1.5 chat-warning gate uses
            ``estimate_payload_mb`` to warn the user before a global sweep
            commits.

    Returns:
        A ``LayerURI`` pointing at a FlatGeobuf in the cache bucket
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/usace_nid_dams/<key>.fgb``
        containing point geometries (``Point`` in EPSG:4326) and the
        canonical NID attribute schema — ``NIDID``, ``NAME``, ``STATE``,
        ``DAM_HEIGHT``, ``NID_STORAGE``, ``HAZARD_POTENTIAL``,
        ``CONDITION_ASSESSMENT``, ``EAP_PREPARED``, ``YEAR_COMPLETED``,
        ``PRIMARY_DAM_TYPE``, ``PRIMARY_PURPOSE``, etc. Downstream tools
        consume ``NIDID`` (join key), ``HAZARD_POTENTIAL`` (filter), and
        ``NID_STORAGE`` / ``DAM_HEIGHT`` (sizing). ``layer_type="vector"``,
        ``role="primary"``, ``units=None``.

    Cross-tool dependencies:
        Consumes optional bbox from ``fetch_administrative_boundaries`` /
        ``geocode_location`` (typical agent workflow: geocode "Lake Mead" →
        derive bbox → call this tool). Feeds into ``clip_vector_to_polygon``
        (clip dams to watershed / county / Case AOI), and into
        ``compute_zonal_statistics`` / Pelicun composers that pair dam
        location with hazard footprints from ``run_model_flood_scenario``.

    Cache: ``static-30d`` (NID is updated quarterly at fastest; a 30-day
    bucket gives ~12x amortization). Cache key: SHA-256 of bbox-rounded-6dp
    or "global" sentinel.

    External-API resilience (NFR-R-1): The ESRI Living Atlas cluster
    occasionally returns 5xx during ESRI maintenance windows. On network
    failure / non-2xx / malformed JSON / ArcGIS error envelope the tool
    raises ``USACEDAMSUpstreamError(retryable=True)`` so the agent's
    FR-AS-11 surface decides whether to retry, clarify, or fall back.

    Source-tier: FR-HEP-2 Tier 1 (USACE is the regulatory authority for
    the NID). Claims derived from this tool should be marked
    ``source_authority_tier=1`` in any ``ClaimSet`` aggregation.
    """
    # bbox quantization for cache-key stability + pre-flight validation.
    q_bbox: tuple[float, float, float, float] | None
    if bbox is None:
        q_bbox = None
    else:
        _validate_bbox(bbox)
        q_bbox = _round_bbox_to_6dp(bbox)

    params = {
        "bbox": list(q_bbox) if q_bbox is not None else None,
    }

    result = read_through(
        metadata=_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_nid_bytes(q_bbox),
    )
    assert result.uri is not None, (
        "fetch_usace_dams is cacheable; uri must be set by read_through"
    )

    if q_bbox is None:
        name = "USACE National Inventory of Dams — CONUS+AK+HI"
        layer_id = "usace-nid-dams-global"
    else:
        name = (
            f"USACE National Inventory of Dams — bbox "
            f"({q_bbox[0]:.2f},{q_bbox[1]:.2f},{q_bbox[2]:.2f},{q_bbox[3]:.2f})"
        )
        layer_id = (
            f"usace-nid-dams-{q_bbox[0]:.4f}-{q_bbox[1]:.4f}"
        )

    return LayerURI(
        layer_id=layer_id,
        name=name,
        layer_type="vector",
        uri=result.uri,
        style_preset="usace_nid_dams",
        role="primary",
        units=None,
    )
