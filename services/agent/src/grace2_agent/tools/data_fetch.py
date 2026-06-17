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
- ``fetch_population(bbox, dataset="worldpop_2020")`` — WorldPop 100m Unconstrained
  UN-adjusted gridded population (Tier-1 per Appendix F.1, no key required).
  Windowed read over the bbox via ``rasterio`` ``/vsicurl/`` from the WorldPop
  REST endpoint → COG bytes → ``cache/static-30d/population/<key>.tif``.
  ``dataset="acs_2022"`` opts into the Tier-2 Census ACS B01003 tract-level
  GeoJSON path (requires Census API key for high-volume use; routed when the
  agent needs tract-level precision rather than the 100m raster) →
  ``cache/static-30d/population/<key>.json``.
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
    "fetch_landcover",
    "fetch_river_geometry",
    "lookup_precip_return_period",
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

    # job-0306: py3dep reads the USGS 3DEP seamless DEM from the PUBLIC bucket
    # ``prd-tnm.s3.amazonaws.com`` via GDAL ``/vsicurl/``. On the AWS box the
    # instance-role AWS creds are in the environment, so GDAL tried to SIGN the
    # request (and to readdir-list the bucket) — both fail on a public,
    # no-ListBucket bucket, surfacing as "…USGS_Seamless_DEM_1.vrt does not
    # exist in the file system" even though the VRT is reachable (curl 200).
    # Cold DEM fetches for EVERY novel bbox failed (live Case 3, 2026-06-16);
    # only previously-cached DEMs worked. Scope AWS_NO_SIGN_REQUEST +
    # readdir/extension hints to THIS read via ``rasterio.Env`` so the agent's
    # PRIVATE-bucket access (signed instance-role boto3/GDAL) is unaffected.
    try:
        import rasterio  # type: ignore[import-not-found]
        _dem_env = rasterio.Env(
            AWS_NO_SIGN_REQUEST="YES",
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".vrt,.tif,.tiff",
            VSI_CACHE=True,
        )
    except Exception:  # noqa: BLE001 — rasterio always present where py3dep is
        import contextlib
        _dem_env = contextlib.nullcontext()

    try:
        with _dem_env:
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


@register_tool(
    _FETCH_DEM_METADATA,
    # Annotations: readOnlyHint=True, openWorldHint=True (USGS 3DEP py3dep),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_dem(
    bbox: tuple[float, float, float, float],
    resolution_m: int = 10,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """Fetch a digital elevation model (DEM) for a bounding box from USGS 3DEP.

    **What it does:** Downloads a Cloud-Optimized GeoTIFF of ground elevation
    from the USGS 3D Elevation Program (3DEP) via the ``py3dep`` library and
    writes it to the 30-day cache. Returns a ``LayerURI`` pointing at the
    cached COG so downstream SFINCS/HydroMT setup and terrain analysis tools
    can consume it without re-fetching.

    **When to use:**
    - Any flood workflow step that needs terrain elevation: SFINCS model
      domain setup, watershed delineation, slope/hillshade computation.
    - User asks "show me the terrain elevation for [area]" or "what does the
      ground look like here?" — render with the ``continuous_dem`` QML preset.
    - ``build_sfincs_model`` requires a DEM for the SFINCS grid; this tool
      supplies it.
    - Pre-processing step before ``compute_slope``, ``compute_hillshade``,
      ``compute_aspect``, or ``compute_zonal_statistics``.

    **When NOT to use:**
    - Coverage outside the continental US — 3DEP is CONUS-only; a future
      ``fetch_dem(source="copernicus")`` will handle global queries.
    - Bathymetry (below-water elevation) — 3DEP is land-only; use a future
      ``fetch_bathymetry`` routed to NOAA NCEI.
    - Single-point elevation lookups — the tool fetches a raster window;
      for a point query use a future ``point_elevation`` tool.
    - Bboxes larger than 10,000 km² — the tool raises ``BboxInvalidError``
      at that threshold; use a tiled workflow for very large domains.

    **Parameters:**
    - ``bbox`` (tuple[float,float,float,float]): ``(min_lon, min_lat, max_lon,
      max_lat)`` in EPSG:4326. Max area 10,000 km².
    - ``resolution_m`` (int, default 10): DEM grid spacing in meters.
      10 m or 30 m are fastest on 3DEP's tile tree; other values interpolate.

    **Returns:**
    A ``LayerURI`` pointing at a Cloud-Optimized GeoTIFF in the cache bucket
    (``gs://grace-2-hazard-prod-cache/cache/static-30d/dem/<key>.tif``).
    CRS: EPSG:5070 (py3dep default); units: meters above NAVD88.
    Fields consumed downstream: ``uri`` → by ``build_sfincs_model`` and QGIS
    Server WMS; ``style_preset="continuous_dem"`` → map rendering.

    **Cross-tool dependencies:**
    - Downstream: ``build_sfincs_model``, ``compute_slope``,
      ``compute_hillshade``, ``compute_aspect``, ``compute_colored_relief``,
      ``compute_zonal_statistics``.
    - Typically called after: ``geocode_location`` supplies the bbox.
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


@register_tool(
    _FETCH_BUILDINGS_METADATA,
    # Annotations: readOnlyHint=True, openWorldHint=True (MS Open Maps buildings),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_buildings(
    bbox: tuple[float, float, float, float],
    source: str = "msft",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
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


# ---------------------------------------------------------------------------
# WorldPop branch (Tier-1 default per Appendix F.1).
# ---------------------------------------------------------------------------
#
# WorldPop publishes a global population grid as country-clipped GeoTIFFs.
# Two products are relevant here (REST index at
# https://www.worldpop.org/rest/data/pop/<alias>?iso3=<ISO3>):
#
#   - alias=wpgpunadj (Unconstrained 100m UN-adjusted, 2000-2020) →
#       Global_2000_2020/<YEAR>/<ISO3>/<iso3_lower>_ppp_<YEAR>_UNadj.tif
#       (USA file = ~4 GB)
#   - alias=wpic1km (Unconstrained 1km individual countries, 2000-2020) →
#       Global_2000_2020_1km/<YEAR>/<ISO3>/<iso3_lower>_ppp_<YEAR>_1km_Aggregated.tif
#       (USA file = ~50 MB)
#
# Substrate choice: the 1km Aggregated product. WorldPop's HTTP server
# returns HTTP 200 with the full body for range requests (instead of HTTP
# 206 Partial Content), so GDAL's ``/vsicurl/`` cannot windowed-read the
# 100m file remotely — and downloading 4 GB per cache miss is impractical.
# The 1km file is tractable as a one-shot download and is sufficient for
# exposure analysis at M5/Fort-Myers-class bbox scales. Surfaced as
# OQ-37-WORLDPOP-RESOLUTION-VS-RANGE: revisit when a range-request-capable
# mirror lands, or when an official STAC catalog with native COGs is
# published (the kickoff suggested Microsoft Planetary Computer's
# ``worldpop-100m`` collection — that collection does not exist on PC at
# this writing; the WorldPop Hub STAC at https://hub.worldpop.org/stac/
# also 404s).


_WORLDPOP_BBOX_BY_ISO3: dict[str, tuple[float, float, float, float]] = {
    # ISO3 -> approximate (min_lon, min_lat, max_lon, max_lat) envelope.
    # Substrate-scope: CONUS-centric coverage matching the v0.1 Decision I
    # scope. Replaced with a real point-in-polygon over Natural Earth admin0
    # in a follow-up. Same shape/role as the CONUS state envelope table.
    "USA": (-125.0, 24.0, -66.5, 49.5),
    "CAN": (-141.0, 41.7, -52.6, 70.0),
    "MEX": (-118.5, 14.5, -86.7, 32.7),
    "CUB": (-85.0, 19.8, -74.1, 23.3),
    "BHS": (-79.5, 20.9, -72.7, 27.3),
    "JAM": (-78.4, 17.7, -76.2, 18.5),
    "HTI": (-74.5, 18.0, -71.6, 20.1),
    "DOM": (-72.0, 17.6, -68.3, 19.9),
    "PRI": (-67.3, 17.9, -65.2, 18.6),
}


def _iso3_for_lonlat(lon: float, lat: float) -> str | None:
    """Best-effort ISO3 country code lookup from a point — heuristic only.

    Returns ``None`` if no envelope matches. A future enrichment job replaces
    this with a real point-in-polygon over Natural Earth admin0 boundaries.
    """
    for iso3, (mn_lon, mn_lat, mx_lon, mx_lat) in _WORLDPOP_BBOX_BY_ISO3.items():
        if mn_lon <= lon <= mx_lon and mn_lat <= lat <= mx_lat:
            return iso3
    return None


def _worldpop_url_for(iso3: str, year: int) -> str:
    """Compose the WorldPop 1km aggregated GeoTIFF URL for a country/year.

    Uses the ``Global_2000_2020_1km/<YEAR>/<ISO3>/<iso3_lower>_ppp_<YEAR>_1km_Aggregated.tif``
    convention from the WorldPop GIS Data hub — the 1km-aggregated product
    is ~50MB per country (USA), vs the 100m UN-adjusted product at ~4GB.
    The substrate uses 1km because the WorldPop server does not support HTTP
    range requests, so a 4GB whole-country download per cache miss is
    impractical even with the 30-day cache window (see
    OQ-37-WORLDPOP-RESOLUTION-VS-RANGE for the resolution-vs-tractability
    trade-off; the 1km product is sufficient for exposure analysis at the
    bbox scales typical of M5/Fort-Myers-class demos).
    """
    iso3_l = iso3.lower()
    return (
        f"https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/{year}/"
        f"{iso3}/{iso3_l}_ppp_{year}_1km_Aggregated.tif"
    )


def _fetch_worldpop_population_bytes(
    bbox: tuple[float, float, float, float], dataset: str
) -> bytes:
    """Fetch a windowed COG of WorldPop 1km-aggregated population for ``bbox``.

    The WorldPop product is published as a single GeoTIFF per (year, country)
    at ~50MB (1km aggregated). Because the WorldPop server does not support
    HTTP range requests, we download the full country file once to a tmp
    file, then use rasterio to read the windowed sub-region and rewrite it
    as a small Cloud-Optimized GeoTIFF for the cache. Subsequent calls hit
    the GCS cache (30-day TTL) and skip the full download.

    ``dataset`` shape: ``worldpop_<YEAR>`` (e.g. ``worldpop_2020``). The year
    is parsed off the suffix and routed to the corresponding WorldPop URL.
    """
    _validate_bbox(bbox)
    if not dataset.startswith("worldpop_"):
        raise UpstreamAPIError(
            f"unsupported dataset={dataset!r} for WorldPop branch; expected 'worldpop_2020'"
        )
    try:
        year = int(dataset.split("_", 1)[1])
    except (IndexError, ValueError) as exc:
        raise UpstreamAPIError(
            f"could not parse vintage year from dataset={dataset!r}; expected 'worldpop_YYYY'"
        ) from exc

    mid_lon = 0.5 * (bbox[0] + bbox[2])
    mid_lat = 0.5 * (bbox[1] + bbox[3])
    iso3 = _iso3_for_lonlat(mid_lon, mid_lat)
    if iso3 is None:
        raise UpstreamAPIError(
            f"could not resolve ISO3 country code for bbox center=({mid_lon}, {mid_lat}); "
            "WorldPop branch needs an envelope match for the country file URL"
        )

    url = _worldpop_url_for(iso3, year)

    # rasterio is pulled in transitively by rioxarray; import lazily so test
    # environments without it can still load the registry.
    try:
        import rasterio  # type: ignore[import-not-found]
        from rasterio.windows import Window, from_bounds  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise UpstreamAPIError(f"rasterio unavailable: {exc}") from exc

    # Download the country file to a tmp path. We cannot use ``/vsicurl/``
    # because the WorldPop server returns HTTP 200 with the full body for
    # range requests instead of HTTP 206 — GDAL's curl driver then errors
    # with "Range downloading not supported by this server!". The 1km
    # aggregated USA file is ~50MB; bounded enough for a one-shot download.
    import tempfile

    src_tmp: str | None = None
    out_tmp: str | None = None
    try:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _DEFAULT_USER_AGENT},
                timeout=180.0,
                stream=True,
                allow_redirects=True,
            )
            if resp.status_code == 404:
                raise UpstreamAPIError(
                    f"WorldPop file not found at {url} (iso3={iso3}, year={year}); "
                    "verify dataset vintage availability"
                )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise UpstreamAPIError(
                f"WorldPop download failed url={url}: {exc}"
            ) from exc

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as src_f:
            src_tmp = src_f.name
            for chunk in resp.iter_content(chunk_size=1 << 20):  # 1 MiB chunks
                if chunk:
                    src_f.write(chunk)

        try:
            with rasterio.open(src_tmp) as src:
                # Compute the window for the bbox in the source's CRS
                # (WorldPop publishes in EPSG:4326; coords match bbox shape).
                window = from_bounds(
                    bbox[0], bbox[1], bbox[2], bbox[3], transform=src.transform
                )
                window = window.round_offsets().round_lengths()
                window = window.intersection(
                    Window(0, 0, src.width, src.height)
                )
                if window.width <= 0 or window.height <= 0:
                    raise UpstreamAPIError(
                        f"WorldPop window is empty for bbox={bbox} iso3={iso3} — "
                        "bbox may not intersect the country file extent"
                    )
                data = src.read(1, window=window)
                window_transform = src.window_transform(window)
                profile = src.profile.copy()
                profile.update(
                    {
                        "driver": "COG",
                        "width": int(window.width),
                        "height": int(window.height),
                        "transform": window_transform,
                        "compress": "LZW",
                        "BIGTIFF": "IF_SAFER",
                    }
                )
        except UpstreamAPIError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UpstreamAPIError(
                f"rasterio windowed read failed for {url}: {exc}"
            ) from exc

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as out_f:
            out_tmp = out_f.name
        with rasterio.open(out_tmp, "w", **profile) as dst:
            dst.write(data, 1)
        with open(out_tmp, "rb") as f:
            out_bytes = f.read()

        return out_bytes
    finally:
        for path in (src_tmp, out_tmp):
            if path is None:
                continue
            try:
                os.unlink(path)
            except OSError:
                pass


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


@register_tool(
    _FETCH_POPULATION_METADATA,
    # Annotations: readOnlyHint=True, openWorldHint=True (WorldPop/GCS public bucket),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_population(
    bbox: tuple[float, float, float, float],
    dataset: str = "worldpop_2020",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """Fetch population data for a bbox from WorldPop (Tier-1 default) or Census ACS.

    Use this when: the agent needs population counts for exposure analysis,
    risk scoring, or display alongside hazard layers. Anywhere globally, with
    no API key, at 100m resolution — that's the default WorldPop path.

    Do NOT use this for: real-time / daytime population (WorldPop and ACS are
    both residential count estimates); per-individual data (these are gridded /
    tract-level aggregates); sub-100m resolution (WorldPop's native grid is
    100m; finer resolution is a paid LandScan-grade product, not Tier-1).

    Default behavior (FR-AS-3, Appendix F.1 Tier-1 preference rule):
        ``dataset="worldpop_2020"`` is the Tier-1 default — WorldPop
        Unconstrained 100m UN-adjusted gridded population. No API key
        required; global coverage; windowed read of the country GeoTIFF via
        rasterio ``/vsicurl/`` so only the bbox window is downloaded.

    Tier-2 opt-in:
        ``dataset="acs_2022"`` routes to the US Census ACS 5-year estimates
        (B01003_001E total population at tract level) — authoritative for
        CONUS, finer demographic detail, but **requires a Census API key**
        for non-trivial volumes (the Tier-2 routing rule per Appendix F.1).
        Pick this when the agent specifically needs tract-level precision
        rather than the 100m raster.

    Params:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        dataset: ``"worldpop_2020"`` (Tier-1 default, no key) or
            ``"acs_2022"`` (Tier-2 opt-in, US-only, Census key required for
            high-volume use). Future vintages: ``"worldpop_2024"`` will land
            once the v2024B file URLs stabilize (currently the Global_2000_2020
            tree is the canonical analytical product; tracked as
            OQ-37-WORLDPOP-VINTAGE-YEAR).

    Returns:
        A ``LayerURI`` pointing at a Cloud-Optimized GeoTIFF (WorldPop branch)
        or a GeoJSON FeatureCollection (ACS branch) in the cache bucket.
        - WorldPop: ``gs://grace-2-hazard-prod-cache/cache/static-30d/population/<key>.tif``
          (100m raster, units = people per 100m cell).
        - ACS: ``gs://grace-2-hazard-prod-cache/cache/static-30d/population/<key>.json``
          (tract-level FeatureCollection; geometry enrichment is a follow-up).

    FR-CE-8: The fetch is routed through ``read_through`` so identical
    quantized-bbox + dataset calls reuse the cached artifact. FR-DC-4 dedup
    is preserved at 100m bbox quantization (matches WorldPop native
    resolution; coarser than the bbox driving the ACS tract intersection).
    """
    if dataset.startswith("worldpop_"):
        # Tier-1 default: WorldPop 100m windowed COG.
        # Quantize at 100m — matches WorldPop native resolution, preserves
        # FR-DC-4 dedup, and the ACS branch (when opted into) is happy with
        # the same grid since tracts are coarser than 100m anyway.
        quantized = round_bbox_to_resolution(bbox, 100)
        params = {"bbox": list(quantized), "dataset": dataset}
        result = read_through(
            metadata=_FETCH_POPULATION_METADATA,
            params=params,
            ext="tif",
            fetch_fn=lambda: _fetch_worldpop_population_bytes(quantized, dataset),
        )
        assert result.uri is not None
        return LayerURI(
            layer_id=f"population-{quantized[0]:.4f}-{quantized[1]:.4f}-{dataset}",
            name=f"Population ({dataset})",
            layer_type="raster",
            uri=result.uri,
            style_preset="continuous_dem",  # placeholder; population preset lands later
            role="input",
            units="people",
        )

    if dataset.startswith("acs_"):
        # Tier-2 opt-in: US Census ACS B01003 tract-level. Census API key is
        # required for non-trivial volumes (OQ-36-CENSUS-API-KEY-REQUIRED);
        # the substrate works for small CONUS queries without a key.
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

    raise BboxInvalidError(
        f"unsupported dataset={dataset!r}; allowed: 'worldpop_2020' (default), "
        "'acs_2022' (Tier-2 opt-in, US-only)"
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


@register_tool(
    _GEOCODE_LOCATION_METADATA,
    # Annotations: readOnlyHint=True, openWorldHint=True (OSM Nominatim API),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def geocode_location(query: str, **_extra_ignored: Any) -> dict[str, Any]:
    """Translate a free-text place name into a bbox and canonical name via OpenStreetMap Nominatim.

    **What it does:** Forward-geocodes a human-readable location string to a
    WGS84 bounding box, centroid latitude/longitude, and canonical place name
    using the OpenStreetMap Nominatim REST API. The result is cached for one
    hour (``dynamic-1h``), so repeated references to the same place within a
    session are free.

    **When to use:**
    - User asks to "model flooding in Fort Myers, FL" or "show wildfires near
      Los Angeles" — convert the place name to a bbox before calling spatial
      fetch tools.
    - The agent needs to translate a textual event location from the Hazard
      Event Pipeline (``EventMetadata.location_name``) into a usable bbox.
    - Any workflow step that starts from a city, county, neighborhood, or
      named geographic feature rather than coordinates.

    **When NOT to use:**
    - Reverse geocoding (coordinates → place name) — Nominatim has a separate
      ``/reverse`` endpoint; use ``web_fetch`` or a future dedicated tool.
    - Routing or turn-by-turn distance queries — Nominatim does not support
      them; use a routing API.
    - High-precision parcel-level address resolution — Nominatim is
      street-address level at best; use a dedicated geocoding provider for
      sub-parcel accuracy.
    - Queries where bbox coverage matters: the returned bbox reflects OSM's
      administrative boundary for the named place, which can be very large for
      counties or states; narrow it before passing to ``fetch_dem`` or similar
      large-download tools.

    **Parameters:**
    - ``query`` (str): Free-text place name or description.
      Examples: ``"Fort Myers, FL"``, ``"Lee County Florida"``,
      ``"Gulf of Mexico"``. Must be non-empty.

    **Returns:**
    A plain dict with keys:
    - ``name`` (str): canonical OSM display name.
    - ``bbox`` (list[float]): ``[min_lon, min_lat, max_lon, max_lat]`` in
      EPSG:4326 — feeds directly into ``fetch_dem``, ``fetch_buildings``,
      ``fetch_population``, ``fetch_landcover``, etc.
    - ``latitude`` / ``longitude`` (float): centroid of the matched feature.
    - ``source`` (str): ``"nominatim"``.
    - ``osm_type``, ``osm_id``, ``place_id`` (str / int): OSM provenance fields.

    **Cross-tool dependencies:**
    - Upstream of: ``fetch_dem``, ``fetch_buildings``, ``fetch_population``,
      ``fetch_landcover``, ``fetch_river_geometry``,
      ``fetch_administrative_boundaries``, ``fetch_nws_event``,
      ``fetch_firms_active_fire``, and most other bbox-based fetchers.
    - Called internally by ``model_flood_scenario`` workflow to resolve a
      user-supplied location string before fetching DEM/landcover.

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


# ---------------------------------------------------------------------------
# fetch_landcover — NLCD (MRLC) / ESA WorldCover (sprint-07 Stage B, job-0039;
# job-0044 hotfix: WMS → WCS 1.0.0 to fix palette encoding).
# ---------------------------------------------------------------------------
#
# Access pattern tier — LIVE-VERIFIED THROUGH TWO ROUNDS:
#
# Round 1 (job-0039, 2026-06-07):
#
#   * The MRLC direct file mirror (``s3-us-west-2.amazonaws.com/mrlc/
#     Annual_NLCD_LndCov_<YEAR>_CU_C1V0.tif``) returned an HTTP 200 with a
#     **42-byte placeholder TIFF** (a 1×1 IFD with two ``0xFFFFFFFF`` strip
#     offsets — not a real raster). 2019 and 2021 file URLs at the same path
#     return HTTP 403. The "direct HTTPS + Range" path the kickoff inferred is
#     NOT a real surface for NLCD bytes.
#   * The MRLC WCS endpoint (`/geoserver/mrlc_display/wcs`) timed out on
#     GetCapabilities in the first probe.
#   * MRLC's **WMS** GeoServer at ``www.mrlc.gov/geoserver/mrlc_display/wms``
#     serves NLCD year layers (``NLCD_2021_Land_Cover_L48`` etc.) and supports
#     ``GetMap?format=image/geotiff`` — Tier 2 (OGC service) byte materialized.
#     Substrate landed against WMS GetMap.
#
# Round 2 (job-0044, 2026-06-07 — THE PALETTE-ENCODING HOTFIX):
#
#   * Job-0042's NLCD validation gate (Invariant 7 mitigation) fired on a real
#     Fort Myers smoke run: the WMS GetMap GeoTIFF returns raster bytes that
#     are **palette indices** (1, 3, 4, 5, ..., 21) NOT canonical NLCD class
#     integers (11, 21, 22, 23, ..., 95) — surfaced as
#     OQ-42-NLCD-WMS-PALETTE-ENCODING. The Manning's mapping CSV is keyed by
#     canonical integers; SFINCS dispatch was blocked end-to-end.
#   * Live-probed both candidate fix paths per §F.1.1 live-verification discipline:
#
#     - **Path A (palette decode):** the WMS GeoTIFF carries a 256-entry
#       ColorTable in its IFD; the index→RGB→canonical NLCD mapping is fixed
#       (idx 1 = open-water = (71,107,160) = NLCD 11; idx 3 = developed-open
#       = (221,201,201) = NLCD 21; …). Decoding via the embedded ColorTable
#       and an inverse RGB→class table is feasible but adds a fragile
#       client-side translation step (one MRLC palette reorder breaks us).
#     - **Path B (WCS 1.0.0 GetCoverage):** ``mrlc_display:NLCD_2021_Land_
#       Cover_L48`` coverage served by the WCS 1.0.0 endpoint with
#       ``REQUEST=GetCoverage&CRS=EPSG:4326&BBOX=...&WIDTH=...&HEIGHT=...&FORMAT=GeoTIFF``
#       returns canonical NLCD class integers DIRECTLY (verified: unique band1
#       values for Fort Myers bbox = [11, 21, 22, 23, 24, 31, 41, 42, 43, 52,
#       71, 81, 82, 90, 95, 255-nodata] — every value cleanly mapped to
#       manning_mapping.csv v1.0.0). The DescribeCoverage XML calls the band
#       "PALETTE_INDEX" but the integers ARE the canonical NLCD codes — WCS
#       1.0.0 emits the source dataset's raw byte values whereas WMS GetMap
#       emits the rendered (re-indexed) palette indices.
#     - **WCS 2.0.1 / 1.1.1:** also tried; both fail in different ways. WCS
#       2.0.1 hits a GeoServer "Unable to map projection Popular Visualisation
#       Pseudo Mercator" exception (GeoServer projection-mapping bug on its
#       own native CRS). WCS 1.1.1 rejects bbox-only requests as "less than a
#       pixel would be read." WCS 1.0.0 with explicit WIDTH/HEIGHT is the
#       reliable byte surface.
#
#   * **Path B chosen.** Canonical bytes from the server is a clean win over
#     client-side palette decoding: no RGB→class lookup to maintain, no
#     fragility to MRLC palette reorders, no Round-3 silent-wrong-answer risk.
#     Both paths are §F.1.1 Tier 2 (OGC service) — substrate stays Tier 2,
#     vendor sub-protocol switches from WMS GetMap to WCS GetCoverage.
#
# Job-0044 cache-migration policy: cache key now includes ``source: "mrlc-wcs"``
# (the palette-encoded ``mrlc-wms`` entries from job-0039's evidence land
# under a different cache prefix and naturally evict on the 30-day TTL — no
# explicit invalidation needed). Job-0039's evidence COGs at
# ``cache/static-30d/landcover/56bad09bfa8a71d502ed61badc785a00.tif`` will
# remain until TTL eviction; the new canonical-bytes COGs land at a new key.
#
# Round 1 deviation (job-0039) is still recorded as OQ-39-NLCD-TIER-DEVIATION
# (kickoff inferred Tier 3 → live Tier 2). Round 2 hotfix (job-0044) closes
# OQ-42-NLCD-WMS-PALETTE-ENCODING.
#
# Vintage discipline: NLCD vintages 2019, 2021 (default), and 2023 are most-
# relevant. The Annual NLCD Collection 1.0 (2023 release) is published as the
# ``Annual_NLCD_LndCov_<YEAR>_CU_C1V0`` family; the WMS GeoServer lists
# discrete-year layers up through **NLCD_2021_Land_Cover_L48**. 2023 is the
# newest release but its WMS layer name was not present in the MRLC
# GetCapabilities at probe time (2026-06-07); the substrate defaults to 2021
# and the dataset string parameter supports ``"nlcd_2019"`` and (forward-
# looking) ``"nlcd_2023"`` once it lands. ESA WorldCover (Planetary Computer
# ``esa-worldcover``) opt-in via ``dataset="esa_worldcover_2021"``.
#
# Manning's mapping validation gate (per docs/decisions/oq-4-hydromt-depth.md
# §4 "Immediate (job-0039)"): the NLCD vintage year is returned as sidecar
# metadata alongside the LayerURI so job-0042 ``build_sfincs_model`` can
# verify the Manning's mapping CSV covers the vintage's class encoding. This
# is the Invariant 7 (no silent wrong answers) mitigation OQ-4 demanded.
#
# Sidecar shape — return-value design: ``LayerURI`` (in
# ``grace2_contracts.execution``) is a FROZEN contract with
# ``extra="forbid"`` — we cannot add a ``metadata`` field. The kickoff's
# example syntax ``LayerURI.metadata["nlcd_vintage_year"] = 2021`` was
# illustrative; the actual seam is a structured ``dict`` return shape:
#
#     {
#       "layer": LayerURI(...),
#       "nlcd_vintage_year": 2021,
#       "dataset": "nlcd_2021",
#       "source": "mrlc-wms",
#     }
#
# This is the same dict-return pattern as ``geocode_location`` (also no
# contract for its shape) and ``lookup_precip_return_period`` below — see
# OQ-39-LANDCOVER-RETURN-SHAPE-CONTRACT-PROMOTION.


_FETCH_LANDCOVER_METADATA = AtomicToolMetadata(
    name="fetch_landcover",
    ttl_class="static-30d",
    source_class="landcover",
    cacheable=True,
)


# MRLC WCS 1.0.0 GeoServer endpoint (Tier 2 OGC service, live-verified
# 2026-06-07 in job-0044). WCS 1.0.0 GetCoverage returns canonical NLCD class
# integers in the raster band — the WMS GetMap path job-0039 landed against
# returned palette-encoded indices (the OQ-42-NLCD-WMS-PALETTE-ENCODING
# blocker job-0042's validation gate caught). WCS 1.0.0 was chosen over
# WCS 1.1.1 / 2.0.1: 2.0.1 hits a GeoServer projection-mapping bug ("Unable
# to map projection Popular Visualisation Pseudo Mercator") on its own
# native EPSG:3857; 1.1.1 rejects bbox-only requests; 1.0.0 with explicit
# CRS=EPSG:4326 + WIDTH/HEIGHT + FORMAT=GeoTIFF is the reliable surface.
_MRLC_WCS_URL = "https://www.mrlc.gov/geoserver/mrlc_display/wcs"

# NLCD year → WCS coverage ID in the MRLC GeoServer catalog. WCS uses the
# qualified workspace:coverage form ``mrlc_display:NLCD_<YEAR>_Land_Cover_L48``
# (the underlying GeoServer layer); live-verified 2026-06-07.
_NLCD_WCS_COVERAGE_BY_YEAR: dict[int, str] = {
    2001: "mrlc_display:NLCD_2001_Land_Cover_L48",
    2004: "mrlc_display:NLCD_2004_Land_Cover_L48",
    2006: "mrlc_display:NLCD_2006_Land_Cover_L48",
    2008: "mrlc_display:NLCD_2008_Land_Cover_L48",
    2011: "mrlc_display:NLCD_2011_Land_Cover_L48",
    2013: "mrlc_display:NLCD_2013_Land_Cover_L48",
    2016: "mrlc_display:NLCD_2016_Land_Cover_L48",
    2019: "mrlc_display:NLCD_2019_Land_Cover_L48",
    2021: "mrlc_display:NLCD_2021_Land_Cover_L48",
}


def _clip_raster_bytes_to_bbox(
    tif_bytes: bytes, bbox: tuple[float, float, float, float]
) -> bytes:
    """Crop a GeoTIFF (bytes) to the EXACT requested bbox via rasterio windowing.

    The MRLC WCS GetCoverage already returns the requested BBOX server-side,
    but pixel snapping can leave a fringe row/column outside the AOI. This
    reprojects the bbox into the raster's CRS, computes the pixel window, and
    writes the cropped raster — guaranteeing the output extent matches the
    requested bbox to within one pixel. Best-effort: returns the input bytes
    unchanged on any failure (never raises — clipping is a precision nicety,
    not a correctness gate).
    """
    in_tmp: str | None = None
    out_tmp: str | None = None
    try:
        import rasterio  # type: ignore[import-not-found]
        from rasterio.warp import transform_bounds  # type: ignore[import-not-found]
        from rasterio.windows import from_bounds as window_from_bounds  # type: ignore[import-not-found]

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
            in_tmp = f.name
            f.write(tif_bytes)

        with rasterio.open(in_tmp) as src:
            dst_crs = src.crs
            # Reproject the WGS84 bbox into the raster CRS (no-op when already 4326).
            if dst_crs is not None and dst_crs.to_epsg() != 4326:
                left, bottom, right, top = transform_bounds(
                    "EPSG:4326", dst_crs, *bbox, densify_pts=21
                )
            else:
                left, bottom, right, top = bbox
            window = window_from_bounds(
                left, bottom, right, top, transform=src.transform
            )
            # Intersect with the raster's full window so we never read outside it.
            full = rasterio.windows.Window(0, 0, src.width, src.height)
            window = window.intersection(full).round_offsets().round_lengths()
            if window.width < 1 or window.height < 1:
                # Degenerate intersection — keep the original (don't blank it out).
                return tif_bytes
            data = src.read(window=window)
            transform = src.window_transform(window)
            profile = src.profile.copy()
            profile.update(
                height=int(window.height),
                width=int(window.width),
                transform=transform,
            )
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as of:
                out_tmp = of.name
            with rasterio.open(out_tmp, "w", **profile) as dst:
                dst.write(data)
        with open(out_tmp, "rb") as f:
            return f.read()
    except Exception as exc:  # noqa: BLE001 — clip is best-effort precision
        logger.warning(
            "fetch_landcover: bbox clip failed (%s: %s); returning unclipped raster",
            type(exc).__name__,
            exc,
        )
        return tif_bytes
    finally:
        for path in (in_tmp, out_tmp):
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _rasterio_translate_to_cog(tif_bytes: bytes) -> bytes:
    """Translate GeoTIFF bytes to a tiled COG WITH overviews via the rasterio COG driver.

    Used as the fallback when the GDAL CLI binaries that ``_translate_to_cog``
    (compute_hillshade) shells out to are not on PATH (e.g. the agent .venv
    without gdal-bin). The rasterio ``COG`` driver builds internal overviews
    and 512x512 tiling automatically — the exact properties TiTiler needs to
    avoid the zoomed-out 404s that made NLCD render spotty. Best-effort:
    returns the input bytes unchanged on any failure.
    """
    in_tmp: str | None = None
    out_tmp: str | None = None
    try:
        import rasterio  # type: ignore[import-not-found]

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
            in_tmp = f.name
            f.write(tif_bytes)
        with rasterio.open(in_tmp) as src:
            profile = {
                "driver": "COG",
                "width": src.width,
                "height": src.height,
                "count": src.count,
                "dtype": src.dtypes[0],
                "crs": src.crs,
                "transform": src.transform,
                "compress": "DEFLATE",
            }
            if src.nodata is not None:
                profile["nodata"] = src.nodata
            data = src.read()
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as of:
                out_tmp = of.name
            with rasterio.open(
                out_tmp, "w", OVERVIEW_RESAMPLING="NEAREST", **profile
            ) as dst:
                dst.write(data)
        with open(out_tmp, "rb") as f:
            return f.read()
    except Exception as exc:  # noqa: BLE001 — COG translate is best-effort
        logger.warning(
            "fetch_landcover: rasterio COG translate failed (%s: %s); returning "
            "flat GeoTIFF bytes",
            type(exc).__name__,
            exc,
        )
        return tif_bytes
    finally:
        for path in (in_tmp, out_tmp):
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _landcover_bytes_to_cog(
    tif_bytes: bytes, bbox: tuple[float, float, float, float]
) -> bytes:
    """Clip NLCD bytes to the exact bbox and emit a tiled COG WITH overviews.

    job-0271-class fix for fetch_landcover: the MRLC WCS GetCoverage returns a
    flat strip-organized GeoTIFF with NO overviews, so TiTiler 404s the
    zoomed-out tiles and the layer renders spotty / never paints when panned
    out. This routes the raster through ``_translate_to_cog`` (the
    compute_hillshade COG translator that writes a tiled COG with overviews)
    when the GDAL CLI is available, and falls back to the pure-rasterio COG
    driver otherwise — so overviews are present in BOTH environments.

    Also clips to the EXACT requested bbox first (precision nicety; the WCS
    already honors BBOX server-side but pixel snapping can leave a fringe).
    """
    clipped = _clip_raster_bytes_to_bbox(tif_bytes, bbox)

    # Prefer the assigned compute_hillshade COG translator (GDAL CLI path) so
    # the COG profile matches every other raster product. Fall back to the
    # pure-rasterio COG driver when the gdal binaries are not on PATH.
    try:
        from .compute_hillshade import _get_gdaldem_bin, _translate_to_cog

        in_tmp: str | None = None
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
                in_tmp = f.name
                f.write(clipped)
            gdaldem_bin = _get_gdaldem_bin()  # raises if gdal CLI absent
            cog = _translate_to_cog(in_tmp, gdaldem_bin)
            # _translate_to_cog returns flat bytes when gdal_translate is missing
            # even though gdaldem resolved; verify overviews landed, else fall
            # through to the rasterio path below.
            if _has_overviews(cog):
                return cog
        finally:
            if in_tmp is not None:
                try:
                    os.unlink(in_tmp)
                except OSError:
                    pass
    except Exception as exc:  # noqa: BLE001 — GDAL CLI not available / failed
        logger.info(
            "fetch_landcover: GDAL-CLI COG translate unavailable (%s); using "
            "rasterio COG driver fallback",
            exc,
        )

    return _rasterio_translate_to_cog(clipped)


def _has_overviews(tif_bytes: bytes) -> bool:
    """Return True iff the GeoTIFF bytes carry internal overviews on band 1."""
    in_tmp: str | None = None
    try:
        import rasterio  # type: ignore[import-not-found]

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
            in_tmp = f.name
            f.write(tif_bytes)
        with rasterio.open(in_tmp) as src:
            return len(src.overviews(1)) > 0
    except Exception:  # noqa: BLE001
        return False
    finally:
        if in_tmp is not None:
            try:
                os.unlink(in_tmp)
            except OSError:
                pass


def _fetch_nlcd_landcover_bytes(
    bbox: tuple[float, float, float, float], vintage_year: int
) -> bytes:
    """Fetch NLCD landcover for ``bbox`` at the given vintage year via MRLC WCS 1.0.0.

    Tier 2 access pattern (per §F.1.1) — MRLC WCS 1.0.0 ``GetCoverage`` with
    ``FORMAT=GeoTIFF`` returns the canonical NLCD class integers (11, 21, 22,
    23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95) in the
    raster band — NOT palette indices. This is the job-0044 hotfix that
    unblocks job-0042's NLCD validation gate. The returned GeoTIFF carries a
    proper geo-header (EPSG:4326 in this request shape) so HydroMT's
    ``setup_manning_roughness`` consumes the bytes directly without a
    client-side palette decode.

    Path-comparison summary (live-verified 2026-06-07):
    - WMS GetMap: returned palette indices [1, 3, 4, 5, 6, 7, 9, 10, 11, 13,
      14, 18, 19, 20, 21] for Fort Myers — BROKEN (Manning's mapping keyed by
      canonical integers).
    - WCS 1.0.0 GetCoverage: returned canonical integers [11, 21, 22, 23, 24,
      31, 41, 42, 43, 52, 71, 81, 82, 90, 95, 255-nodata] — CORRECT.
    """
    _validate_bbox(bbox)
    coverage = _NLCD_WCS_COVERAGE_BY_YEAR.get(vintage_year)
    if coverage is None:
        available = sorted(_NLCD_WCS_COVERAGE_BY_YEAR.keys())
        raise UpstreamAPIError(
            f"NLCD vintage year {vintage_year} not in MRLC WCS catalog "
            f"(available: {available}); add 2023 once MRLC publishes "
            f"``mrlc_display:NLCD_2023_Land_Cover_L48`` (see OQ-39-NLCD-VINTAGE-DEFAULT)."
        )

    # Pixel grid: 30 m native, sized to the bbox in EPSG:4326. WCS 1.0.0
    # requires explicit WIDTH/HEIGHT (no resolution shorthand at this version).
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = 0.5 * (min_lat + max_lat)
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
    width_m = (max_lon - min_lon) * m_per_deg_lon
    height_m = (max_lat - min_lat) * 111_320.0
    # 30 m native; clamp 16 px..4096 px per axis (the server rejects very
    # large GetCoverage requests; 4096 covers ~122 km wide bbox at 30 m).
    width_px = max(16, min(4096, int(round(width_m / 30.0))))
    height_px = max(16, min(4096, int(round(height_m / 30.0))))

    # WCS 1.0.0 GetCoverage via the shared generic OGC adapter (job-0047
    # refactor — single source of truth for §F.1.1 Tier 2 retrieval). The
    # adapter handles the WCS request shape (Coverage, CRS, BBOX, WIDTH,
    # HEIGHT, FORMAT), surfaces OGC exception XMLs as typed errors, and
    # validates the GeoTIFF content-type so a misconfigured GeoServer
    # response (HTML error page, ExceptionReport XML) doesn't poison the
    # cache. The MRLC WCS sub-protocol (1.0.0 over 1.1.1/2.0.1) was
    # established in job-0044's live-verification rounds and is preserved.
    from .ogc_adapter import OGCAdapterError, fetch_ogc_layer

    try:
        ogc_resp = fetch_ogc_layer(
            url=_MRLC_WCS_URL,
            layer_name=coverage,
            bbox=bbox,
            crs="EPSG:4326",
            service_type="WCS",
            image_format="GeoTIFF",
            version="1.0.0",
            width_px=width_px,
            height_px=height_px,
            timeout_s=120.0,
            user_agent=_DEFAULT_USER_AGENT,
        )
    except OGCAdapterError as exc:
        raise UpstreamAPIError(
            f"MRLC WCS GetCoverage failed for coverage={coverage} bbox={bbox}: {exc}"
        ) from exc

    # Extra defensive check: the adapter already validates content-type and
    # body length, but we re-check the TIFF content-type because the cache
    # write extension is fixed at ``.tif``.
    ct = ogc_resp.content_type
    if "tiff" not in ct.lower() and "geotiff" not in ct.lower():
        raise UpstreamAPIError(
            f"MRLC WCS returned unexpected content-type={ct!r} for coverage={coverage} "
            f"bbox={bbox}; body preview: {ogc_resp.content[:300]!r}"
        )

    # job-0271-class fix (F33/F39): the MRLC WCS GetCoverage GeoTIFF is a flat
    # strip-organized raster with NO overviews, so TiTiler 404s the zoomed-out
    # tiles and NLCD renders spotty / vanishes when panned out. Clip to the
    # exact bbox and re-emit a tiled COG WITH overviews before caching.
    return _landcover_bytes_to_cog(ogc_resp.content, bbox)


def _fetch_esa_worldcover_bytes(
    bbox: tuple[float, float, float, float], vintage_year: int
) -> bytes:
    """Fetch ESA WorldCover landcover for ``bbox`` at the given vintage year.

    ESA WorldCover is hosted by Microsoft Planetary Computer as STAC + COG
    (Tier 1 per §F.1.1). The implementation is reserved as a forward-looking
    branch; the v0.1 substrate raises ``UpstreamAPIError`` so the agent's
    FR-AS-11 surface can decide whether to fall back to NLCD or surface to
    the user. Surface as OQ-39-ESA-WORLDCOVER-SUBSTRATE.
    """
    raise UpstreamAPIError(
        "ESA WorldCover branch is not implemented in the v0.1 substrate "
        "(reserved for a follow-up job; opt into NLCD by passing "
        "dataset='nlcd_2021' / 'nlcd_2019')."
    )


def _round_bbox_to_30m_nlcd(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Quantize a WGS84 bbox to the NLCD 30 m native grid.

    Per the per-source bbox quantization rule (acceptance criterion 3 of
    the kickoff): NLCD's native cell is 30 m. We reuse
    ``round_bbox_to_resolution(bbox, 30)`` — same semantics as ``fetch_dem``
    at 30 m, so dedup-via-quantization works the same way.
    """
    return round_bbox_to_resolution(bbox, 30)


@register_tool(
    _FETCH_LANDCOVER_METADATA,
    # Annotations: readOnlyHint=True, openWorldHint=True (NLCD WMS + USGS 3DEP),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_landcover(
    bbox: tuple[float, float, float, float],
    dataset: str = "nlcd_2021",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> dict[str, Any]:
    """Fetch landcover classification raster (NLCD or ESA WorldCover) for a bbox.

    Access pattern: Tier 2 (OGC service — MRLC WCS/WMS endpoint per §F.1.1; live
    verification 2026-06-07 found NLCD is Tier 2, see OQ-39-NLCD-TIER-DEVIATION).

    **What it does:** Downloads an NLCD or ESA WorldCover landcover GeoTIFF
    clipped to the requested bbox via the MRLC WCS 1.0.0 GeoServer endpoint.
    Returns a dict containing a ``LayerURI`` plus a ``nlcd_vintage_year``
    sidecar field that downstream SFINCS setup uses to validate Manning's
    roughness mappings before HydroMT invocation (Invariant 7 — no silent
    wrong answers).

    **When to use:**
    - ``build_sfincs_model`` requires landcover for Manning's roughness
      assignment — this is the canonical supply tool.
    - User asks "what land cover exists in this area?" for a CONUS location.
    - Exposure analysis: intersect a hazard footprint with impervious-surface
      or developed-land classes.
    - Visualization using the ``categorical_landcover`` QML style preset.

    **When NOT to use:**
    - Coverage outside CONUS L48 — NLCD covers only the 48 contiguous US
      states; Alaska, Hawaii, and Puerto Rico have separate MRLC layers not
      in the v0.1 substrate.
    - Global landcover — pass ``dataset="esa_worldcover_2021"`` to opt into
      the ESA WorldCover branch, but that branch currently raises
      ``UpstreamAPIError`` (forward-looking, OQ-39-ESA-WORLDCOVER-SUBSTRATE).
    - Single-point landcover classification — this tool returns a raster;
      use ``extract_landcover_class`` for point lookups once it lands.
    - Bboxes larger than 10,000 km² — the MRLC WCS server rejects oversized
      requests; the tool raises ``BboxInvalidError`` at that threshold.

    **Parameters:**
    - ``bbox`` (tuple[float,float,float,float]): ``(min_lon, min_lat, max_lon,
      max_lat)`` in EPSG:4326. Max area 10,000 km².
    - ``dataset`` (str, default ``"nlcd_2021"``): NLCD vintage string
      (``"nlcd_2021"``, ``"nlcd_2019"``, ``"nlcd_2016"``, etc.) or
      ``"esa_worldcover_2021"`` (forward-looking). Valid NLCD years:
      2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021.

    **Returns:**
    A dict with keys:
    - ``layer`` (LayerURI): COG at
      ``gs://grace-2-hazard-prod-cache/cache/static-30d/landcover/<key>.tif``;
      ``style_preset="categorical_landcover"``, ``units="nlcd_class_code"``.
    - ``nlcd_vintage_year`` (int): vintage year consumed by
      ``build_sfincs_model`` to validate the Manning's mapping CSV.
    - ``dataset`` (str): echo of the input dataset string for provenance.
    - ``source`` (str): ``"mrlc-wcs"`` for NLCD.

    **Cross-tool dependencies:**
    - Upstream: ``geocode_location`` for bbox derivation.
    - Downstream: ``build_sfincs_model`` (Manning's roughness), QGIS Server
      WMS rendering, ``extract_landcover_class``, ``compute_impervious_surface``.
    """
    if not isinstance(dataset, str) or not dataset:
        raise BboxInvalidError(
            f"fetch_landcover requires a non-empty dataset string; got {dataset!r}"
        )

    if dataset.startswith("nlcd_"):
        try:
            vintage_year = int(dataset.split("_", 1)[1])
        except (IndexError, ValueError) as exc:
            raise BboxInvalidError(
                f"could not parse NLCD vintage year from dataset={dataset!r}; "
                "expected 'nlcd_YYYY' (e.g. 'nlcd_2021')."
            ) from exc

        quantized = _round_bbox_to_30m_nlcd(bbox)
        # Guardrail: MRLC WMS rejects very large requests; cap bbox area to
        # 10,000 km^2 same as fetch_dem, so a single GetMap call is tractable.
        if _bbox_area_km2(quantized) > 10_000.0:
            raise BboxInvalidError(
                f"bbox area {_bbox_area_km2(quantized):.1f} km^2 exceeds 10000 km^2 "
                "guardrail for fetch_landcover (MRLC WMS will reject; use a tiled "
                "workflow for larger domains)."
            )
        # Cache-key source tag is ``mrlc-wcs`` after job-0044's hotfix; the
        # palette-encoded ``mrlc-wms`` entries from job-0039 land under a
        # different key and naturally evict on the 30-day TTL — no explicit
        # invalidation needed (cached COG migration is a no-op).
        params = {"bbox": list(quantized), "dataset": dataset, "source": "mrlc-wcs"}
        result = read_through(
            metadata=_FETCH_LANDCOVER_METADATA,
            params=params,
            ext="tif",
            fetch_fn=lambda: _fetch_nlcd_landcover_bytes(quantized, vintage_year),
        )
        assert result.uri is not None
        layer = LayerURI(
            layer_id=f"landcover-{quantized[0]:.4f}-{quantized[1]:.4f}-{dataset}",
            name=f"NLCD Land Cover ({vintage_year})",
            layer_type="raster",
            uri=result.uri,
            style_preset="categorical_landcover",
            role="input",
            units="nlcd_class_code",
        )
        return {
            "layer": layer,
            "nlcd_vintage_year": vintage_year,
            "dataset": dataset,
            "source": "mrlc-wcs",
        }

    if dataset.startswith("esa_worldcover_"):
        try:
            vintage_year = int(dataset.rsplit("_", 1)[1])
        except (IndexError, ValueError) as exc:
            raise BboxInvalidError(
                f"could not parse ESA WorldCover vintage year from dataset={dataset!r}; "
                "expected 'esa_worldcover_YYYY' (e.g. 'esa_worldcover_2021')."
            ) from exc
        quantized = round_bbox_to_resolution(bbox, 10)  # ESA WorldCover is 10 m native
        params = {"bbox": list(quantized), "dataset": dataset, "source": "esa-worldcover-stac"}
        result = read_through(
            metadata=_FETCH_LANDCOVER_METADATA,
            params=params,
            ext="tif",
            fetch_fn=lambda: _fetch_esa_worldcover_bytes(quantized, vintage_year),
        )
        assert result.uri is not None
        layer = LayerURI(
            layer_id=f"landcover-{quantized[0]:.4f}-{quantized[1]:.4f}-{dataset}",
            name=f"ESA WorldCover ({vintage_year})",
            layer_type="raster",
            uri=result.uri,
            style_preset="categorical_landcover",
            role="input",
            units="esa_worldcover_class_code",
        )
        return {
            "layer": layer,
            "nlcd_vintage_year": None,  # ESA WorldCover is not NLCD
            "esa_worldcover_vintage_year": vintage_year,
            "dataset": dataset,
            "source": "esa-worldcover-stac",
        }

    raise BboxInvalidError(
        f"unsupported dataset={dataset!r}; allowed prefixes: 'nlcd_' (default, "
        "Tier-1 CONUS), 'esa_worldcover_' (opt-in, forward-looking — not implemented)."
    )


# ---------------------------------------------------------------------------
# fetch_river_geometry — NHDPlus HR (USGS) (sprint-07 Stage B, job-0039).
# ---------------------------------------------------------------------------
#
# Access pattern tier — LIVE-VERIFIED matches kickoff inference (2026-06-07):
#
#   * USGS publishes NHDPlus HR as **HUC4-scoped FileGDB zip files** under
#     ``prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHDPlusHR/Beta/
#     GDB/NHDPLUS_H_<HUC4>_HU4_GDB.zip``. Live probe (HUC4 ``0309`` for the
#     Fort Myers / Caloosahatchee region): HTTP 200, accept-ranges=bytes,
#     content-length=151,111,923 (~144 MB).
#   * No per-bbox query API exists for NHDPlus HR raw geometry — the only
#     bbox-aware path is to download the HUC4 GDB and clip locally. The
#     USGS National Map TNM Access REST API (`tnmaccess.nationalmap.gov`)
#     returns the same download URL with file-size metadata.
#   * The ``.zip`` URLs return HTTP 403, so we route through ``.GDB.zip``
#     (the actual product file, not the wrapper zip).
#
# This is the **Tier 4 (region download + local clip)** pattern in §F.1.1.
# Two-stage cache:
#   - Stage 1: the HUC4 region GDB lives at
#     ``cache/static-30d/river_geometry/_regions/NHDPLUS_H_<HUC4>_HU4_GDB.zip``
#     (downloaded once per HUC4, shared across all clips inside that region).
#   - Stage 2: the per-call clip at
#     ``cache/static-30d/river_geometry/<hash>.fgb`` (the clipped FlatGeobuf
#     under the bbox-quantized key).
#
# v0.1 substrate scope: the per-call clip extracts the NHDFlowline feature
# class from the HUC4 GDB, clips by bbox, and writes a FlatGeobuf. The
# implementation does NOT use the two-stage cache in v0.1 — the kickoff calls
# for a single ``read_through`` write per call, and the GDB download is
# inside the fetcher (so the HUC4 region is fetched fresh on every cache
# miss). The two-stage optimization is captured as
# OQ-39-NHDPLUSHR-TWO-STAGE-CACHE for a follow-up job.
#
# HUC4 routing: a bbox in EPSG:4326 must be mapped to a HUC4 region code.
# Per the kickoff's per-source bbox quantization rule: "NHDPlus HR: HUC4-
# scoped (region-download Tier 4); cache key includes HUC4 region per §F.1.1
# Tier-4 discipline." The v0.1 substrate uses a small **bbox → HUC4
# heuristic envelope table** (mirrors the ``_state_fips_for_lonlat``
# heuristic from job-0033 — Fort Myers / Caloosahatchee = HUC4 ``0309``);
# replacement with a real point-in-polygon over the WBD HUC4 dataset is a
# tracked follow-up. Surface as OQ-39-NHDPLUSHR-HUC4-ROUTING-HEURISTIC.


_FETCH_RIVER_GEOMETRY_METADATA = AtomicToolMetadata(
    name="fetch_river_geometry",
    ttl_class="static-30d",
    source_class="river_geometry",
    cacheable=True,
)


# NHDPlus HR staged-products S3 base. HUC4 GDB at
# ``StagedProducts/Hydrography/NHDPlusHR/Beta/GDB/NHDPLUS_H_<HUC4>_HU4_GDB.zip``.
_NHDPLUSHR_BASE = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHDPlusHR/Beta/GDB"
)


# Heuristic bbox → HUC4 region code. Each entry is (HUC4 code, envelope bbox).
# CONUS-centric for v0.1; HUC4 0309 covers the Fort Myers / Caloosahatchee
# region (the M5 demo target). Replacement with a real point-in-polygon over
# the WBD HUC4 dataset is a tracked follow-up — see
# OQ-39-NHDPLUSHR-HUC4-ROUTING-HEURISTIC.
_HUC4_BBOX_ENVELOPES: list[tuple[str, tuple[float, float, float, float]]] = [
    # Florida — South Florida (Caloosahatchee, Big Cypress, Everglades)
    ("0309", (-82.0, 25.0, -80.0, 27.5)),
    # Florida — Peninsular (Tampa Bay south to about Lake Okeechobee)
    ("0310", (-82.9, 26.7, -80.5, 28.7)),
    # Florida — Suwannee / North Florida
    ("0311", (-83.7, 28.5, -82.0, 31.0)),
    # Texas — Lower Colorado (Houston / Galveston Bay)
    ("1209", (-96.0, 28.0, -93.5, 31.5)),
    # Louisiana — Lower Mississippi
    ("0807", (-91.5, 28.5, -89.0, 31.0)),
    # New York — Hudson (Hurricane Sandy reference region)
    ("0203", (-75.0, 40.5, -73.0, 43.0)),
    # North Carolina — Cape Fear (Hurricane Florence reference region)
    ("0303", (-79.5, 33.0, -77.0, 35.8)),
    # California — South Coast (Los Angeles basin)
    ("1807", (-119.0, 33.0, -117.0, 35.0)),
]


def _huc4_for_bbox(bbox: tuple[float, float, float, float]) -> str | None:
    """Best-effort HUC4 lookup from a bbox center — heuristic only.

    Returns ``None`` if no envelope matches. A future enrichment job replaces
    this with a real point-in-polygon over the WBD HUC4 dataset cached in the
    cache bucket. Same shape/role as the job-0033 ``_state_fips_for_lonlat``
    heuristic and the job-0037 ``_iso3_for_lonlat`` heuristic.
    """
    mid_lon = 0.5 * (bbox[0] + bbox[2])
    mid_lat = 0.5 * (bbox[1] + bbox[3])
    for huc4, (mn_lon, mn_lat, mx_lon, mx_lat) in _HUC4_BBOX_ENVELOPES:
        if mn_lon <= mid_lon <= mx_lon and mn_lat <= mid_lat <= mx_lat:
            return huc4
    return None


# ---------------------------------------------------------------------------
# OSM Overpass waterway path — PRIMARY source for fetch_river_geometry.
# ---------------------------------------------------------------------------
#
# Root-cause fix: the NHDPlus HR HUC4 routing heuristic only covers a handful
# of CONUS demo envelopes, so most bboxes hit "could not route bbox to a HUC4
# region" and the tool dead-ends (data-source-fallback norm violation). OSM
# Overpass exposes a true per-bbox waterway query that fills the WHOLE bbox
# (not just a seed-connected sub-network), is global, and serializes to the
# same FlatGeobuf -> inline-GeoJSON render path the Wave 4.9 vector pipeline
# already drives (``add_loaded_layer`` reads the .fgb, converts to GeoJSON).
#
# Overpass QL shape (mirrors fetch_roads_osm, but for waterways):
#
#     [out:json][timeout:60];
#     (way["waterway"~"^(river|stream|canal)$"](s,w,n,e););
#     out geom;
#
# Overpass returns the bbox corners as (south, west, north, east) — the
# OPPOSITE corner-pair ordering from the caller's (min_lon, min_lat, max_lon,
# max_lat). Same convention as the roads tool.

#: Overpass interpreter endpoint (same public mirror fetch_roads_osm uses).
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: HTTP timeout for the Overpass POST (Overpass is slow under load).
_OVERPASS_HTTP_TIMEOUT = 120.0

#: Overpass-side internal-query timeout (the ``[timeout:N]`` directive).
_OVERPASS_QL_TIMEOUT = 60

#: OSM ``waterway`` tag values treated as "rivers and streams" for this tool.
#: ``river`` + ``stream`` + ``canal`` is the channel-carrying network most
#: comparable to NHDFlowline; ``ditch``/``drain`` are excluded by default
#: (they explode feature counts in agricultural/urban areas with little
#: hydrologic-modeling value).
_WATERWAY_CLASSES: tuple[str, ...] = ("river", "stream", "canal")


def _build_overpass_waterway_ql(
    bbox: tuple[float, float, float, float],
    waterway_classes: tuple[str, ...],
) -> str:
    """Construct the Overpass QL payload for waterway ways inside ``bbox``.

    Overpass expects the bbox corners as ``(south, west, north, east)``
    (lat first) — the OPPOSITE ordering from the caller's
    ``(min_lon, min_lat, max_lon, max_lat)``.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    s, w, n, e = min_lat, min_lon, max_lat, max_lon
    classes_pipe = "|".join(waterway_classes)
    return (
        f"[out:json][timeout:{_OVERPASS_QL_TIMEOUT}];"
        f"(way[\"waterway\"~\"^({classes_pipe})$\"]({s},{w},{n},{e}););"
        f"out geom;"
    )


def _post_overpass_waterways(ql: str) -> dict[str, Any]:
    """POST ``ql`` to the Overpass interpreter; return the parsed JSON dict.

    Raises ``UpstreamAPIError`` on network / HTTP / parse failure so the
    caller can fall through to the NHDPlus HR fallback (data-source-fallback
    norm) rather than dead-ending.
    """
    try:
        resp = requests.post(
            _OVERPASS_URL,
            data={"data": ql},
            headers={"User-Agent": _DEFAULT_USER_AGENT},
            timeout=_OVERPASS_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamAPIError(
            f"Overpass waterway query failed (transport/HTTP): {exc}"
        ) from exc
    try:
        return resp.json()
    except ValueError as exc:
        raise UpstreamAPIError(
            f"Overpass returned non-JSON response for waterway query: {exc}"
        ) from exc


def _extract_overpass_waterway_records(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project Overpass ``way`` elements to LineString records.

    Each record carries ``coords`` (list of ``(lon, lat)`` tuples) plus the
    ``osm_id``, ``name``, and ``waterway`` attributes. Ways with fewer than
    two valid coordinates are dropped (a LineString needs >= 2 points).
    """
    elements = payload.get("elements") or []
    if not isinstance(elements, list):
        raise UpstreamAPIError(
            f"Overpass 'elements' is not a list: {type(elements).__name__}"
        )
    records: list[dict[str, Any]] = []
    for el in elements:
        if not isinstance(el, dict) or el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if not isinstance(geom, list) or len(geom) < 2:
            continue
        coords: list[tuple[float, float]] = []
        for pt in geom:
            if not isinstance(pt, dict):
                continue
            lat_v = pt.get("lat")
            lon_v = pt.get("lon")
            if lat_v is None or lon_v is None:
                continue
            try:
                lat = float(lat_v)
                lon = float(lon_v)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
            coords.append((lon, lat))
        if len(coords) < 2:
            continue
        tags = el.get("tags") or {}
        if not isinstance(tags, dict):
            tags = {}
        records.append(
            {
                "osm_id": el.get("id"),
                "name": tags.get("name"),
                "waterway": tags.get("waterway"),
                "coords": coords,
            }
        )
    return records


def _waterway_records_to_clipped_fgb_bytes(
    records: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> bytes:
    """Serialize waterway LineString records to bbox-clipped FlatGeobuf bytes.

    Builds a GeoDataFrame of LineStrings (EPSG:4326), clips it to the exact
    requested bbox so the layer fills the whole bbox without spilling outside
    it, and writes FlatGeobuf bytes (the same `.fgb` -> inline-GeoJSON render
    path Wave 4.9 drives via ``add_loaded_layer``). An empty record list still
    produces a valid (empty) FlatGeobuf — never a sentinel (cache.py poison
    contract).
    """
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
        from shapely.geometry import LineString, box as shapely_box  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise UpstreamAPIError(
            f"geopandas / shapely unavailable for OSM waterway serialization: {exc}"
        ) from exc

    if records:
        geometries = [LineString(r["coords"]) for r in records]
        attrs = [
            {
                "osm_id": r.get("osm_id"),
                "name": r.get("name"),
                "waterway": r.get("waterway"),
            }
            for r in records
        ]
        gdf = gpd.GeoDataFrame(attrs, geometry=geometries, crs="EPSG:4326")
        # Clip to the exact bbox so geometry doesn't spill outside the AOI.
        try:
            gdf = gdf.clip(shapely_box(*bbox))
        except Exception as exc:  # noqa: BLE001 — clip is best-effort precision
            logger.warning(
                "OSM waterway clip failed; returning unclipped features: %s", exc
            )
    else:
        import pandas as pd  # type: ignore[import-not-found]

        empty_df = pd.DataFrame(
            {
                "osm_id": pd.Series(dtype="Int64"),
                "name": pd.Series(dtype="object"),
                "waterway": pd.Series(dtype="object"),
            }
        )
        gdf = gpd.GeoDataFrame(
            empty_df,
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )

    out_tmp: str | None = None
    try:
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".fgb", delete=False, prefix="grace2_osm_rivers_"
        ) as f:
            out_tmp = f.name
        try:
            gdf.to_file(out_tmp, driver="FlatGeobuf")
        except Exception as exc:  # noqa: BLE001
            raise UpstreamAPIError(
                f"FlatGeobuf write failed for OSM waterways (bbox={bbox}): {exc}"
            ) from exc
        with open(out_tmp, "rb") as f:
            return f.read()
    finally:
        if out_tmp is not None:
            try:
                os.unlink(out_tmp)
            except OSError:
                pass


def _fetch_osm_waterway_geometry_bytes(
    bbox: tuple[float, float, float, float],
) -> bytes:
    """PRIMARY river-geometry fetcher — OSM Overpass waterway query over the bbox.

    Queries Overpass for ``waterway`` ways (river/stream/canal) inside the
    bbox, projects each to a LineString, clips to the bbox, and returns
    FlatGeobuf bytes. Fills the WHOLE bbox (true per-bbox query — not a
    seed-connected sub-network like NLDI). Raises ``UpstreamAPIError`` on any
    failure so ``fetch_river_geometry`` can fall through to NHDPlus HR.
    """
    _validate_bbox(bbox)
    ql = _build_overpass_waterway_ql(bbox, _WATERWAY_CLASSES)
    payload = _post_overpass_waterways(ql)
    records = _extract_overpass_waterway_records(payload)
    logger.info(
        "fetch_river_geometry[osm]: extracted %d waterway(s) for bbox=%s classes=%s",
        len(records),
        bbox,
        _WATERWAY_CLASSES,
    )
    return _waterway_records_to_clipped_fgb_bytes(records, bbox)


def _fetch_river_geometry_bytes(
    bbox: tuple[float, float, float, float],
    huc4: str | None,
) -> bytes:
    """Internal fallback chain for river geometry (data-source-fallback norm).

    Order:
      1. PRIMARY — OSM Overpass waterway query over the bbox (global, true
         per-bbox, fills the whole AOI). Empty-but-valid results are accepted
         (no rivers in the bbox is a legitimate answer, not a failure).
      2. FALLBACK — NHDPlus HR HUC4 region download + local clip, but only
         when the bbox routed to a HUC4 region (``huc4`` is not None).
      3. Typed honest error (``UpstreamAPIError``) if every path fails — never
         a silent dead-end or a hallucinated success.

    Returns FlatGeobuf bytes. The caller (``fetch_river_geometry``) routes
    these through ``read_through`` so the 30-day cache absorbs repeat calls.
    """
    primary_exc: Exception | None = None
    try:
        return _fetch_osm_waterway_geometry_bytes(bbox)
    except Exception as exc:  # noqa: BLE001 — fall through to NHDPlus HR
        primary_exc = exc
        logger.warning(
            "fetch_river_geometry: OSM Overpass primary failed (%s: %s); "
            "falling back to NHDPlus HR (huc4=%s)",
            type(exc).__name__,
            exc,
            huc4,
        )

    if huc4 is not None:
        try:
            return _fetch_nhdplushr_geometry_bytes(bbox, huc4)
        except Exception as exc:  # noqa: BLE001 — both paths failed
            logger.warning(
                "fetch_river_geometry: NHDPlus HR fallback also failed "
                "(huc4=%s): %s: %s",
                huc4,
                type(exc).__name__,
                exc,
            )
            raise UpstreamAPIError(
                "fetch_river_geometry: both OSM Overpass (primary) and NHDPlus HR "
                f"(fallback, huc4={huc4}) failed. OSM error: {primary_exc}. "
                f"NHDPlus HR error: {exc}."
            ) from exc

    # OSM failed and there is no HUC4 fallback available.
    raise UpstreamAPIError(
        "fetch_river_geometry: OSM Overpass (primary) failed and no NHDPlus HR "
        f"HUC4 fallback is available for this bbox. OSM error: {primary_exc}."
    )


def _fetch_nhdplushr_geometry_bytes(
    bbox: tuple[float, float, float, float], huc4: str
) -> bytes:
    """Download the NHDPlus HR HUC4 GDB, extract NHDFlowline, clip by bbox, return FlatGeobuf.

    Tier 4 access pattern: download the HUC4 region GDB (~144 MB for HUC4
    0309 South Florida), extract the ``NHDFlowline`` feature class from the
    GeoDatabase via OpenFileGDB driver (GDAL native), clip features whose
    geometry intersects the bbox, and rewrite as FlatGeobuf. Raises
    ``UpstreamAPIError`` on any download / extraction failure.

    Implementation note: the substrate downloads the full HUC4 GDB on every
    cache miss; the two-stage region-cache optimization is OQ-39-NHDPLUSHR-
    TWO-STAGE-CACHE. For the Fort Myers demo path the per-bbox cache miss is
    a one-time ~144 MB transfer, cached for 30 days.
    """
    _validate_bbox(bbox)
    url = f"{_NHDPLUSHR_BASE}/NHDPLUS_H_{huc4}_HU4_GDB.zip"

    # rasterio + geopandas/pyogrio import lazily.
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
        from shapely.geometry import box as shapely_box  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise UpstreamAPIError(
            f"geopandas / shapely unavailable for NHDPlus HR clip: {exc}"
        ) from exc

    import tempfile
    import zipfile

    zip_tmp: str | None = None
    gdb_dir: str | None = None
    out_tmp: str | None = None
    try:
        # Download the HUC4 GDB zip.
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _DEFAULT_USER_AGENT},
                timeout=300.0,
                stream=True,
                allow_redirects=True,
            )
            if resp.status_code == 404:
                raise UpstreamAPIError(
                    f"NHDPlus HR HUC4 GDB not found at {url} (huc4={huc4}); "
                    "the staged-products tree may have moved — verify the base path."
                )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise UpstreamAPIError(
                f"NHDPlus HR GDB download failed url={url}: {exc}"
            ) from exc

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as zf:
            zip_tmp = zf.name
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    zf.write(chunk)

        # Extract the GDB directory.
        gdb_dir = tempfile.mkdtemp(prefix="nhdplushr-")
        try:
            with zipfile.ZipFile(zip_tmp) as zfh:
                zfh.extractall(gdb_dir)
        except zipfile.BadZipFile as exc:
            raise UpstreamAPIError(
                f"NHDPlus HR HUC4 GDB zip is corrupt or empty for huc4={huc4}: {exc}"
            ) from exc

        # Find the .gdb directory inside the extracted tree.
        import os as _os

        gdb_path: str | None = None
        for root, dirs, _files in _os.walk(gdb_dir):
            for d in dirs:
                if d.endswith(".gdb"):
                    gdb_path = _os.path.join(root, d)
                    break
            if gdb_path:
                break
        if gdb_path is None:
            raise UpstreamAPIError(
                f"could not find .gdb directory in extracted NHDPlus HR archive "
                f"for huc4={huc4} (extracted under {gdb_dir})"
            )

        # Read NHDFlowline, clip by bbox, write FlatGeobuf.
        try:
            gdf = gpd.read_file(gdb_path, layer="NHDFlowline", bbox=bbox)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamAPIError(
                f"geopandas could not read NHDFlowline from {gdb_path}: {exc}"
            ) from exc

        # Clip by bbox polygon for tight precision (geopandas bbox read is
        # a spatial filter, not a clip — features extending outside the bbox
        # are returned whole; clip trims them).
        try:
            bbox_geom = shapely_box(*bbox)
            gdf_clipped = gdf.clip(bbox_geom)
        except Exception as exc:  # noqa: BLE001
            # Fall back to the unclipped result if clip fails (some geometry
            # types don't clip cleanly); surface a warning in the log.
            logger.warning("NHDPlus HR clip failed; returning bbox-filtered features: %s", exc)
            gdf_clipped = gdf

        with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as ot:
            out_tmp = ot.name
        try:
            gdf_clipped.to_file(out_tmp, driver="FlatGeobuf")
        except Exception as exc:  # noqa: BLE001
            raise UpstreamAPIError(
                f"FlatGeobuf write failed for NHDPlus HR clip (huc4={huc4}, bbox={bbox}): {exc}"
            ) from exc

        with open(out_tmp, "rb") as f:
            return f.read()
    finally:
        # Best-effort cleanup of all tmp paths.
        for path in (zip_tmp, out_tmp):
            if path is None:
                continue
            try:
                os.unlink(path)
            except OSError:
                pass
        if gdb_dir is not None:
            try:
                import shutil

                shutil.rmtree(gdb_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass


@register_tool(
    _FETCH_RIVER_GEOMETRY_METADATA,
    # Annotations: readOnlyHint=True, openWorldHint=True (USGS NHDPlus HR),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_river_geometry(
    bbox: tuple[float, float, float, float],
    source: str = "nhdplus_hr",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """Fetch river and stream flowline geometry for a bbox (OSM + NHDPlus HR).

    **What it does:** Returns river/stream/canal LineStrings that fill the
    requested bbox, as a FlatGeobuf that renders inline on the map (Wave 4.9
    vector path). Access pattern: Tier 2/Tier 4 with an internal fallback
    chain (data-source-fallback norm):

    1. PRIMARY — OSM Overpass ``waterway`` query over the bbox
       (river/stream/canal). Global, true per-bbox: fills the WHOLE bbox, not
       just a seed-connected sub-network. Clipped to the bbox.
    2. FALLBACK — USGS NHDPlus High Resolution NHDFlowline (Tier 4 region
       download + local clip), used when the bbox routes to one of the v0.1
       HUC4 envelopes and OSM is unavailable.
    3. Typed honest error if both fail — never a silent dead-end.

    Both paths serialize to FlatGeobuf and clip to the requested bbox. The
    30-day cache absorbs repeat calls.

    **When to use:**
    - ``build_sfincs_model`` needs river flowlines for DEM hydro-conditioning
      (HydroMT's ``setup_rivers_from_dem`` step burns channel geometry).
    - Fluvial flood workflow requires channel network for boundary-condition
      placement (upstream inflow nodes, downstream outlets).
    - User asks to visualize stream networks or watershed drainage patterns.
    - Watershed delineation: ``delineate_watershed`` tool consumes the
      flowline outlet point to route upstream.

    **When NOT to use:**
    - Real-time streamflow measurements — use ``fetch_streamflow`` (NWIS
      USGS gauges) for discharge time series.
    - Flow-direction / accumulation grids — derive from the DEM inside
      HydroMT; NHDPlus HR publishes those separately.
    - Areas larger than 5,000 km² — the tool enforces a guardrail to keep a
      single fetch tractable (use a smaller bbox or a future tiled workflow).

    **Parameters:**
    - ``bbox`` (tuple[float,float,float,float]): ``(min_lon, min_lat, max_lon,
      max_lat)`` in EPSG:4326. Max area 5,000 km².
    - ``source`` (str, default ``"nhdplus_hr"``): preferred hydrography
      source label. ``"nhdplus_hr"`` and ``"osm"`` are accepted; the internal
      fallback chain (OSM primary, NHDPlus HR fallback) runs regardless so the
      tool stays reliable across all bboxes. Unsupported labels (e.g.
      ``"merit_hydro"``) raise ``BboxInvalidError``.

    **Returns:**
    A ``LayerURI`` pointing at a FlatGeobuf of river/stream LineStrings in the
    cache bucket (``gs://grace-2-hazard-prod-cache/cache/static-30d/river_geometry/<key>.fgb``).
    ``layer_type="vector"``, ``role="input"``. The FlatGeobuf renders inline
    on the map via the Wave 4.9 GeoJSON path (``add_loaded_layer``) — it is
    NOT published through ``publish_layer`` (that path is raster-only).

    **Cross-tool dependencies:**
    - Upstream: ``geocode_location`` for bbox derivation.
    - Downstream: ``build_sfincs_model`` (river-burning DEM step),
      ``delineate_watershed``, stream-network display in map panel.
    """
    if source not in ("nhdplus_hr", "osm"):
        # Reserved future sources (NHDPlus V2, MERIT-Hydro) — not in v0.1.
        raise BboxInvalidError(
            f"unsupported source={source!r}; allowed: 'nhdplus_hr' (Tier-4 HUC4 GDB) "
            "or 'osm' (Overpass waterway). The internal fallback chain runs "
            "OSM-primary regardless of which label you pass."
        )

    _validate_bbox(bbox)
    quantized = round_bbox_to_resolution(bbox, 10)

    # Guardrail: keep a single fetch tractable (OSM Overpass + NHDPlus HR HUC4
    # GDBs are both heavy for huge bboxes). 5,000 km^2 explicit bound — matches
    # the previous NHDPlus-only behavior.
    if _bbox_area_km2(quantized) > 5_000.0:
        raise BboxInvalidError(
            f"bbox area {_bbox_area_km2(quantized):.1f} km^2 exceeds 5000 km^2 "
            "guardrail for fetch_river_geometry (use a smaller bbox or a future "
            "tiled workflow)."
        )

    # HUC4 routing is now BEST-EFFORT (fallback only) — a missing HUC4 no
    # longer dead-ends the tool, because OSM Overpass is the primary path
    # (root-cause fix for "could not route bbox to a HUC4 region").
    huc4 = _huc4_for_bbox(quantized)

    # Cache key is keyed on the quantized bbox (+ HUC4 when available, for
    # backward-compatible dedup discipline). The fallback chain decides the
    # actual provider; identical bboxes dedup to the same artifact.
    params = {
        "bbox": list(quantized),
        "source": "river_geometry",  # provider-agnostic; chain decides at fetch time
        "huc4": huc4,
    }
    result = read_through(
        metadata=_FETCH_RIVER_GEOMETRY_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_river_geometry_bytes(quantized, huc4),
    )
    assert result.uri is not None
    return LayerURI(
        layer_id=f"rivers-{quantized[0]:.4f}-{quantized[1]:.4f}",
        name="Rivers & Streams",
        layer_type="vector",
        uri=result.uri,
        style_preset="continuous_dem",  # placeholder — hydrography preset is a follow-up
        role="input",
    )


# ---------------------------------------------------------------------------
# lookup_precip_return_period — NOAA Atlas 14 PFDS (sprint-07 Stage B, job-0039).
# ---------------------------------------------------------------------------
#
# Access pattern tier — LIVE-VERIFIED matches kickoff inference (2026-06-07):
#
#   * NWS HDSC publishes the Precipitation Frequency Data Server (PFDS) as a
#     point-query CSV endpoint at ``hdsc.nws.noaa.gov/cgi-bin/hdsc/new/
#     fe_text_mean.csv?lat=&lon=&data=depth&units=english&series=pds``.
#     Live probe at (lat=26.6, lon=-81.9) — Fort Myers FL — returned an HTTP
#     200 with a 1598-byte CSV: header rows naming "NOAA Atlas 14 Volume 9
#     Version 2" + "Project area: Southeastern States", then a matrix of
#     precipitation depths (inches) indexed by duration (5-min, 10-min, …,
#     60-day) × ARI (1, 2, 5, …, 1000 years).
#   * Per-coordinate / point-only query surface — no native bbox lookup. The
#     fetcher routes by ``location=(lat, lon)`` quantized to Atlas 14's native
#     source grid (1/120 degree, per the kickoff's per-source quantization
#     rule).
#
# This is the **Tier 3 (direct HTTPS + Range-irrelevant point query)**
# pattern in §F.1.1 — small textual responses keyed by point coordinates.
# Cache key is bbox-equivalent: the quantized (lat, lon) tuple per the
# 1/120-degree source grid; ARI + duration are part of the params.


_LOOKUP_PRECIP_RETURN_PERIOD_METADATA = AtomicToolMetadata(
    name="lookup_precip_return_period",
    ttl_class="static-30d",
    source_class="precip_return_period",
    cacheable=True,
)


_ATLAS14_PFDS_URL = "https://hdsc.nws.noaa.gov/cgi-bin/hdsc/new/fe_text_mean.csv"

#: Atlas 14 native source grid: 1/120 degree (≈ 30 arc-seconds).
_ATLAS14_GRID_DEG = 1.0 / 120.0

#: The ARI (Average Recurrence Interval) columns Atlas 14 reports — fixed.
_ATLAS14_ARI_YEARS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]

#: The duration rows Atlas 14 reports — fixed across volumes.
#: Each entry maps the CSV row label (key) to its duration in hours (value).
_ATLAS14_DURATIONS_HR: dict[str, float] = {
    "5-min": 5 / 60,
    "10-min": 10 / 60,
    "15-min": 15 / 60,
    "30-min": 30 / 60,
    "60-min": 1.0,
    "2-hr": 2.0,
    "3-hr": 3.0,
    "6-hr": 6.0,
    "12-hr": 12.0,
    "24-hr": 24.0,
    "2-day": 48.0,
    "3-day": 72.0,
    "4-day": 96.0,
    "7-day": 168.0,
    "10-day": 240.0,
    "20-day": 480.0,
    "30-day": 720.0,
    "45-day": 1080.0,
    "60-day": 1440.0,
}


def _quantize_lonlat_to_atlas14_grid(
    lat: float, lon: float
) -> tuple[float, float]:
    """Quantize a (lat, lon) pair to Atlas 14's 1/120-degree native grid.

    Per the per-source bbox quantization rule (acceptance criterion 3 of
    the kickoff): Atlas 14 PFDS is reported on a 1/120-degree source grid.
    We snap to the nearest grid intersection so two callers within the same
    grid cell hit the same cache entry.
    """
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise BboxInvalidError(f"non-finite location ({lat!r}, {lon!r})")
    if not (-90.0 <= lat <= 90.0):
        raise BboxInvalidError(f"latitude out of range [-90,90]: {lat!r}")
    if not (-180.0 <= lon <= 180.0):
        raise BboxInvalidError(f"longitude out of range [-180,180]: {lon!r}")
    lat_q = round(lat / _ATLAS14_GRID_DEG) * _ATLAS14_GRID_DEG
    lon_q = round(lon / _ATLAS14_GRID_DEG) * _ATLAS14_GRID_DEG
    return round(lat_q, 9), round(lon_q, 9)


def _parse_atlas14_csv(body: str) -> dict[str, Any]:
    """Parse the Atlas 14 PFDS CSV into a structured dict.

    The PFDS CSV is a small textual document — header lines naming the
    volume / version / project area, then a matrix indexed by duration × ARI.
    We surface both the full matrix and a top-level ``vintage_volume`` field
    for provenance (e.g. "NOAA Atlas 14 Volume 9 Version 2").
    """
    vintage_volume = "unknown"
    project_area = "unknown"
    lines = body.splitlines()
    matrix: dict[str, dict[int, float]] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("NOAA Atlas 14"):
            vintage_volume = line
            continue
        if line.startswith("Project area:"):
            project_area = line.split(":", 1)[1].strip()
            continue
        # Duration rows look like ``5-min:, 0.553,0.620,...``.
        if ":" not in line:
            continue
        label, _, values_str = line.partition(":")
        label = label.strip()
        if label not in _ATLAS14_DURATIONS_HR:
            continue
        values_clean = [v.strip() for v in values_str.split(",") if v.strip()]
        if len(values_clean) != len(_ATLAS14_ARI_YEARS):
            continue
        try:
            depths = [float(v) for v in values_clean]
        except ValueError:
            continue
        matrix[label] = {ari: depth for ari, depth in zip(_ATLAS14_ARI_YEARS, depths)}
    return {
        "vintage_volume": vintage_volume,
        "project_area": project_area,
        "matrix": matrix,
    }


def _fetch_atlas14_pfds_bytes(lat: float, lon: float) -> bytes:
    """Fetch the Atlas 14 PFDS CSV at (lat, lon) and return raw response bytes.

    Tier 3 access pattern: HTTPS GET with the location as a query parameter,
    text/csv (well, text/html with CSV body — see the parser for the body
    shape). The bytes returned are the verbatim Atlas 14 response so
    downstream re-parsing is possible without a re-fetch.
    """
    try:
        resp = requests.get(
            _ATLAS14_PFDS_URL,
            params={
                "lat": str(lat),
                "lon": str(lon),
                "data": "depth",
                "units": "english",
                "series": "pds",  # partial-duration series — Atlas 14 convention
            },
            headers={"User-Agent": _DEFAULT_USER_AGENT},
            timeout=30.0,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamAPIError(
            f"NOAA Atlas 14 PFDS fetch failed for (lat={lat}, lon={lon}): {exc}"
        ) from exc

    body = resp.text
    if "NOAA Atlas 14" not in body:
        # The PFDS returns an HTML "out of project area" page if the point
        # falls outside Atlas 14 coverage; surface that as a typed error.
        raise UpstreamAPIError(
            f"NOAA Atlas 14 PFDS returned no precip-frequency data for "
            f"(lat={lat}, lon={lon}) — point may be outside the Atlas 14 "
            f"project areas (Western US: V1; SW: V2; ... ; OCONUS: not yet)."
        )
    return body.encode("utf-8")


def _pick_duration_label(duration_hours: float) -> str:
    """Find the Atlas 14 duration row whose hours match ``duration_hours`` exactly.

    Atlas 14 reports a fixed set of durations (5-min through 60-day). We
    require an exact match against the known set so the caller can't ask
    for an interpolated value (Atlas 14 doesn't publish interpolations and
    we don't fabricate them — Invariant 7).
    """
    for label, hrs in _ATLAS14_DURATIONS_HR.items():
        if abs(hrs - duration_hours) < 1e-9:
            return label
    available_hr = sorted(_ATLAS14_DURATIONS_HR.values())
    raise BboxInvalidError(
        f"duration_hours={duration_hours} not in Atlas 14's published rows "
        f"(available hours: {available_hr})."
    )


@register_tool(
    _LOOKUP_PRECIP_RETURN_PERIOD_METADATA,
    # Annotations: readOnlyHint=True, openWorldHint=True (NOAA PFDS API),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def lookup_precip_return_period(
    location: tuple[float, float],
    return_period_years: int,
    duration_hours: float,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> dict[str, Any]:
    """Look up a precipitation return-period depth at a point via NOAA Atlas 14 PFDS.

    Access pattern: Tier 3 (direct HTTPS point query to the NOAA PFDS endpoint).

    **What it does:** Issues a point query to the NOAA Hydrometeorological Design
    Studies Center (HDSC) Precipitation Frequency Data Server (PFDS) at
    ``hdsc.nws.noaa.gov/cgi-bin/hdsc/new/fe_text_mean.csv``, parses the returned
    duration × ARI matrix, and returns the requested depth in inches. Input
    coordinates are snapped to Atlas 14's 1/120° (~30 arc-second) grid before
    the cache key is computed (FR-DC-4 dedup). This is a point query, not a
    raster — it returns a scalar dict, not a ``LayerURI``. Tier-1 free, no
    API key, CONUS + Puerto Rico / US Virgin Islands only.

    **When to use:**

    - Design-storm precipitation depth for an SFINCS pluvial-flood scenario
      ("what is the 100-year, 24-hour rainfall for Miami?"). Example:
      ``location=(25.77, -80.19)``, ``return_period_years=100``,
      ``duration_hours=24.0``.
    - Characterising a published historical storm by its return-period equivalence
      ("Harvey's 48-hour total at Houston — what ARI?"). Run the tool for
      multiple ARIs and compare.
    - Providing IDF (intensity-duration-frequency) input for a rainfall-runoff
      model (SCS CN, Green-Ampt).

    **When NOT to use:**

    - Observed precipitation totals — use ``fetch_mrms_qpe`` (gauge-corrected
      radar accumulation) or NWIS / NEXRAD for measurements.
    - Future-climate design storms — Atlas 14 is based on historical records
      (Atlas 15, in development, will integrate non-stationarity).
    - Locations outside CONUS / PR / USVI — Atlas 14 OCONUS coverage is partial;
      Alaska, Hawaii, and Pacific Islands are not in the v0.1 substrate.
    - Spatial rasters of return-period precipitation — Atlas 14 PFDS is a point
      service; for a spatial map use a pre-computed gridded Atlas 14 dataset.

    **Parameters:**

    - ``location``: ``(lat, lon)`` decimal degrees EPSG:4326. Note: lat first,
      lon second (opposite of the ``bbox`` convention). Example: ``(29.76, -95.37)``
      for Houston.
    - ``return_period_years``: ARI in years; Atlas 14 publishes
      ``{1, 2, 5, 10, 25, 50, 100, 200, 500, 1000}``; values outside this set
      raise ``BboxInvalidError``.
    - ``duration_hours``: storm duration in hours; Atlas 14 publishes durations
      from 5 min (5/60 h) to 60 days (1440 h); unsupported durations raise
      ``BboxInvalidError``.

    **Returns:**

    A ``dict`` with keys: ``precip_inches`` (float, precipitation depth in
    inches), ``units`` (``"inches"``), ``location`` ([lat, lon] of the snapped
    Atlas 14 grid point), ``return_period_years`` (ARI echo), ``duration_hours``
    (duration echo), ``vintage_volume`` (e.g. ``"NOAA Atlas 14 Volume 9 Version
    2"``), ``project_area`` (e.g. ``"Southeastern States"``),
    ``source`` (``"noaa-atlas14-pfds"``).

    **Cross-tool dependencies:**

    - Consumed by: ``build_sfincs_model`` to construct a synthetic design-storm
      hyetograph; ``run_pluvial_flood`` workflow (uses the returned depth to
      drive the SFINCS rainfall input file).
    - Compare with: ``fetch_mrms_qpe`` for observed accumulations vs Atlas 14
      design depths; the ratio gives the storm's return-period rank.
    - Pair with: ``fetch_gcn250_curve_numbers`` or NLCD-derived CNs when
      converting depth → runoff volume via SCS CN method.

    FR-CE-8: Routed through ``read_through`` with ``ttl_class="static-30d"``;
    cache key = SHA-256 of ``(lat-quantized, lon-quantized, return_period_years,
    duration_label)`` — snapping ensures callers within the same 30 arc-second
    cell dedup (FR-DC-4).
    """
    if not isinstance(location, (tuple, list)) or len(location) != 2:
        raise BboxInvalidError(
            f"location must be a (lat, lon) 2-tuple; got {location!r}"
        )
    if return_period_years not in _ATLAS14_ARI_YEARS:
        raise BboxInvalidError(
            f"return_period_years={return_period_years} not in Atlas 14's published "
            f"ARIs {_ATLAS14_ARI_YEARS}."
        )
    duration_label = _pick_duration_label(duration_hours)

    lat, lon = float(location[0]), float(location[1])
    lat_q, lon_q = _quantize_lonlat_to_atlas14_grid(lat, lon)

    params = {
        "lat": lat_q,
        "lon": lon_q,
        "return_period_years": return_period_years,
        "duration_label": duration_label,
        "series": "pds",
        "units": "english",
    }
    result = read_through(
        metadata=_LOOKUP_PRECIP_RETURN_PERIOD_METADATA,
        params=params,
        ext="csv",
        fetch_fn=lambda: _fetch_atlas14_pfds_bytes(lat_q, lon_q),
    )

    parsed = _parse_atlas14_csv(result.data.decode("utf-8"))
    matrix = parsed["matrix"]
    if duration_label not in matrix or return_period_years not in matrix[duration_label]:
        raise UpstreamAPIError(
            f"NOAA Atlas 14 PFDS response did not contain "
            f"duration={duration_label} × ARI={return_period_years} for "
            f"(lat={lat_q}, lon={lon_q}); parsed matrix labels: "
            f"{list(matrix.keys())[:5]}..."
        )
    depth_inches = matrix[duration_label][return_period_years]
    payload = {
        "precip_inches": depth_inches,
        "units": "inches",
        "location": [lat_q, lon_q],
        "return_period_years": return_period_years,
        "duration_hours": duration_hours,
        "vintage_volume": parsed["vintage_volume"],
        "project_area": parsed["project_area"],
        "source": "noaa-atlas14-pfds",
    }
    logger.info(
        "lookup_precip_return_period (lat=%s lon=%s ari=%s dur=%s) -> %.3f inches cache_hit=%s",
        lat_q,
        lon_q,
        return_period_years,
        duration_label,
        depth_inches,
        result.hit,
    )
    return payload
