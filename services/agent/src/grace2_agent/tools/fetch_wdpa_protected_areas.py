"""``fetch_wdpa_protected_areas`` atomic tool — WDPA polygon fetcher (job-0089).

Queries the World Database on Protected Areas (UNEP-WCMC) via its ArcGIS REST
FeatureServer endpoint and returns a FlatGeobuf of the polygons clipped to
the requested bbox. No authentication required for read access.

The WDPA service publishes a single ``WDPA_v0`` FeatureServer with one layer
(``/0``) that contains the full global polygon corpus. The endpoint supports
spatial filtering via ``geometry`` + ``geometryType=esriGeometryEnvelope`` so
we constrain the response server-side to the bbox. Designation filtering
(``designation_filter``) is performed client-side on the returned features:
the WDPA mirror cluster is heterogeneous, server-side ``where=`` filters on
``DESIG_ENG`` can return different results across mirror nodes, and the
network round-trip cost of pulling the full bbox is dominated by the spatial
query itself — so we filter in Python for stability.

Tier-1 free fetcher (no API key). Cached with TTL ``static-30d`` since WDPA
publishes monthly updates and a 30-day stale window is acceptable for
hazard-modeling overlay use.

FR-TA-2: atomic tool, returns ``LayerURI``.
FR-CE-8 / FR-DC-3/4: routed through ``read_through`` so identical
``(bbox, designation_filter)`` calls reuse the cached FlatGeobuf.

URL convention (verified 2026-06-08 against the live UNEP-WCMC org):
    https://services5.arcgis.com/Mj0hjvkNtV7NRhA7/ArcGIS/rest/services/
        WDPA_v0/FeatureServer/1/query
    ?where=1=1
    &geometry={xmin,ymin,xmax,ymax}
    &geometryType=esriGeometryEnvelope
    &spatialRel=esriSpatialRelIntersects
    &inSR=4326
    &outFields=name_eng,desig_eng,iucn_cat,status,status_yr,site_id
    &outSR=4326
    &f=geojson
    &resultRecordCount=2000

NOTE on endpoint corrections (OQ-0089-WDPA-URL-CORRECTED): the audit.md
kickoff cited ``services3.arcgis.com`` + layer ``/0``. Probing the live
UNEP-WCMC ArcGIS Online org (orgId ``Mj0hjvkNtV7NRhA7``) at agent author
time revealed the FeatureServer is hosted on ``services5.arcgis.com`` and
layer ``/0`` is ``WDPA_point_Latest`` while layer ``/1`` is
``WDPA_poly_Latest`` — the polygon layer the kickoff intends. We corrected
both. The field names are lowercase in the live schema (``name_eng``,
``desig_eng``, ``iucn_cat``, ``status``, ``status_yr``, ``site_id``) and
``outFields=*`` returns HTTP 400 when combined with a ``geometry`` filter,
so we enumerate the columns explicitly.

Pagination uses ``resultOffset`` when the service indicates more features are
available (we detect this via ``exceededTransferLimit`` in the response).

OQ-0089-DESIGNATION-FILTER-SEMANTICS (TENTATIVE): designation_filter is an
exact-match list against ``desig_eng``. The WDPA designation vocabulary is
not fully standardized (e.g. "National Park" vs "National Parks"). For v0.1
we expose exact match; a future enrichment job can add a designation-alias
table if conservation tools surface false-negative complaints.

OQ-0089-WDPA-VERSION (TENTATIVE): the endpoint path embeds ``WDPA_v0`` which
is UNEP-WCMC's current "v0" service alias (continuously refreshed against
the monthly WDPA release; ``v1``/``v2``/``v4`` services co-exist as legacy
snapshots). The 30-day TTL window aligns with the monthly release cadence.
"""

from __future__ import annotations

import io
import json
import logging
import math
from typing import Any

import httpx

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = ["fetch_wdpa_protected_areas"]

logger = logging.getLogger("grace2_agent.tools.fetch_wdpa_protected_areas")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class WDPAError(RuntimeError):
    """Base class for fetch_wdpa_protected_areas failures.

    ``error_code`` maps to the WebSocket A.6 error frame emitted by the
    agent surface. ``retryable`` guides FR-AS-11 retry logic.
    """

    error_code: str = "WDPA_ERROR"
    retryable: bool = True


class WDPAUpstreamError(WDPAError):
    """WDPA ArcGIS REST query failed (network, HTTP, or parse error)."""

    error_code = "WDPA_UPSTREAM_ERROR"
    retryable = True


class WDPABboxError(WDPAError):
    """The bbox failed validation (degenerate, out of range, non-finite)."""

    error_code = "WDPA_BBOX_INVALID"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_WDPA_BASE = (
    "https://services5.arcgis.com/Mj0hjvkNtV7NRhA7/ArcGIS/rest/services/"
    "WDPA_v0/FeatureServer/1/query"
)

# WDPA OutFields we keep (live schema field names, lowercase). ``name_eng`` is
# the human-readable site name, ``desig_eng`` is the designation string used
# by ``designation_filter``, ``iucn_cat`` is the IUCN protected-area category,
# ``status`` / ``status_yr`` carry status + year of designation, ``site_id``
# is the stable WDPA identifier. ``outFields=*`` rejects with HTTP 400 when
# combined with a spatial filter, so we enumerate.
_WDPA_OUT_FIELDS = "name_eng,desig_eng,iucn_cat,status,status_yr,site_id"

#: The DESIG_ENG field name in the live schema (lowercase). Used by the
#: client-side designation_filter.
_WDPA_DESIG_FIELD = "desig_eng"

#: The NAME field name in the live schema (lowercase).
_WDPA_NAME_FIELD = "name_eng"

# Page size. WDPA's FeatureServer default cap is 2000 — request that
# explicitly so server-side defaults do not surprise us.
_PAGE_SIZE = 2000

# Per-request timeout. WDPA's ArcGIS REST cluster can be slow under load —
# the kickoff allots 60s.
_REQUEST_TIMEOUT = 60.0

# Safety cap on pagination iterations. 50 * 2000 = 100k features. A bbox
# returning more than that is almost certainly an unintentional global
# query; fail loudly rather than silently paginate forever.
_MAX_PAGES = 50

# User-Agent — UNEP-WCMC's terms ask for identifying agents.
_USER_AGENT = (
    "grace-2/0.1 (Hazard Modeling Agent; "
    "https://github.com/double-r-squared/GRACE-2; agent@grace-2.dev)"
)


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_wdpa_protected_areas",
    ttl_class="static-30d",
    source_class="wdpa",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# bbox helpers.
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Raise ``WDPABboxError`` if bbox is invalid."""
    if len(bbox) != 4:
        raise WDPABboxError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    min_lon, min_lat, max_lon, max_lat = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise WDPABboxError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise WDPABboxError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise WDPABboxError(f"bbox lat out of [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise WDPABboxError(
            f"bbox is degenerate (min must be < max on both axes): {bbox!r}"
        )


def _round_bbox_to_6dp(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Round bbox coordinates to 6 decimal places (~0.1m) for cache-key stability.

    Matching the audit.md cache-key spec: bbox-rounded-6dp + sorted
    designation_filter tuple.
    """
    return tuple(round(v, 6) for v in bbox)  # type: ignore[return-value]


def _bbox_to_envelope(bbox: tuple[float, float, float, float]) -> str:
    """Format a bbox as an ArcGIS ``geometryType=esriGeometryEnvelope`` string.

    ArcGIS REST envelope format is the literal ``xmin,ymin,xmax,ymax`` —
    no JSON wrapping when ``geometryType=esriGeometryEnvelope`` is set.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    return f"{min_lon},{min_lat},{max_lon},{max_lat}"


# ---------------------------------------------------------------------------
# WDPA HTTP fetch.
# ---------------------------------------------------------------------------


def _wdpa_query_one_page(
    bbox: tuple[float, float, float, float],
    offset: int,
) -> dict[str, Any]:
    """Fetch one page of the WDPA FeatureServer query, returning parsed GeoJSON.

    Returns the parsed response dict (the FeatureServer wraps GeoJSON in a
    standard envelope: ``{"type": "FeatureCollection", "features": [...],
    "exceededTransferLimit": bool}``).
    """
    params = {
        "where": "1=1",
        "geometry": _bbox_to_envelope(bbox),
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": _WDPA_OUT_FIELDS,
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": str(_PAGE_SIZE),
        "resultOffset": str(offset),
    }
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.get(
                _WDPA_BASE,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
    except httpx.RequestError as exc:
        raise WDPAUpstreamError(
            f"WDPA query failed (network) offset={offset}: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise WDPAUpstreamError(
            f"WDPA query returned HTTP {resp.status_code} offset={offset}: "
            f"{resp.text[:200]}"
        )

    try:
        payload = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise WDPAUpstreamError(
            f"WDPA returned non-JSON body offset={offset}: {exc}"
        ) from exc

    # ArcGIS REST surfaces errors inside a 200 envelope: {"error": {...}}.
    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        raise WDPAUpstreamError(
            f"WDPA query returned error envelope offset={offset}: {err}"
        )

    return payload


def _fetch_wdpa_features(
    bbox: tuple[float, float, float, float],
    designation_filter: list[str] | None,
) -> list[dict[str, Any]]:
    """Fetch all features in the bbox, paginating as needed.

    Applies ``designation_filter`` client-side after fetch.
    Returns a list of GeoJSON Feature dicts (possibly empty).
    """
    all_features: list[dict[str, Any]] = []
    offset = 0

    for page_idx in range(_MAX_PAGES):
        payload = _wdpa_query_one_page(bbox, offset)
        page_features = payload.get("features", []) or []
        all_features.extend(page_features)

        logger.info(
            "fetch_wdpa_protected_areas: page %d offset=%d -> %d feature(s) "
            "(total so far: %d)",
            page_idx,
            offset,
            len(page_features),
            len(all_features),
        )

        # WDPA tells us if more is available via exceededTransferLimit.
        # Some ArcGIS mirrors put this at the top of the GeoJSON envelope;
        # others nest it under "properties". Check both.
        more = bool(
            payload.get("exceededTransferLimit")
            or (payload.get("properties") or {}).get("exceededTransferLimit")
        )
        if not more:
            break
        if len(page_features) == 0:
            # Defensive: server says "more" but returned 0; avoid infinite loop.
            break
        offset += len(page_features)
    else:
        raise WDPAUpstreamError(
            f"WDPA pagination exceeded {_MAX_PAGES} pages for bbox={bbox}; "
            "bbox is probably too large — reduce bbox extent."
        )

    # Client-side designation filter (lowercase field name per the live schema).
    if designation_filter:
        filter_set = set(designation_filter)
        filtered = [
            f
            for f in all_features
            if (f.get("properties") or {}).get(_WDPA_DESIG_FIELD) in filter_set
        ]
        logger.info(
            "fetch_wdpa_protected_areas: designation_filter=%s reduced %d -> %d",
            designation_filter,
            len(all_features),
            len(filtered),
        )
        all_features = filtered

    return all_features


# ---------------------------------------------------------------------------
# Features -> FlatGeobuf bytes.
# ---------------------------------------------------------------------------


def _features_to_flatgeobuf(features: list[dict[str, Any]]) -> bytes:
    """Convert a list of GeoJSON Features to FlatGeobuf bytes via geopandas.

    An empty feature list is returned as an empty FlatGeobuf (still valid
    bytes) — callers (and the cache shim) treat that as a successful
    "no-features-in-bbox" response per the audit.md "Empty bbox over open
    water → 0 features without error" test.
    """
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise WDPAUpstreamError(
            f"geopandas not available for FlatGeobuf encode: {exc}"
        ) from exc

    if not features:
        # Empty geodataframe with the WDPA schema columns (lowercase field
        # names matching the live FeatureServer schema).
        empty_gdf = gpd.GeoDataFrame(
            {
                "name_eng": [],
                "desig_eng": [],
                "iucn_cat": [],
                "status": [],
                "status_yr": [],
                "site_id": [],
                "geometry": [],
            },
            crs="EPSG:4326",
        )
        buf = io.BytesIO()
        import tempfile
        import os as _os

        with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as tf:
            tmp_path = tf.name
        try:
            empty_gdf.to_file(tmp_path, driver="FlatGeobuf", engine="pyogrio")
            with open(tmp_path, "rb") as f:
                return f.read()
        except Exception as exc:  # noqa: BLE001
            raise WDPAUpstreamError(
                f"failed to write empty FlatGeobuf: {exc}"
            ) from exc
        finally:
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass

    # Build a FeatureCollection and let geopandas parse it.
    fc = {"type": "FeatureCollection", "features": features}
    try:
        gdf = gpd.GeoDataFrame.from_features(fc, crs="EPSG:4326")
    except Exception as exc:  # noqa: BLE001
        raise WDPAUpstreamError(
            f"geopandas could not parse WDPA features: {exc}"
        ) from exc

    import os as _os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as tf:
        tmp_path = tf.name
    try:
        gdf.to_file(tmp_path, driver="FlatGeobuf", engine="pyogrio")
        with open(tmp_path, "rb") as f:
            return f.read()
    except Exception as exc:  # noqa: BLE001
        raise WDPAUpstreamError(
            f"failed to write FlatGeobuf: {exc}"
        ) from exc
    finally:
        try:
            _os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Fetch function — builds the bytes callable for read_through.
# ---------------------------------------------------------------------------


def _fetch_wdpa_bytes(
    bbox: tuple[float, float, float, float],
    designation_filter: list[str] | None,
) -> bytes:
    """Download WDPA features, filter, and serialize to FlatGeobuf bytes."""
    features = _fetch_wdpa_features(bbox, designation_filter)
    return _features_to_flatgeobuf(features)


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
def fetch_wdpa_protected_areas(
    bbox: tuple[float, float, float, float],
    designation_filter: list[str] | None = None,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """Fetch World Database on Protected Areas (WDPA) polygons clipped to a bbox.

    **What it does:** Queries the UNEP-WCMC WDPA ArcGIS REST FeatureServer
    (``services5.arcgis.com/Mj0hjvkNtV7NRhA7``) with a spatial envelope filter,
    paginates all matching protected-area polygons into a FlatGeobuf, and
    optionally filters by designation type client-side. Global coverage,
    monthly WDPA releases, cached ``static-30d``. No API key required.

    **When to use:**
    - Agent needs protected-area boundaries for a study area — e.g. overlay
      National Parks or National Wildlife Refuges on a flood risk surface.
    - Workflow must compute the fraction of a hazard footprint that intersects
      protected lands (conservation-impact analysis).
    - User asks about biodiversity context inside vs outside protected status.
    - Filtering ``fetch_gbif_occurrences`` or ``fetch_inaturalist_observations``
      results by protected/unprotected designation.

    **When NOT to use:**
    - Parcel-level land ownership or cadastral boundaries (WDPA covers
      conservation designations only; use county assessor data for parcels).
    - Private conservation easements not registered with UNEP-WCMC.
    - Tribal lands (use TIGER AIANNH or a BIA dataset).
    - Single-point inside/outside test (fetch the bbox once, test locally).

    **Parameters:**
    - ``bbox`` (tuple): ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
      Example: ``(-82.0, 25.0, -80.0, 26.5)`` for Everglades region.
    - ``designation_filter`` (list[str] or None): exact-match list of
      ``desig_eng`` strings to retain, e.g.
      ``["National Park", "National Wildlife Refuge"]``. ``None`` returns all
      designations. Filter is applied client-side after spatial fetch.

    **Returns:**
    ``LayerURI(layer_type="vector", role="context", units=None)`` pointing at a
    FlatGeobuf with fields: ``name_eng``, ``desig_eng``, ``iucn_cat``,
    ``status``, ``status_yr``, ``site_id``. Empty bbox over open water returns
    a valid 0-feature FlatGeobuf (not an error).

    **Cross-tool dependencies:**
    - Pairs with: ``fetch_gbif_occurrences``, ``fetch_inaturalist_observations``
      (conservation layer context).
    - Upstream of: ``compute_zonal_statistics`` for inside/outside protected
      area summaries.
    - Complemented by: ``fetch_administrative_boundaries`` for jurisdictional
      boundary overlay.
    """
    _validate_bbox(bbox)

    # Quantize bbox to 6dp for cache-key stability (audit.md spec).
    q_bbox = _round_bbox_to_6dp(bbox)

    # Normalize designation_filter for cache-key stability: sort + dedupe,
    # treat None and empty list as the same (no filter).
    if designation_filter:
        df_normalized: list[str] | None = sorted(set(designation_filter))
    else:
        df_normalized = None

    params = {
        "bbox": list(q_bbox),
        "designation_filter": df_normalized,
    }

    result = read_through(
        metadata=_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_wdpa_bytes(q_bbox, df_normalized),
    )
    assert result.uri is not None, (
        "fetch_wdpa_protected_areas is cacheable; uri must be set by read_through"
    )

    # Layer name encodes the filter so multiple WDPA layers in the same panel
    # are distinguishable.
    if df_normalized:
        filter_label = " (" + ", ".join(df_normalized) + ")"
    else:
        filter_label = ""
    name = f"Protected Areas — WDPA{filter_label}"

    return LayerURI(
        layer_id=f"wdpa-{q_bbox[0]:.4f}-{q_bbox[1]:.4f}",
        name=name,
        layer_type="vector",
        uri=result.uri,
        style_preset="wdpa_protected_areas",
        role="context",
        units=None,
    )
