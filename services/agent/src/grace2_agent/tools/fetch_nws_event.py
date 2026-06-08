"""``fetch_nws_event`` atomic tool — NWS active alerts/events fetcher (job-0090).

Wraps the National Weather Service ``api.weather.gov/alerts/active`` endpoint
and emits FlatGeobuf polygons + properties (severity, headline, event, onset,
ends, description, ...). Tier-1 free (no API key required); a descriptive
``User-Agent`` header is REQUIRED by NWS or the API returns 403.

Usage modes (``area`` polymorphism):

- 2-letter US state code ("FL", "TX", ...) → ``?area={STATE}``
- US county FIPS (5-digit string, e.g. "12071" for Lee County, FL) → ``?area=FIPS``
- bbox tuple ``(min_lon, min_lat, max_lon, max_lat)`` (EPSG:4326) → converted
  to a point center (lat, lon) and passed as ``?point={lat},{lon}`` for the
  zone lookup (NWS does not accept bbox queries directly; point lookup returns
  all alerts whose forecast zones contain that point).

Cache: ``dynamic-1h`` (FR-DC-2 active-state) — alerts change frequently, but
a one-hour bucket is the FR-DC-3 minimum window and keeps repeat queries
inside a short demo / research session cheap.

Cache key: SHA-256 of ``(area_canonicalized, event_types_sorted, status,
message_type)`` — see ``read_through`` for the full canonicalization rules.

Returns: ``LayerURI(layer_type="vector", role="context", units=None)`` pointing
at a FlatGeobuf in the cache bucket containing the alert polygons + properties.

FR-TA-2 / FR-AS-3 docstring discipline applies.

Geographic-correctness check (job-0086 lesson, codified):
The live test verifies that bbox→point conversion produces the EXACT center
of the input bbox (algebraic identity, not just round-trip), so a sign-flip
or axis-swap bug in the point computation surfaces as a wrong polygon.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import urllib.parse
from typing import Any

import httpx

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = [
    "fetch_nws_event",
    "NWSError",
    "NWSUpstreamError",
    "NWSInputError",
    "NWSEmptyError",
    "_bbox_to_point_center",
    "_canonicalize_area",
    "_build_nws_url",
]

logger = logging.getLogger("grace2_agent.tools.fetch_nws_event")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class NWSError(RuntimeError):
    """Base class for fetch_nws_event failures.

    ``error_code`` maps to the WebSocket A.6 error frame the agent surface
    emits. ``retryable`` guides FR-AS-11 retry/clarify/fallback logic.
    """

    error_code: str = "NWS_EVENT_ERROR"
    retryable: bool = True


class NWSInputError(NWSError):
    """Caller passed an invalid ``area``/``event_types``/``status``/``message_type``."""

    error_code = "NWS_EVENT_INPUT_INVALID"
    retryable = False


class NWSUpstreamError(NWSError):
    """api.weather.gov request failed (network, 5xx, malformed JSON).

    Marked retryable=True per audit.md (transient NWS issues recover on retry;
    the agent FR-AS-11 surface decides whether to actually re-issue).
    """

    error_code = "NWS_EVENT_UPSTREAM_ERROR"
    retryable = True


class NWSEmptyError(NWSError):
    """NWS returned an empty FeatureCollection — informational, not retryable.

    Empty results are LEGITIMATE for `fetch_nws_event` (no active alerts in
    the requested area is the most common steady state). Tests and callers
    treat this as a valid response, not an error — but it's surfaced as a
    typed subclass so consumers that DO want to assert non-emptiness can.

    Currently NOT raised by the tool body (we serialize an empty FGB instead),
    but kept available for future strict-mode opt-in.
    """

    error_code = "NWS_EVENT_EMPTY"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_NWS_BASE = "https://api.weather.gov"

# REQUIRED per NWS policy — without a descriptive User-Agent identifying the
# app + contact, NWS returns HTTP 403. The kickoff spec calls this out.
_USER_AGENT = (
    "grace2-agent/0.1 (Hazard Modeling Agent; contact: grace2-ops@local)"
)

# Valid status values per NWS alert schema.
_VALID_STATUSES = frozenset({"actual", "exercise", "system", "test", "draft"})

# Valid messageType values per NWS alert schema.
_VALID_MESSAGE_TYPES = frozenset({"alert", "update", "cancel"})

# 2-letter US state codes (50 + DC + 5 territories) accepted by /alerts/active.
_VALID_STATE_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
    # Territories
    "AS", "GU", "MP", "PR", "VI",
    # Marine zones
    "PZ", "PK", "PH", "PS", "PM", "AN", "AM", "GM", "LS", "LM", "LH", "LC", "LE", "LO",
})

# 5-digit FIPS code pattern.
_FIPS_PATTERN = re.compile(r"^\d{5}$")

# Request timeout per audit.md.
_HTTP_TIMEOUT_S = 30.0

# Properties preserved from each NWS alert feature (audit.md spec).
# We keep the FULL set of NWS-documented properties so downstream visualization
# / styling has everything; the audit list is the MINIMUM.
_PRESERVED_PROPERTIES = (
    "event", "headline", "description", "severity", "urgency", "certainty",
    "effective", "onset", "ends", "expires", "senderName", "sender",
    "category", "messageType", "status", "areaDesc", "instruction",
    "response", "id",
)


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_nws_event",
    ttl_class="dynamic-1h",
    source_class="nws_event",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# Area canonicalization + URL building.
# ---------------------------------------------------------------------------


def _bbox_to_point_center(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Return the (lat, lon) center of ``bbox = (min_lon, min_lat, max_lon, max_lat)``.

    Algebraic identity:
        lat_center = (min_lat + max_lat) / 2
        lon_center = (min_lon + max_lon) / 2

    Per the codified job-0086 lesson, the GEOGRAPHIC correctness of this
    function is what unit tests assert — NOT just "did the bytes survive".
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lat_center = (min_lat + max_lat) / 2.0
    lon_center = (min_lon + max_lon) / 2.0
    return (lat_center, lon_center)


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Raise ``NWSInputError`` if bbox is invalid."""
    if len(bbox) != 4:
        raise NWSInputError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    min_lon, min_lat, max_lon, max_lat = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise NWSInputError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise NWSInputError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise NWSInputError(f"bbox lat out of [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise NWSInputError(
            f"bbox degenerate (min must be < max on both axes): {bbox!r}"
        )


def _canonicalize_area(
    area: str | tuple[float, float, float, float],
) -> dict[str, Any]:
    """Reduce ``area`` to a stable {kind, value, ...} dict for cache-keying + URL building.

    Returns one of:
        {"kind": "state", "value": "FL"}
        {"kind": "fips", "value": "12071"}
        {"kind": "point", "lat": 26.6, "lon": -81.8, "bbox": [...]}
    """
    if isinstance(area, tuple):
        _validate_bbox(area)
        lat, lon = _bbox_to_point_center(area)
        # Round to 4dp (~11m) for cache-key stability — NWS zones are
        # much coarser than that, so the snap loses no useful precision.
        return {
            "kind": "point",
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "bbox": [round(v, 6) for v in area],
        }
    if isinstance(area, str):
        s = area.strip().upper()
        if _FIPS_PATTERN.match(s):
            return {"kind": "fips", "value": s}
        if s in _VALID_STATE_CODES:
            return {"kind": "state", "value": s}
        raise NWSInputError(
            f"area={area!r} is not a recognized 2-letter state code, "
            f"5-digit county FIPS, or bbox tuple"
        )
    raise NWSInputError(
        f"area must be str (state code or FIPS) or tuple bbox; got {type(area).__name__}"
    )


def _build_nws_url(
    canon_area: dict[str, Any],
    event_types: list[str] | None,
    status: str,
    message_type: str,
) -> str:
    """Build the api.weather.gov/alerts/active URL for the canonicalized area.

    NWS supports repeatable ``&event=`` params for filtering; we URL-encode
    each. ``status`` and ``message_type`` are validated by the tool body.
    """
    params: list[tuple[str, str]] = []
    if canon_area["kind"] == "state":
        params.append(("area", canon_area["value"]))
    elif canon_area["kind"] == "fips":
        # NWS treats FIPS the same as state code via ?area= (zone lookup).
        params.append(("area", canon_area["value"]))
    elif canon_area["kind"] == "point":
        params.append(
            ("point", f"{canon_area['lat']},{canon_area['lon']}")
        )
    else:  # pragma: no cover — _canonicalize_area only emits the three above
        raise NWSInputError(f"unknown canon_area kind: {canon_area['kind']!r}")

    params.append(("status", status))
    params.append(("message_type", message_type))

    if event_types:
        for et in event_types:
            params.append(("event", et))

    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"{_NWS_BASE}/alerts/active?{query}"


# ---------------------------------------------------------------------------
# Upstream call + GeoJSON → FlatGeobuf conversion.
# ---------------------------------------------------------------------------


def _fetch_nws_geojson(url: str) -> dict[str, Any]:
    """GET the NWS alerts URL with the required headers; return parsed JSON.

    Raises:
        ``NWSUpstreamError``: network / 5xx / non-JSON / malformed body.
    """
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/geo+json",
    }
    logger.info("fetch_nws_event: GET %s", url)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise NWSUpstreamError(
            f"NWS request failed url={url}: {exc}"
        ) from exc

    if resp.status_code == 403:
        raise NWSUpstreamError(
            f"NWS returned 403 — User-Agent header is required + must identify the app. "
            f"Sent: {_USER_AGENT!r}; url={url}"
        )
    if resp.status_code >= 400:
        raise NWSUpstreamError(
            f"NWS returned HTTP {resp.status_code} url={url}: {resp.text[:500]!r}"
        )

    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise NWSUpstreamError(
            f"NWS returned non-JSON url={url}: {exc}"
        ) from exc

    if not isinstance(body, dict) or body.get("type") != "FeatureCollection":
        raise NWSUpstreamError(
            f"NWS response is not a GeoJSON FeatureCollection url={url}: type={body.get('type') if isinstance(body, dict) else type(body).__name__!r}"
        )

    return body


def _geojson_to_fgb(geojson: dict[str, Any]) -> bytes:
    """Convert an NWS GeoJSON FeatureCollection to FlatGeobuf bytes.

    Preserves the audit.md-listed properties (event, headline, severity, ...)
    plus the rest of the NWS-documented fields. Features WITHOUT a geometry
    (NWS sometimes returns alerts that have only zone/county references) are
    materialized with a NULL geometry so the property table is still preserved
    — FlatGeobuf supports null geometries.

    Returns FlatGeobuf bytes (always non-empty: an empty FeatureCollection
    still yields a valid header-only FGB).
    """
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise NWSUpstreamError(
            f"geopandas not available for FlatGeobuf conversion: {exc}"
        ) from exc

    features = geojson.get("features", []) or []

    # Build a list of records that geopandas can ingest. We trim each feature's
    # properties to the preserved-set + 'geometry' so we don't bloat the FGB
    # with unbounded NWS fields. Missing properties become None.
    rows: list[dict[str, Any]] = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        row: dict[str, Any] = {}
        for key in _PRESERVED_PROPERTIES:
            v = props.get(key)
            # NWS sometimes returns nested objects/arrays in properties (e.g.
            # parameters, geocode). Coerce non-scalar values to JSON strings
            # so geopandas/pyogrio can write them — FlatGeobuf needs scalar
            # column types per field.
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            row[key] = v
        row["geometry"] = feat.get("geometry")
        rows.append(row)

    if not rows:
        # Empty FeatureCollection — emit a minimal valid FGB with one row of
        # all-None and immediately filter it out. pyogrio refuses to write a
        # truly empty layer, so we use a sentinel approach: build a 1-row gdf,
        # then write only if non-empty; else write a zero-feature placeholder
        # JSON-shape that the cache still preserves. This mirrors what the
        # cache_path slot expects.
        # Simplest robust path: serialize empty as an empty GeoDataFrame
        # with the geometry column declared; pyogrio handles this.
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

    tmp_fgb: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".fgb", delete=False, prefix="grace2_nws_"
        ) as f:
            tmp_fgb = f.name
        try:
            # pyogrio is the geopandas default writer; FlatGeobuf is its
            # native fast path. Use SPATIAL_INDEX=NO for empty layers
            # (pyogrio errors if the input has zero features and we request
            # a spatial index).
            if len(gdf) == 0:
                gdf.to_file(
                    tmp_fgb, driver="FlatGeobuf", engine="pyogrio",
                    SPATIAL_INDEX="NO",
                )
            else:
                gdf.to_file(tmp_fgb, driver="FlatGeobuf", engine="pyogrio")
        except Exception as exc:  # noqa: BLE001
            raise NWSUpstreamError(
                f"FlatGeobuf write failed for {len(gdf)} features: {exc}"
            ) from exc

        with open(tmp_fgb, "rb") as f:
            fgb_bytes = f.read()

        logger.info(
            "fetch_nws_event: FlatGeobuf = %d bytes (%d feature(s))",
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


def _fetch_nws_event_bytes(
    canon_area: dict[str, Any],
    event_types: list[str] | None,
    status: str,
    message_type: str,
) -> bytes:
    """End-to-end fetcher: build URL → GET JSON → convert to FlatGeobuf bytes.

    Wrapped in a single try so we never leak an httpx exception past the typed
    error boundary.
    """
    url = _build_nws_url(canon_area, event_types, status, message_type)
    geojson = _fetch_nws_geojson(url)
    return _geojson_to_fgb(geojson)


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(_METADATA)
def fetch_nws_event(
    area: str | tuple[float, float, float, float],
    event_types: list[str] | None = None,
    status: str = "actual",
    message_type: str = "alert",
) -> LayerURI:
    """NWS active alerts/events Tier-1 fetcher.

    Use this when: the agent needs the current set of active National Weather
    Service alerts (hurricane warnings, flood warnings, severe thunderstorm,
    winter storm, etc.) for a US state, county, or geographic area. The tool
    returns a FlatGeobuf of alert polygons + properties (severity, headline,
    event, onset, ends, description) suitable for overlay on the map and for
    feeding into the Hazard Event Pipeline as agency-tier (FR-HEP-2 Tier 1)
    forcing evidence.

    Do NOT use this for: historical alerts (use ``fetch_storm_events_db`` for
    NOAA Storm Events DB lookups instead — NWS active-alerts is current-only,
    typically 0-7 days); reverse-geocoding a single point to a forecast zone
    (different NWS endpoint); rainfall return periods or river forecasts
    (different NWS surfaces — use ``lookup_precip_return_period`` etc.);
    international weather alerts (NWS is US-only).

    Params:
        area: One of three forms —
            * 2-letter US state/territory code: "FL", "TX", "PR", ...
              (also accepts marine zones like "GM" for Gulf of Mexico);
            * 5-digit county FIPS code as a string: "12071" (Lee County FL);
            * bbox tuple ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326
              — converted to a point center ``(lat, lon)`` and sent as
              ``?point=`` (NWS does not accept bbox; the point lookup returns
              all alerts whose forecast zones contain that point).
        event_types: Optional list of NWS event-type strings to filter to
            (e.g. ``["Hurricane Warning", "Flood Warning"]``). When provided,
            NWS supports a repeated ``&event=`` query param per type. When
            None / empty, returns ALL active alerts for the area.
        status: NWS alert status. Default ``"actual"`` (real alerts; never
            test/exercise/draft). Accepted: actual, exercise, system, test, draft.
        message_type: NWS alert message_type. Default ``"alert"`` (the
            originating message). Accepted: alert, update, cancel.

    Returns:
        A ``LayerURI`` pointing at a FlatGeobuf in the cache bucket:
        ``gs://grace-2-hazard-prod-cache/cache/dynamic-1h/nws_event/<key>.fgb``
        containing alert polygons + properties.
        ``layer_type="vector"``, ``role="context"``, ``units=None``.

    Cache: ``dynamic-1h`` (FR-DC-2 active-state). Two identical calls inside
    the same hour-bucket reuse the cached FlatGeobuf; a one-hour boundary
    crossing forces a refresh.

    Cache key: SHA-256 of ``(area_canonicalized, event_types_sorted, status,
    message_type, "dynamic-1h" vintage)``. ``area_canonicalized`` is one of
    ``{kind="state", value=...}``, ``{kind="fips", value=...}``, or
    ``{kind="point", lat=round(lat,4), lon=round(lon,4)}``.

    External-API resilience (NFR-R-1): NWS rate-limits unauthenticated
    requests and REQUIRES a descriptive User-Agent header (returns 403
    otherwise — see ``_USER_AGENT``). On network failure / non-2xx /
    malformed JSON the tool raises ``NWSUpstreamError(retryable=True)``
    so the agent's FR-AS-11 surface can decide.

    Source-tier: FR-HEP-2 Tier 1 (federal agency authoritative source).
    Claims derived from this tool should be marked
    ``source_authority_tier=1`` in any ``ClaimSet`` aggregation.
    """
    # Validate status / message_type early — the kickoff says these have
    # fixed enums on the NWS side. Bad values are caller error, not retryable.
    if status not in _VALID_STATUSES:
        raise NWSInputError(
            f"status={status!r} not in {sorted(_VALID_STATUSES)}"
        )
    if message_type not in _VALID_MESSAGE_TYPES:
        raise NWSInputError(
            f"message_type={message_type!r} not in {sorted(_VALID_MESSAGE_TYPES)}"
        )

    canon_area = _canonicalize_area(area)

    # Sort event_types for cache-key stability (per audit.md "event_types sorted").
    # None and [] are equivalent — both mean "no filter".
    sorted_event_types: list[str] | None = None
    if event_types:
        if not all(isinstance(e, str) for e in event_types):
            raise NWSInputError(
                f"event_types must be list[str]; got {event_types!r}"
            )
        sorted_event_types = sorted({e.strip() for e in event_types if e.strip()})
        if not sorted_event_types:
            sorted_event_types = None

    params: dict[str, Any] = {
        "area": canon_area,
        "event_types": sorted_event_types,
        "status": status,
        "message_type": message_type,
    }

    result = read_through(
        metadata=_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_nws_event_bytes(
            canon_area, sorted_event_types, status, message_type,
        ),
    )
    assert result.uri is not None, (
        "fetch_nws_event is cacheable; uri must be set by read_through"
    )

    # LayerURI display name reflects the area kind for diagnostics.
    if canon_area["kind"] == "state":
        area_label = f"State {canon_area['value']}"
        layer_id = f"nws-state-{canon_area['value']}"
    elif canon_area["kind"] == "fips":
        area_label = f"FIPS {canon_area['value']}"
        layer_id = f"nws-fips-{canon_area['value']}"
    else:  # point
        area_label = (
            f"Point ({canon_area['lat']:.4f}, {canon_area['lon']:.4f})"
        )
        layer_id = (
            f"nws-point-{canon_area['lat']:.4f}-{canon_area['lon']:.4f}"
        )

    return LayerURI(
        layer_id=layer_id,
        name=f"NWS Active Alerts — {area_label}",
        layer_type="vector",
        uri=result.uri,
        style_preset="nws_alerts",  # placeholder; NWS-specific QML preset is a follow-up
        role="context",
        units=None,
    )
