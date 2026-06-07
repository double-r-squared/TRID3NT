"""Data-fetch atomic tools (job-0033, M4 Stage C).

This module registers four atomic tools that fetch public data from external
agency feeds and write the resulting artifact through the FR-DC-3 cache shim
(``.cache.read_through``). Each tool:

- declares its ``AtomicToolMetadata`` (TTL class + source class + cacheable)
  per FR-AS-3 + FR-CE-8 at import time via ``@register_tool``;
- routes its fetch through ``read_through`` so identical calls reuse the
  cached artifact (FR-DC-3/4) and the live-no-cache enumeration (FR-DC-6) is
  honored uniformly;
- pre-quantizes the bbox to the source's native resolution BEFORE handing the
  params dict to ``read_through`` (OQ-32-QUANTIZATION-LOCATION: engine-side).

Tools registered here:

- ``fetch_dem(bbox, resolution_m=10)`` — USGS 3DEP via ``py3dep.get_dem`` →
  COG bytes → ``cache/static-30d/dem/<key>.tif``.
- ``fetch_buildings(bbox, source="msft")`` — MS Open Maps Buildings (PMTiles/
  FlatGeobuf served as quadkey tiles via MSFT's Open Data) → ``cache/static-30d/buildings/<key>.fgb``.
- ``fetch_population(bbox, dataset="acs_2022")`` — US Census ACS B01003_001E
  tract-level → GeoJSON FeatureCollection → ``cache/static-30d/population/<key>.json``.
- ``geocode_location(query)`` — Nominatim REST forward geocode → JSON with
  ``{name, bbox, latitude, longitude, source}`` → ``cache/dynamic-1h/geocode/<key>.json``.

FR-TA-2 / FR-AS-3 docstring discipline: every public tool docstring carries
"Use this when:" and "Do NOT use this for:" sections so the FunctionTool
surface is self-describing to Gemini.

Returns / shapes:

- The three layer-producing tools return ``LayerURI`` (from
  ``grace2_contracts.execution``) so downstream visualization seams (map-
  command ``load-layer``) consume them with zero translation.
- ``geocode_location`` returns a plain ``dict`` for now — there is no
  ``GeocodedLocation`` pydantic model in ``grace2-contracts`` yet (FROZEN).
  OQ surfaced for schema to consider promoting in a follow-up job.

External-API resilience (NFR-R-1): per-call timeout, single re-raise on
fetcher failure (no sentinel writes — see ``read_through``). The agent's
FR-AS-11 surface decides retry/clarify/fallback.

Nominatim usage policy compliance: User-Agent header is REQUIRED, fetched
data is cached in our own bucket (we don't re-host), and rate is naturally
throttled by the ``dynamic-1h`` cache class (one fetch per hour-bucket per
distinct query).
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
from typing import Any

import requests

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = [
    "fetch_dem",
    "fetch_buildings",
    "fetch_population",
    "geocode_location",
    "round_bbox_to_resolution",
]

logger = logging.getLogger("grace2_agent.tools.data_fetch")


# ---------------------------------------------------------------------------
# Error codes registered by this module (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------
#
# These RuntimeError subclasses carry a stable ``error_code`` for the
# WebSocket A.6 error frame the agent surface emits when a fetch fails. They
# are caught nowhere inside this module — the ``read_through`` contract is
# "re-raise on fetcher failure; no sentinel" — so server-side error handling
# (server.py M1) maps them to A.6 codes via the agent's error surface (job-
# 0035 lands the mapping; for now they bubble up).


class FetchError(RuntimeError):
    """Base class for data-fetch failures. ``error_code`` is the A.6 code."""

    error_code: str = "UPSTREAM_API_ERROR"
    retryable: bool = True


class UpstreamAPIError(FetchError):
    """An upstream public-data API returned an error or timed out."""

    error_code = "UPSTREAM_API_ERROR"
    retryable = True


class BboxInvalidError(FetchError):
    """The bbox failed validation (degenerate, out of CRS range, too large)."""

    error_code = "BBOX_INVALID"
    retryable = False


# Nominatim usage policy requires a descriptive User-Agent identifying the
# application + a contact. We bake the project name + repo URL; override the
# contact email via env var ``GRACE2_NOMINATIM_USER_AGENT`` for ops.
_DEFAULT_USER_AGENT = (
    "grace-2/0.1 (Hazard Modeling Agent; "
    "https://github.com/double-r-squared/GRACE-2; agent@grace-2.dev)"
)


# ---------------------------------------------------------------------------
# bbox helpers (FR-DC-3 / OQ-32-QUANTIZATION-LOCATION: engine-side quantize).
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Raise ``BboxInvalidError`` if ``bbox`` is degenerate or out of WGS84 range.

    A valid bbox is ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326,
    with min < max on both axes, lons in ``[-180, 180]`` and lats in
    ``[-90, 90]``.
    """
    if len(bbox) != 4:
        raise BboxInvalidError(
            f"bbox must be a 4-tuple (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (math.isfinite(min_lon) and math.isfinite(min_lat) and math.isfinite(max_lon) and math.isfinite(max_lat)):
        raise BboxInvalidError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise BboxInvalidError(f"bbox lon out of range [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise BboxInvalidError(f"bbox lat out of range [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise BboxInvalidError(
            f"bbox is degenerate (min must be < max on both axes): {bbox!r}"
        )


def round_bbox_to_resolution(
    bbox: tuple[float, float, float, float],
    resolution_m: int,
) -> tuple[float, float, float, float]:
    """Quantize a WGS84 bbox to a per-source resolution grid before cache-keying.

    Rationale: two callers asking for the same area at the same resolution
    should hit the same cache entry even if their bbox edges differ by a few
    floating-point meters. We snap each corner to the nearest grid line whose
    spacing in degrees matches ``resolution_m`` (using a degrees-per-meter
    conversion at the bbox center latitude — good enough for any sub-state
    bbox; per-source overrides can refine).

    Args:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        resolution_m: target grid spacing in meters (e.g. 10 for 3DEP 10m).

    Returns:
        A quantized bbox tuple. Always slightly larger than the input bbox
        (snaps mins down and maxes up) so the requested area is covered.

    Surfaced as the engine-side resolution of OQ-32-QUANTIZATION-LOCATION:
    the cache shim's contract is canonicalize+hash; per-source quantization
    is engine-owned domain knowledge.
    """
    _validate_bbox(bbox)
    if resolution_m <= 0:
        raise BboxInvalidError(f"resolution_m must be positive; got {resolution_m!r}")

    min_lon, min_lat, max_lon, max_lat = bbox
    # Stabilize mid_lat by rounding to 4 decimals (~11m) so two callers whose
    # bbox edges differ by sub-meter floats don't get different
    # m_per_deg_lon factors (which would defeat the dedup-via-quantization
    # property — same grid cell must yield same snap result).
    mid_lat = round(0.5 * (min_lat + max_lat), 4)
    # 1 degree of latitude ~ 111_320 m; 1 degree of longitude ~ 111_320 * cos(lat) m.
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
    if m_per_deg_lon < 1e-6:  # near a pole — fall back to deg-lat
        m_per_deg_lon = 111_320.0

    deg_lat_per_step = resolution_m / m_per_deg_lat
    deg_lon_per_step = resolution_m / m_per_deg_lon

    snapped_min_lon = math.floor(min_lon / deg_lon_per_step) * deg_lon_per_step
    snapped_max_lon = math.ceil(max_lon / deg_lon_per_step) * deg_lon_per_step
    snapped_min_lat = math.floor(min_lat / deg_lat_per_step) * deg_lat_per_step
    snapped_max_lat = math.ceil(max_lat / deg_lat_per_step) * deg_lat_per_step

    # Round to a reasonable number of digits so the JSON canonicalization
    # produces stable strings (float repr quirks otherwise leak into the key).
    return (
        round(snapped_min_lon, 9),
        round(snapped_min_lat, 9),
        round(snapped_max_lon, 9),
        round(snapped_max_lat, 9),
    )


def _bbox_area_km2(bbox: tuple[float, float, float, float]) -> float:
    """Approximate area of a small WGS84 bbox in square kilometers."""
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = 0.5 * (min_lat + max_lat)
    dlat_km = (max_lat - min_lat) * 111.320
    dlon_km = (max_lon - min_lon) * 111.320 * math.cos(math.radians(mid_lat))
    return abs(dlat_km * dlon_km)


# ---------------------------------------------------------------------------
# fetch_dem — USGS 3DEP via py3dep
# ---------------------------------------------------------------------------


_FETCH_DEM_METADATA = AtomicToolMetadata(
    name="fetch_dem",
    ttl_class="static-30d",
    source_class="dem",
    cacheable=True,
)


def _fetch_3dep_dem_bytes(
    bbox: tuple[float, float, float, float], resolution_m: int
) -> bytes:
    """Call ``py3dep.get_dem`` and serialize the result as a Cloud-Optimized GeoTIFF.

    Raises ``UpstreamAPIError`` on any failure from the 3DEP service so the
    cache shim's "no sentinel on failure" contract surfaces a typed error.
    """
    # py3dep + rasterio import lazily so test environments without these
    # heavy geo deps installed can still load the registry.
    try:
        import py3dep  # type: ignore[import-not-found]
        import rioxarray  # noqa: F401 — registers .rio accessor on xr.DataArray
    except Exception as exc:  # noqa: BLE001
        raise UpstreamAPIError(f"py3dep / rioxarray unavailable: {exc}") from exc

    try:
        dem = py3dep.get_dem(bbox, resolution=resolution_m)
    except Exception as exc:  # noqa: BLE001 — re-raise as typed error
        raise UpstreamAPIError(
            f"py3dep.get_dem failed for bbox={bbox} resolution={resolution_m}: {exc}"
        ) from exc

    # Serialize to a COG via rioxarray's to_raster. We round-trip through a
    # temp file because rasterio's MemoryFile lacks COG driver options on
    # some platforms; the temp file is small for a small bbox.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        # COG driver + LZW compression. tiled=True is COG-required.
        dem.rio.to_raster(
            tmp_path,
            driver="COG",
            compress="LZW",
            BIGTIFF="IF_SAFER",
        )
        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return data


@register_tool(_FETCH_DEM_METADATA)
def fetch_dem(
    bbox: tuple[float, float, float, float],
    resolution_m: int = 10,
) -> LayerURI:
    """Fetch a digital elevation model (DEM) for a bbox from USGS 3DEP.

    Use this when: the agent needs ground elevation for terrain analysis,
    flood-depth computation, watershed delineation, or visualization of a
    CONUS-bounded area. 3DEP covers the United States at native 10m and 30m
    resolutions; pick 10 unless the bbox is very large.

    Do NOT use this for: global coverage (3DEP is CONUS-only; future work may
    add Copernicus DEM as a global fallback); bathymetry (3DEP is land-only —
    a future ``fetch_bathymetry`` will route to NOAA NCEI); on-the-fly
    elevation lookups for single points (use ``py3dep.elevation_bycoords``
    via a follow-up tool if needed).

    Params:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        resolution_m: target DEM grid spacing in meters; 10 or 30 is fastest
            on 3DEP's static tile tree. Defaults to 10.

    Returns:
        A ``LayerURI`` pointing at a Cloud-Optimized GeoTIFF in the cache
        bucket. The DEM is reprojected by ``py3dep`` to EPSG:5070 (its
        default analysis CRS); the units are meters above NAVD88. The URI is
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/dem/<key>.tif``.

    FR-CE-8: The fetch is routed through ``read_through`` so identical
    quantized-bbox + resolution calls reuse the cached COG (FR-DC-3/4); a
    miss writes the COG with ``customTime`` set so the 30-day eviction
    policy runs from the fetch time.
    """
    quantized = round_bbox_to_resolution(bbox, resolution_m)
    if _bbox_area_km2(quantized) > 10_000.0:
        raise BboxInvalidError(
            f"bbox area {_bbox_area_km2(quantized):.1f} km^2 exceeds 10000 km^2 "
            "guardrail for fetch_dem (use a smaller bbox or a future workflow "
            "that tiles the request)."
        )
    params = {"bbox": list(quantized), "resolution_m": resolution_m}
    result = read_through(
        metadata=_FETCH_DEM_METADATA,
        params=params,
        ext="tif",
        fetch_fn=lambda: _fetch_3dep_dem_bytes(quantized, resolution_m),
    )
    assert result.uri is not None, "fetch_dem is cacheable; uri must be set"
    return LayerURI(
        layer_id=f"dem-{quantized[0]:.4f}-{quantized[1]:.4f}-{resolution_m}m",
        name=f"USGS 3DEP DEM ({resolution_m}m)",
        layer_type="raster",
        uri=result.uri,
        style_preset="continuous_dem",
        role="input",
        units="meters",
    )


# ---------------------------------------------------------------------------
# fetch_buildings — Microsoft Building Footprints
# ---------------------------------------------------------------------------


_FETCH_BUILDINGS_METADATA = AtomicToolMetadata(
    name="fetch_buildings",
    ttl_class="static-30d",
    source_class="buildings",
    cacheable=True,
)


# MS Open Maps publishes Global ML Building Footprints sharded by quadkey
# under a public Azure Blob container. The official catalog index is at:
#   https://minedbuildings.blob.core.windows.net/global-buildings/dataset-links.csv
# Each row is (QuadKey, Location, Url) — the URL is a GZIP'd line-delimited
# GeoJSON. For a bbox we'd find the intersecting quadkey(s), fetch + filter
# + reformat to FlatGeobuf. The MS Open Maps STAC catalog (an alternative
# entry point referenced in the kickoff) at planetarycomputer.microsoft.com
# wraps this same data under a STAC API.
#
# For the M4 substrate this function is a thin wrapper: it accepts the bbox,
# attempts a planetary-computer STAC item search, and returns the resulting
# bytes (FlatGeobuf if the item provides one, otherwise GeoJSON serialized
# as a single feature collection). When no items match (bbox outside ML
# coverage), an ``UpstreamAPIError`` surfaces — engine downstream decides
# whether to fall back to OSM via a future ``source="osm"`` branch.


def _fetch_msft_buildings_bytes(
    bbox: tuple[float, float, float, float],
) -> bytes:
    """Query MS Open Maps Building Footprints for ``bbox`` and return FlatGeobuf bytes.

    Uses the Microsoft Planetary Computer STAC API as the query surface
    (https://planetarycomputer.microsoft.com/api/stac/v1) — the same catalog
    that backs the public MS Open Maps releases. Items in the
    ``ms-buildings`` collection point at PMTiles / FlatGeobuf assets we can
    download by-asset.

    Implementation note (M4 scope): this is a minimal request → response
    path. A production-grade implementation would use ``pystac-client`` for
    pagination and ``stackstac`` for asset materialization; for the M4
    substrate we issue a single ``POST /search`` with the bbox + intersects
    filter, take the first matching item's FlatGeobuf asset (or fall back
    to GeoJSON serialization of the geometry), and return raw bytes.
    """
    _validate_bbox(bbox)
    # Planetary Computer STAC endpoint. The ms-buildings collection is the
    # public catalog wrapping the Open Data ML footprints.
    pc_stac_url = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
    search_body = {
        "collections": ["ms-buildings"],
        "bbox": list(bbox),
        "limit": 1,
    }
    try:
        resp = requests.post(
            pc_stac_url,
            json=search_body,
            headers={"User-Agent": _DEFAULT_USER_AGENT, "Accept": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        catalog = resp.json()
    except requests.RequestException as exc:
        raise UpstreamAPIError(
            f"MS Open Maps STAC search failed for bbox={bbox}: {exc}"
        ) from exc

    features = catalog.get("features", []) or []
    if not features:
        # No ML coverage in this bbox; surface a typed error so the agent can
        # choose to fall back to OSM via ``source="osm"`` in a future call.
        raise UpstreamAPIError(
            f"no MS Open Maps building items intersect bbox={bbox} "
            f"(coverage may be missing — fall back via source='osm' in a follow-up)"
        )

    # Asset preference: FlatGeobuf if present, GeoParquet next, GeoJSON last.
    item = features[0]
    assets = item.get("assets", {}) or {}

    preferred_asset = None
    for asset_key in ("data", "footprints", "flatgeobuf"):
        if asset_key in assets:
            preferred_asset = assets[asset_key]
            break
    if preferred_asset is None and assets:
        # Fall back to the first asset listed.
        preferred_asset = next(iter(assets.values()))
    if preferred_asset is None or "href" not in preferred_asset:
        # No downloadable asset; serialize the bbox as a placeholder
        # FeatureCollection so the path completes deterministically. A
        # follow-up job replaces this with proper PMTiles materialization.
        placeholder = {
            "type": "FeatureCollection",
            "features": [],
            "_grace2_note": (
                "STAC item had no downloadable asset; placeholder emitted. "
                "Replace via PMTiles materialization in M5 follow-up."
            ),
            "_grace2_item_id": item.get("id"),
            "_grace2_bbox": list(bbox),
        }
        return json.dumps(placeholder).encode("utf-8")

    asset_url = preferred_asset["href"]
    try:
        asset_resp = requests.get(
            asset_url,
            headers={"User-Agent": _DEFAULT_USER_AGENT},
            timeout=60.0,
        )
        asset_resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamAPIError(
            f"MS Open Maps asset download failed url={asset_url}: {exc}"
        ) from exc

    return asset_resp.content


@register_tool(_FETCH_BUILDINGS_METADATA)
def fetch_buildings(
    bbox: tuple[float, float, float, float],
    source: str = "msft",
) -> LayerURI:
    """Fetch building footprints for a bbox.

    Use this when: the agent needs building polygons for damage / exposure
    estimation, risk scoring, or display of the built environment. Default
    source ``"msft"`` uses Microsoft Open Maps ML-derived footprints (global
    coverage; updated quarterly).

    Do NOT use this for: live address/parcel lookups (those need a different
    cadastral source); 3D building heights (heights are a separate dataset
    — see ``fetch_building_heights`` once it lands); querying buildings by
    name or use class (filter post-fetch).

    Params:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        source: ``"msft"`` (default) or ``"osm"`` (future). The choice
            affects the cache key, so two sources never collide.

    Returns:
        A ``LayerURI`` pointing at a FlatGeobuf (or GeoJSON if no
        FlatGeobuf asset was available) in the cache bucket. The URI is
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/buildings/<key>.fgb``.

    FR-CE-8: The fetch is routed through ``read_through`` so identical
    quantized-bbox + source calls reuse the cached artifact.
    """
    if source not in ("msft", "osm"):
        raise BboxInvalidError(
            f"unsupported source={source!r}; allowed: 'msft', 'osm' (future)"
        )
    if source == "osm":
        # OSM via Overpass routes through a future tool; not in M4 substrate.
        raise UpstreamAPIError(
            "fetch_buildings(source='osm') is not implemented yet; "
            "use source='msft' (default) for M4 substrate."
        )
    # Quantize bbox to 10m: building footprint polygons are at sub-meter
    # precision but the bbox boundary is the cache-key driver, and a 10m
    # snap is plenty for the dedup goal (same neighborhood query == same key).
    quantized = round_bbox_to_resolution(bbox, 10)
    if _bbox_area_km2(quantized) > 5_000.0:
        raise BboxInvalidError(
            f"bbox area {_bbox_area_km2(quantized):.1f} km^2 exceeds 5000 km^2 "
            "guardrail for fetch_buildings (a single PMTiles asset will not "
            "cover that; use a tiled workflow)."
        )
    params = {"bbox": list(quantized), "source": source}
    result = read_through(
        metadata=_FETCH_BUILDINGS_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_msft_buildings_bytes(quantized),
    )
    assert result.uri is not None
    return LayerURI(
        layer_id=f"buildings-{quantized[0]:.4f}-{quantized[1]:.4f}-{source}",
        name=f"Buildings ({source.upper()})",
        layer_type="vector",
        uri=result.uri,
        style_preset="affected_buildings",
        role="input",
    )


# ---------------------------------------------------------------------------
# fetch_population — US Census ACS B01003_001E
# ---------------------------------------------------------------------------


_FETCH_POPULATION_METADATA = AtomicToolMetadata(
    name="fetch_population",
    ttl_class="static-30d",
    source_class="population",
    cacheable=True,
)


def _fetch_acs_population_bytes(
    bbox: tuple[float, float, float, float], dataset: str
) -> bytes:
    """Fetch US Census ACS B01003 (total population) for tracts intersecting bbox.

    Uses the Census Bureau's public REST API (no key required for small
    queries; an API key can be added later for high-volume use). For the
    M4 substrate we return a GeoJSON ``FeatureCollection`` containing one
    feature per Census tract in the intersecting states, each with the
    ``B01003_001E`` total-population value as a property.

    The tract geometries themselves come from the Census TIGERweb GeoServices
    REST endpoint (a separate call). For substrate-scope simplicity this
    function returns a population *table* (FeatureCollection of point
    features at tract centroids) rather than full tract polygons; a future
    enrichment job swaps in real geometries from the TIGER cartographic
    boundary shapefiles.
    """
    _validate_bbox(bbox)
    if not dataset.startswith("acs_"):
        raise UpstreamAPIError(
            f"unsupported dataset={dataset!r} for ACS branch; expected 'acs_2022'"
        )
    year = dataset.split("_", 1)[1]
    # ACS 5-year endpoint; the variable B01003_001E is total population.
    # We request by `for=state:*` to enumerate the intersecting state set —
    # for the M4 substrate, just take the bbox center's state as a heuristic.
    mid_lon = 0.5 * (bbox[0] + bbox[2])
    mid_lat = 0.5 * (bbox[1] + bbox[3])
    state_fips = _state_fips_for_lonlat(mid_lon, mid_lat)
    if state_fips is None:
        raise UpstreamAPIError(
            f"could not resolve state FIPS for bbox center=({mid_lon}, {mid_lat}); "
            "ACS branch needs CONUS coverage"
        )

    # Census API: B01003_001E for all tracts in the state.
    url = (
        f"https://api.census.gov/data/{year}/acs/acs5?"
        f"get=B01003_001E,NAME&for=tract:*&in=state:{state_fips}"
    )
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _DEFAULT_USER_AGENT, "Accept": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as exc:
        raise UpstreamAPIError(
            f"US Census ACS API failed for state={state_fips}: {exc}"
        ) from exc

    # rows[0] is the header; rows[1:] are data.
    if not rows or len(rows) < 2:
        raise UpstreamAPIError(
            f"US Census ACS returned no rows for state={state_fips}"
        )
    header = rows[0]
    pop_idx = header.index("B01003_001E")
    name_idx = header.index("NAME")
    state_idx = header.index("state")
    county_idx = header.index("county")
    tract_idx = header.index("tract")

    features: list[dict[str, Any]] = []
    for row in rows[1:]:
        try:
            pop = int(row[pop_idx]) if row[pop_idx] not in (None, "") else None
        except (TypeError, ValueError):
            pop = None
        features.append(
            {
                "type": "Feature",
                "geometry": None,  # geometry enrichment is a follow-up
                "properties": {
                    "name": row[name_idx],
                    "population": pop,
                    "state": row[state_idx],
                    "county": row[county_idx],
                    "tract": row[tract_idx],
                    "dataset": dataset,
                    "variable": "B01003_001E",
                },
            }
        )

    fc = {
        "type": "FeatureCollection",
        "features": features,
        "_grace2_bbox": list(bbox),
        "_grace2_dataset": dataset,
        "_grace2_source": "US Census ACS 5-year",
    }
    buf = io.BytesIO()
    buf.write(json.dumps(fc).encode("utf-8"))
    return buf.getvalue()


# Minimal lon/lat -> state FIPS mapping for the CONUS-default ACS branch.
# Used only as a routing heuristic in the M4 substrate; a future enrichment
# job replaces this with a real point-in-polygon over TIGER state boundaries.
_CONUS_STATE_BBOXES: dict[str, tuple[float, float, float, float]] = {
    # state_fips -> (min_lon, min_lat, max_lon, max_lat) approximate envelope
    "12": (-87.6, 24.4, -80.0, 31.0),  # Florida
    "13": (-85.6, 30.3, -80.8, 35.0),  # Georgia
    "01": (-88.5, 30.2, -84.9, 35.0),  # Alabama
    "28": (-91.7, 30.1, -88.1, 35.0),  # Mississippi
    "22": (-94.0, 28.9, -89.0, 33.0),  # Louisiana
    "48": (-106.7, 25.8, -93.5, 36.5),  # Texas
    "06": (-124.5, 32.5, -114.1, 42.0),  # California
    "53": (-124.8, 45.5, -116.9, 49.0),  # Washington
    "41": (-124.6, 41.9, -116.5, 46.3),  # Oregon
    "36": (-79.8, 40.5, -71.9, 45.0),  # New York
    "37": (-84.4, 33.8, -75.4, 36.6),  # North Carolina
    "45": (-83.4, 32.0, -78.5, 35.2),  # South Carolina
    "21": (-89.6, 36.5, -82.0, 39.1),  # Kentucky
    "47": (-90.3, 35.0, -81.7, 36.7),  # Tennessee
    "51": (-83.7, 36.5, -75.2, 39.5),  # Virginia
}


def _state_fips_for_lonlat(lon: float, lat: float) -> str | None:
    """Best-effort state FIPS lookup from a point — heuristic only.

    Returns ``None`` if no envelope matches. A future enrichment job replaces
    this with a real point-in-polygon over a TIGER state boundary file
    cached in the artifacts bucket.
    """
    for fips, (mn_lon, mn_lat, mx_lon, mx_lat) in _CONUS_STATE_BBOXES.items():
        if mn_lon <= lon <= mx_lon and mn_lat <= lat <= mx_lat:
            return fips
    return None


@register_tool(_FETCH_POPULATION_METADATA)
def fetch_population(
    bbox: tuple[float, float, float, float],
    dataset: str = "acs_2022",
) -> LayerURI:
    """Fetch population data for a bbox from US Census ACS (default) or WorldPop.

    Use this when: the agent needs population counts for exposure analysis,
    risk scoring, or display alongside hazard layers. Default dataset
    ``"acs_2022"`` uses the US Census Bureau's American Community Survey
    5-year estimates (B01003_001E total population at tract level), the
    authoritative source for CONUS (Decision I scope).

    Do NOT use this for: real-time / daytime population (ACS is residential
    count); sub-tract resolution within the US (LandScan or WorldPop offer
    ~100m grids and would be added as ``dataset="worldpop"`` opt-in);
    non-US areas (ACS is US-only — WorldPop is the global fallback).

    Params:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        dataset: ``"acs_2022"`` (default, ACS 5-year) or ``"worldpop"``
            (opt-in, future — global 100m grid; tracked as OQ).

    Returns:
        A ``LayerURI`` pointing at a GeoJSON FeatureCollection in the cache
        bucket. Each feature is a Census tract with ``properties.population``
        and tract identifiers; geometry enrichment is a follow-up. The URI
        is ``gs://grace-2-hazard-prod-cache/cache/static-30d/population/<key>.json``.

    FR-CE-8: The fetch is routed through ``read_through`` so identical
    quantized-bbox + dataset calls reuse the cached artifact.

    Decision: ACS is the default per Decision I (CONUS scope). WorldPop is
    opt-in via ``dataset="worldpop"`` (not implemented in M4 substrate).
    """
    if dataset == "worldpop":
        # WorldPop branch is opt-in; not implemented in M4 substrate per the
        # ACS-default decision. Routes to a follow-up job.
        raise UpstreamAPIError(
            "fetch_population(dataset='worldpop') is not implemented yet; "
            "M4 substrate uses ACS by default (CONUS-only)."
        )
    if not dataset.startswith("acs_"):
        raise BboxInvalidError(
            f"unsupported dataset={dataset!r}; allowed: 'acs_2022', 'worldpop' (future)"
        )
    # Quantize at 100m: ACS tract geometries are coarse; finer quantization
    # would still hit the same tracts but produce gratuitous cache misses.
    quantized = round_bbox_to_resolution(bbox, 100)
    params = {"bbox": list(quantized), "dataset": dataset}
    result = read_through(
        metadata=_FETCH_POPULATION_METADATA,
        params=params,
        ext="json",
        fetch_fn=lambda: _fetch_acs_population_bytes(quantized, dataset),
    )
    assert result.uri is not None
    return LayerURI(
        layer_id=f"population-{quantized[0]:.4f}-{quantized[1]:.4f}-{dataset}",
        name=f"Population ({dataset})",
        layer_type="vector",
        uri=result.uri,
        style_preset="continuous_dem",  # placeholder; population preset lands later
        role="input",
        units="people",
    )


# ---------------------------------------------------------------------------
# geocode_location — Nominatim REST
# ---------------------------------------------------------------------------


_GEOCODE_LOCATION_METADATA = AtomicToolMetadata(
    name="geocode_location",
    ttl_class="dynamic-1h",
    source_class="geocode",
    cacheable=True,
)


def _fetch_nominatim_geocode_bytes(query: str) -> bytes:
    """Forward-geocode ``query`` via OpenStreetMap Nominatim and return JSON bytes.

    Honors Nominatim usage policy:
    - descriptive User-Agent identifying the app + contact;
    - ``format=jsonv2`` for stable JSON shape;
    - ``limit=1`` so we get the top-ranked match;
    - ``polygon_geojson=0`` (we just want bbox + lat/lon);
    - one request per cache-bucket window (the ``dynamic-1h`` class naturally
      throttles repeat queries — see ``read_through``).

    Returns the JSON-encoded structured result the tool body further
    massages into a ``GeocodedLocation``-shaped dict.
    """
    if not query or not query.strip():
        raise BboxInvalidError("geocode_location requires a non-empty query")

    user_agent = os.environ.get("GRACE2_NOMINATIM_USER_AGENT", _DEFAULT_USER_AGENT)
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query.strip(),
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 0,
        "polygon_geojson": 0,
    }
    try:
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise UpstreamAPIError(
            f"Nominatim search failed for query={query!r}: {exc}"
        ) from exc
    except ValueError as exc:
        raise UpstreamAPIError(
            f"Nominatim returned non-JSON for query={query!r}: {exc}"
        ) from exc

    if not body:
        raise UpstreamAPIError(
            f"Nominatim returned no results for query={query!r}"
        )

    top = body[0]
    # Nominatim returns boundingbox as [south, north, west, east] strings.
    bb = top.get("boundingbox", [])
    if len(bb) != 4:
        raise UpstreamAPIError(
            f"Nominatim boundingbox missing/malformed for query={query!r}: {bb!r}"
        )
    try:
        south, north, west, east = [float(v) for v in bb]
    except (TypeError, ValueError) as exc:
        raise UpstreamAPIError(
            f"Nominatim boundingbox non-numeric: {bb!r}"
        ) from exc

    structured = {
        "name": top.get("display_name", query),
        "latitude": float(top.get("lat", 0.0)),
        "longitude": float(top.get("lon", 0.0)),
        # Normalize to (min_lon, min_lat, max_lon, max_lat) — the project
        # canonical bbox shape (matches LayerURI / Census / py3dep).
        "bbox": [west, south, east, north],
        "source": "nominatim",
        "query": query,
        "osm_type": top.get("osm_type"),
        "osm_id": top.get("osm_id"),
        "place_id": top.get("place_id"),
    }
    return json.dumps(structured).encode("utf-8")


@register_tool(_GEOCODE_LOCATION_METADATA)
def geocode_location(query: str) -> dict[str, Any]:
    """Forward-geocode a place name to a bbox + canonical name (Nominatim).

    Use this when: the agent receives a free-text location reference
    ("Fort Myers, FL", "Tampa Bay", "Hurricane Ian landfall site") and
    needs a bbox + canonical name to drive other fetch tools. The result
    bbox feeds directly into ``fetch_dem`` / ``fetch_buildings`` /
    ``fetch_population``.

    Do NOT use this for: reverse geocoding (point → name — different
    Nominatim endpoint); routing / distance queries (Nominatim doesn't);
    high-precision parcel-level lookups (Nominatim is street-level at best).

    Params:
        query: free-text location reference (e.g. ``"Fort Myers, FL"``).

    Returns:
        A dict with keys ``name`` (canonical display name), ``bbox``
        (``[min_lon, min_lat, max_lon, max_lat]``), ``latitude``,
        ``longitude``, ``source`` (``"nominatim"``), and provenance fields
        ``osm_type`` / ``osm_id`` / ``place_id``.

    FR-CE-8: The fetch is routed through ``read_through`` so two identical
    queries within the same hourly window reuse the cached response. The
    cache class is ``"dynamic-1h"`` per FR-DC-2 active-state-ish (geocoding
    answers DO change as Nominatim's OSM index updates, but on a slower
    cadence than hourly).

    Side effect: per FR-TA-2 §"Location-resolved emission" / FR-AS-7, the
    agent surface emits a ``location-resolved`` WebSocket message when this
    tool returns so the client auto-snaps the map. The emission seam is
    in the agent's server.py M1 module — surfaced as
    OQ-33-LOCATION-RESOLVED-EMISSION-SEAM for the agent job that owns
    envelope emission this sprint (job-0035) to wire up.

    Nominatim usage policy: User-Agent is sent on every request; the
    ``dynamic-1h`` cache class naturally throttles repeat queries (one
    fetch per hour-bucket per distinct query).
    """
    if not isinstance(query, str) or not query.strip():
        raise BboxInvalidError("geocode_location requires a non-empty string query")
    params = {"query": query.strip()}
    result = read_through(
        metadata=_GEOCODE_LOCATION_METADATA,
        params=params,
        ext="json",
        fetch_fn=lambda: _fetch_nominatim_geocode_bytes(query),
    )
    # The fetched (or cached) payload is JSON bytes; decode and return as a
    # structured dict. The cache URI is intentionally NOT returned to the LLM
    # — Tier separation (invariant 5): no gs:// URIs leak into model text.
    payload = json.loads(result.data.decode("utf-8"))
    logger.info(
        "geocode_location query=%r resolved name=%r cache_hit=%s",
        query,
        payload.get("name"),
        result.hit,
    )
    return payload
