"""``fetch_nws_alerts_conus`` atomic tool — NWS CONUS-wide active alerts (job-0105).

CONUS-wide companion to ``fetch_nws_event`` (job-0090). Where ``fetch_nws_event``
takes ``area = state | county-FIPS | bbox``, this sibling fetches ALL active
alerts nationwide in a single call — the right tool for "show me current
warnings across America" use cases. NWS typically has ~500 active alerts
nationwide at any moment; the payload is small (~200KB), so a single CONUS
sweep is far cheaper than 50 state calls.

Endpoint:
    https://api.weather.gov/alerts/active?status={status}

No ``area`` filter is sent. ``event_types`` filtering is applied client-side
after fetch (preserving cache reuse across event-type filters of a single
CONUS sweep).

Cache: ``dynamic-1h`` — alerts change frequently; one-hour bucketing matches
the FR-DC-3 minimum window for active-state data.

Returns: ``LayerURI(layer_type="vector", role="primary", units=None)`` pointing
at a FlatGeobuf in the cache bucket of CONUS alert polygons + properties.

FR-TA-2 / FR-AS-3 docstring discipline applies; NWS REQUIRES a descriptive
``User-Agent`` (returns 403 otherwise).

Geographic-correctness check (job-0086 lesson, codified):
The live test verifies that every returned alert with a polygon geometry
falls inside the CONUS+territories envelope (or marine zones); a sign-flip
or axis-swap in the GeoJSON→FGB conversion would surface as features outside
that envelope.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

import httpx

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = [
    "fetch_nws_alerts_conus",
    "NWSConusError",
    "NWSConusInputError",
    "NWSConusUpstreamError",
    "NWSConusEmptyError",
    "_build_nws_conus_url",
    "_filter_features_by_event_types",
    "_geojson_to_fgb",
]

logger = logging.getLogger("grace2_agent.tools.fetch_nws_alerts_conus")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class NWSConusError(RuntimeError):
    """Base class for fetch_nws_alerts_conus failures.

    ``error_code`` maps to the WebSocket A.6 error frame the agent surface
    emits. ``retryable`` guides FR-AS-11 retry/clarify/fallback logic.
    """

    error_code: str = "NWS_CONUS_ERROR"
    retryable: bool = True


class NWSConusInputError(NWSConusError):
    """Caller passed an invalid ``status`` or non-string ``event_types``."""

    error_code = "NWS_CONUS_INPUT_INVALID"
    retryable = False


class NWSConusUpstreamError(NWSConusError):
    """api.weather.gov request failed (network, 5xx, malformed JSON).

    Marked retryable=True (transient NWS issues recover on retry; the agent's
    FR-AS-11 surface decides whether to actually re-issue).
    """

    error_code = "NWS_CONUS_UPSTREAM_ERROR"
    retryable = True


class NWSConusEmptyError(NWSConusError):
    """NWS returned an empty FeatureCollection — informational, not retryable.

    Empty results are LEGITIMATE for CONUS-wide queries during very quiet
    weather periods (rare but possible). Currently NOT raised by the tool
    body (we serialize an empty FGB instead), but kept available for future
    strict-mode opt-in.
    """

    error_code = "NWS_CONUS_EMPTY"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_NWS_BASE = "https://api.weather.gov"

# REQUIRED per NWS policy — without a descriptive User-Agent identifying the
# app + contact, NWS returns HTTP 403.
_USER_AGENT = (
    "grace2-agent/0.1 (Hazard Modeling Agent; contact: grace2-ops@local)"
)

# Valid status values per NWS alert schema.
_VALID_STATUSES = frozenset({"actual", "exercise", "system", "test", "draft"})

# Request timeout — CONUS sweep is larger than a single-state query but still
# small (~200KB); 30s is generous.
_HTTP_TIMEOUT_S = 30.0

# Properties preserved from each NWS alert feature (mirrors fetch_nws_event).
_PRESERVED_PROPERTIES = (
    "event", "headline", "description", "severity", "urgency", "certainty",
    "effective", "onset", "ends", "expires", "senderName", "sender",
    "category", "messageType", "status", "areaDesc", "instruction",
    "response", "id",
)


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
#
# NOTE on supports_global_query (kickoff): the Wave 1.5 schema amendment
# (job-0114) is adding ``supports_global_query: bool = False`` to
# AtomicToolMetadata. As of this job's authoring, that field does NOT exist
# in grace2_contracts.tool_registry.AtomicToolMetadata yet — passing it would
# raise pydantic ValidationError at import time and break the agent service.
# Surfaced as OQ-0105-GLOBAL-QUERY-FIELD. Once job-0114 lands, a one-line
# follow-up adds ``supports_global_query=True`` to this metadata literal.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_nws_alerts_conus",
    ttl_class="dynamic-1h",
    source_class="nws_alerts_conus",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# URL building.
# ---------------------------------------------------------------------------


def _build_nws_conus_url(status: str) -> str:
    """Build the api.weather.gov/alerts/active URL for the CONUS-wide sweep.

    No ``area`` filter is sent. ``event_types`` filtering is applied client-side
    after fetch so the CONUS payload can be reused across queries that differ
    only in event-type filter (same cache hit).

    Note: ``message_type`` is intentionally NOT sent — for CONUS sweeps,
    omitting it returns the full union of active alert/update messages, which
    matches the "show me all warnings nationwide" semantics.
    """
    # No urlencode needed — single param, ASCII value.
    return f"{_NWS_BASE}/alerts/active?status={status}"


# ---------------------------------------------------------------------------
# Client-side event_types filter.
# ---------------------------------------------------------------------------


def _filter_features_by_event_types(
    features: list[dict[str, Any]],
    event_types: list[str] | None,
) -> list[dict[str, Any]]:
    """Narrow ``features`` to those whose ``properties.event`` matches one of
    ``event_types``.

    Returns ``features`` unchanged when ``event_types`` is None or empty.
    Comparison is case-sensitive against the canonical NWS event-type strings
    (e.g. "Hurricane Warning", "Flood Watch") — NWS uses Title Case throughout.
    """
    if not event_types:
        return features
    allowed = {e.strip() for e in event_types if isinstance(e, str) and e.strip()}
    if not allowed:
        return features
    out: list[dict[str, Any]] = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        ev = props.get("event")
        if isinstance(ev, str) and ev in allowed:
            out.append(feat)
    return out


# ---------------------------------------------------------------------------
# Upstream call + GeoJSON → FlatGeobuf conversion.
# ---------------------------------------------------------------------------


def _fetch_nws_conus_geojson(url: str) -> dict[str, Any]:
    """GET the NWS CONUS-wide alerts URL with required headers; return parsed JSON.

    Raises:
        ``NWSConusUpstreamError``: network / 5xx / non-JSON / malformed body.
    """
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/geo+json",
    }
    logger.info("fetch_nws_alerts_conus: GET %s", url)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise NWSConusUpstreamError(
            f"NWS CONUS request failed url={url}: {exc}"
        ) from exc

    if resp.status_code == 403:
        raise NWSConusUpstreamError(
            f"NWS returned 403 — User-Agent header is required + must identify the app. "
            f"Sent: {_USER_AGENT!r}; url={url}"
        )
    if resp.status_code >= 400:
        raise NWSConusUpstreamError(
            f"NWS returned HTTP {resp.status_code} url={url}: {resp.text[:500]!r}"
        )

    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise NWSConusUpstreamError(
            f"NWS returned non-JSON url={url}: {exc}"
        ) from exc

    if not isinstance(body, dict) or body.get("type") != "FeatureCollection":
        raise NWSConusUpstreamError(
            f"NWS response is not a GeoJSON FeatureCollection url={url}: "
            f"type={body.get('type') if isinstance(body, dict) else type(body).__name__!r}"
        )

    return body


def _geojson_to_fgb(geojson: dict[str, Any]) -> bytes:
    """Convert an NWS GeoJSON FeatureCollection to FlatGeobuf bytes.

    Preserves ``_PRESERVED_PROPERTIES``. Features without a geometry (NWS
    sometimes returns alerts that have only zone/county references) are
    materialized with a NULL geometry so the property table is still preserved.

    Returns FlatGeobuf bytes (always non-empty: an empty FeatureCollection
    still yields a valid header-only FGB).
    """
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise NWSConusUpstreamError(
            f"geopandas not available for FlatGeobuf conversion: {exc}"
        ) from exc

    features = geojson.get("features", []) or []

    rows: list[dict[str, Any]] = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        row: dict[str, Any] = {}
        for key in _PRESERVED_PROPERTIES:
            v = props.get(key)
            # Coerce non-scalar values to JSON strings — FlatGeobuf needs
            # scalar column types per field.
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            row[key] = v
        row["geometry"] = feat.get("geometry")
        rows.append(row)

    if not rows:
        gdf = gpd.GeoDataFrame(
            {k: [] for k in _PRESERVED_PROPERTIES},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )
    else:
        gdf = gpd.GeoDataFrame.from_features(
            [
                {
                    "type": "Feature",
                    "properties": {k: r[k] for k in _PRESERVED_PROPERTIES},
                    "geometry": r["geometry"],
                }
                for r in rows
            ],
            crs="EPSG:4326",
        )

    # NWS CONUS responses commonly include features with NULL geometry
    # (alerts that carry only zone/county references). FlatGeobuf's spatial-
    # index code path rejects NULL geometries (pyogrio: "ICreateFeature: NULL
    # geometry not supported with spatial index"). For the CONUS sweep we
    # disable the spatial index whenever ANY null geometry is present so the
    # property table is preserved for downstream identify/style use cases.
    # Trade-off: read paths that rely on FGB's spatial index skip those rows.
    # Acceptable for a context overlay layer.
    has_null_geom = bool(len(gdf)) and bool(gdf.geometry.isna().any())

    tmp_fgb: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".fgb", delete=False, prefix="grace2_nws_conus_"
        ) as f:
            tmp_fgb = f.name
        try:
            if len(gdf) == 0 or has_null_geom:
                gdf.to_file(
                    tmp_fgb, driver="FlatGeobuf", engine="pyogrio",
                    SPATIAL_INDEX="NO",
                )
            else:
                gdf.to_file(tmp_fgb, driver="FlatGeobuf", engine="pyogrio")
        except Exception as exc:  # noqa: BLE001
            raise NWSConusUpstreamError(
                f"FlatGeobuf write failed for {len(gdf)} features: {exc}"
            ) from exc

        with open(tmp_fgb, "rb") as f:
            fgb_bytes = f.read()

        logger.info(
            "fetch_nws_alerts_conus: FlatGeobuf = %d bytes (%d feature(s))",
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


def _fetch_nws_alerts_conus_bytes(
    status: str,
    event_types: list[str] | None,
) -> bytes:
    """End-to-end fetcher: build URL → GET CONUS GeoJSON → client-side filter →
    convert to FlatGeobuf bytes.

    Wrapped in a single try so we never leak an httpx exception past the typed
    error boundary.
    """
    url = _build_nws_conus_url(status)
    geojson = _fetch_nws_conus_geojson(url)
    features = geojson.get("features", []) or []
    filtered = _filter_features_by_event_types(features, event_types)
    # Re-wrap into FeatureCollection for the converter.
    filtered_collection = {
        "type": "FeatureCollection",
        "features": filtered,
    }
    return _geojson_to_fgb(filtered_collection)


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(_METADATA)
def fetch_nws_alerts_conus(
    event_types: list[str] | None = None,
    status: str = "actual",
) -> LayerURI:
    """NWS active weather alerts — CONUS-wide companion to ``fetch_nws_event``.

    Use this when: the agent needs the current set of ALL active National
    Weather Service alerts across the United States in a single call — for
    example "show me every hurricane warning in America right now" or
    "summarize today's severe weather nationwide". One CONUS sweep returns
    typically ~500 active alerts (~200KB payload), making it far cheaper than
    iterating over 50 state-level ``fetch_nws_event`` calls.

    Do NOT use this for: state- or county-scoped queries (use
    ``fetch_nws_event`` with ``area=<state>`` or ``area=<FIPS>`` — same NWS
    surface but with server-side area filtering, smaller payload, and cache
    keyed per-state); historical alerts (use ``fetch_storm_events_db`` for
    NOAA Storm Events DB lookups instead — NWS active-alerts is current-only,
    typically 0-7 days); international alerts (NWS is US + territories +
    marine zones only).

    Params:
        event_types: Optional list of NWS event-type strings to filter to
            (e.g. ``["Hurricane Warning", "Flood Warning"]``). Filtering is
            applied CLIENT-SIDE after the CONUS fetch so the same cached
            CONUS sweep services multiple event-type queries within the
            one-hour cache window. When None / empty, returns ALL active
            alerts CONUS-wide. Match is case-sensitive against the canonical
            NWS event-type strings (NWS uses Title Case throughout).
        status: NWS alert status. Default ``"actual"`` (real alerts; never
            test/exercise/draft). Accepted: actual, exercise, system, test, draft.

    Returns:
        A ``LayerURI`` pointing at a FlatGeobuf in the cache bucket:
        ``gs://grace-2-hazard-prod-cache/cache/dynamic-1h/nws_alerts_conus/<key>.fgb``
        containing all active CONUS alert polygons + properties.
        ``layer_type="vector"``, ``role="primary"``, ``units=None``.

    Cache: ``dynamic-1h`` (FR-DC-2 active-state). Two identical calls inside
    the same hour-bucket reuse the cached FlatGeobuf; a one-hour boundary
    crossing forces a refresh. Because ``event_types`` filtering is client-side,
    a fresh ``event_types`` filter on the same status reuses the same cache key
    (status is the only server-affecting param) and re-filters the cached FGB.

    Note: in this Wave 1.5 implementation the cache key DOES include
    ``event_types_sorted`` because the cached artifact is the FILTERED FGB,
    not the raw CONUS sweep. A future refactor (deferred — see
    OQ-0105-CACHE-RAW-VS-FILTERED) could cache the raw sweep and re-filter
    on each hit, giving even better reuse across filter variations.

    Cache key: SHA-256 of ``(status, event_types_sorted, "dynamic-1h" vintage)``.

    External-API resilience (NFR-R-1): NWS rate-limits unauthenticated
    requests and REQUIRES a descriptive User-Agent header (returns 403
    otherwise — see ``_USER_AGENT``). On network failure / non-2xx /
    malformed JSON the tool raises ``NWSConusUpstreamError(retryable=True)``
    so the agent's FR-AS-11 surface can decide.

    Source-tier: FR-HEP-2 Tier 1 (federal agency authoritative source).
    Claims derived from this tool should be marked
    ``source_authority_tier=1`` in any ``ClaimSet`` aggregation.

    Payload estimation: ~0.2MB CONUS (typical ~500 alerts × ~400 bytes each).

    See also: ``fetch_nws_event`` for state-/county-/bbox-scoped queries.
    """
    # Validate status early — fixed enum on the NWS side. Bad values are caller
    # error, not retryable.
    if status not in _VALID_STATUSES:
        raise NWSConusInputError(
            f"status={status!r} not in {sorted(_VALID_STATUSES)}"
        )

    # Validate event_types shape.
    sorted_event_types: list[str] | None = None
    if event_types:
        if not all(isinstance(e, str) for e in event_types):
            raise NWSConusInputError(
                f"event_types must be list[str]; got {event_types!r}"
            )
        sorted_event_types = sorted({e.strip() for e in event_types if e.strip()})
        if not sorted_event_types:
            sorted_event_types = None

    params: dict[str, Any] = {
        "status": status,
        "event_types": sorted_event_types,
    }

    result = read_through(
        metadata=_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_nws_alerts_conus_bytes(status, sorted_event_types),
    )
    assert result.uri is not None, (
        "fetch_nws_alerts_conus is cacheable; uri must be set by read_through"
    )

    # LayerURI display name reflects the filter, if any, for diagnostics.
    if sorted_event_types:
        filter_label = ", ".join(sorted_event_types[:3])
        if len(sorted_event_types) > 3:
            filter_label += f", +{len(sorted_event_types) - 3} more"
        name = f"NWS Active Alerts — CONUS ({filter_label})"
        layer_id = (
            f"nws-conus-{status}-"
            + "-".join(t.replace(" ", "_") for t in sorted_event_types[:2])
        )
    else:
        name = "NWS Active Alerts — CONUS (all events)"
        layer_id = f"nws-conus-{status}-all"

    return LayerURI(
        layer_id=layer_id,
        name=name,
        layer_type="vector",
        uri=result.uri,
        style_preset="nws_alerts",  # shared with fetch_nws_event preset
        role="primary",
        units=None,
    )
