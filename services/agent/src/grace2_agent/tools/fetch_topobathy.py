"""``fetch_topobathy`` atomic tool — coastal merged topo-bathymetry DEM
(SFINCS North Star P1).

Produces a SINGLE seamless elevation surface that spans the shoreline — land
topography from USGS 3DEP plus sea-floor / nearshore bathymetry from NOAA NCEI
CUDEM — so a coastal SFINCS run has a continuous bed from the hills to the
deep water with NO gap at the waterline. The output is byte-format identical
to ``fetch_dem`` / ``fetch_3dep_extra`` (single-band float32 COG, LZW,
``style_preset="continuous_dem"``, ``units="meters"``, ``role="input"``) so
``build_sfincs_model``'s ``setup_dep`` consumes it UNCHANGED.

Why a dedicated tool (vs. ``fetch_dem``):

    3DEP is a LAND-surface DEM — it has no bathymetry; everything below the
    waterline is nodata. A coastal flood model fed a land-only DEM has no
    nearshore bed to route surge / run-up over. NOAA NCEI's CUDEM 1/9
    arc-second (~3 m) "Topobathy" product is the canonical merged
    topo-bathymetric DEM for the US coast: only the 1/9 arc-second tiles
    integrate BOTH bathymetric and topographic data, referenced to NAVD88
    (positive-up, bathymetry negative). This tool fetches the CUDEM tiles
    that intersect the AOI, fetches the 3DEP land DEM for the same AOI, and
    MERGES them onto a common UTM grid with CUDEM winning on the coast.

Vertical convention (HARD CONTRACT — Invariant 7):

    NAVD88 metres, POSITIVE-UP. Land is positive, bathymetry is NEGATIVE.
    NO SIGN FLIP is applied anywhere — that is exactly what HydroMT's
    ``setup_dep`` ``elevtn`` expects. The datum gate (``_assert_navd88``)
    refuses to merge a tile that is NOT NAVD88 (e.g. an MHW / MSL / LMSL
    product) unless a documented NAVD88 offset is supplied — never a silent
    cross-datum merge.

Data source (verified live 2026-06-18):

    NOAA NCEI CUDEM 1/9 arc-second "Topobathy 2014" collection, public S3
    bucket ``noaa-nos-coastal-lidar-pds`` (also mirrored at
    ``chs.coast.noaa.gov/htdata/raster2/elevation/``):

        s3://noaa-nos-coastal-lidar-pds/dem/NCEI_ninth_Topobathy_2014_8483/

    The authoritative per-tile URL manifest is ``urllist8483.txt`` at the
    collection root (one ``https://...tif`` per line). Tiles are named
    ``ncei19_n{LAT}X{FRAC}_w{LON}X{FRAC}_{YEAR}v1.tif`` where the n/w
    coordinates are the **NW (upper-left) corner** of a 0.25-degree tile
    (e.g. ``ncei19_n30X00_w085X25_2019v1.tif`` covers lat [29.75, 30.00],
    lon [-85.25, -85.00]). We download the manifest, parse each tile's NW
    corner from its filename, intersect those 0.25-degree footprints with
    the AOI bbox, and read the intersecting tiles over GDAL ``/vsicurl/``
    with ``AWS_NO_SIGN_REQUEST`` (mirrors how ``fetch_dem`` reads public
    3DEP S3). Tier-1 free, no auth.

FR-CE-8 / FR-DC-3: routed through ``read_through`` with
``ttl_class="static-30d"`` (CUDEM is a static archival product),
``source_class="topobathy"``. ``supports_global_query=False`` — CUDEM
covers the US coast only.
"""

from __future__ import annotations

import logging
import math
import os
import re
import tempfile
from typing import Any

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = [
    "fetch_topobathy",
    "TopobathyError",
    "TopobathyInputError",
    "TopobathyUpstreamError",
    "TopobathyEmptyError",
    "TopobathyDatumError",
    "TopobathyResult",
    "estimate_payload_mb",
    "CUDEM_COLLECTION_ROOT",
    "CUDEM_URLLIST_URL",
    "TARGET_CRS",
]

logger = logging.getLogger("grace2_agent.tools.fetch_topobathy")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class TopobathyError(RuntimeError):
    """Base class for fetch_topobathy failures.

    ``error_code`` maps to the WebSocket A.6 error frame emitted by the agent
    surface. ``retryable`` guides FR-AS-11 retry/clarify/fallback logic.
    """

    error_code: str = "TOPOBATHY_ERROR"
    retryable: bool = True


class TopobathyInputError(TopobathyError):
    """Bad inputs (bbox shape, out-of-range coordinates, bad datum offset)."""

    error_code = "TOPOBATHY_INPUT_INVALID"
    retryable = False


class TopobathyUpstreamError(TopobathyError):
    """CUDEM tile-index download / tile read / merge / COG materialization
    failure that is NOT a "no coverage" condition (network 5xx, GDAL read
    error, gdalwarp non-zero, etc.)."""

    error_code = "TOPOBATHY_UPSTREAM_ERROR"
    retryable = True


class TopobathyEmptyError(TopobathyError):
    """Neither CUDEM nor 3DEP produced any usable elevation for the AOI.

    This is the hard dead-end: no land DEM AND no bathy. The softer case —
    CUDEM missing but 3DEP land present — does NOT raise; it degrades to a
    land-only DEM and returns a ``TopobathyResult`` carrying an honest
    ``bathymetry_present=False`` warning (data-source fallback norm)."""

    error_code = "TOPOBATHY_EMPTY"
    retryable = False


class TopobathyDatumError(TopobathyError):
    """A CUDEM tile's vertical datum is NOT NAVD88 and no documented NAVD88
    offset was supplied (Invariant 7 — never silently merge mismatched
    datums)."""

    error_code = "TOPOBATHY_DATUM_MISMATCH"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

#: NOAA NCEI CUDEM 1/9 arc-second "Topobathy 2014" collection root (public
#: S3, anonymous read). The same objects are mirrored under
#: chs.coast.noaa.gov/htdata/raster2/elevation/ — the S3 host is faster +
#: range-request friendly for /vsicurl/.
CUDEM_COLLECTION_ROOT = (
    "https://noaa-nos-coastal-lidar-pds.s3.amazonaws.com/"
    "dem/NCEI_ninth_Topobathy_2014_8483/"
)

#: Authoritative per-tile URL manifest (one https://...tif per line). This is
#: the tile-index "footprint" we intersect with the AOI — the tile filenames
#: encode each tile's NW corner, so we don't need to crack open the shapefile
#: tile index (tileindex_NCEI_ninth_Topobathy_2014.zip) to do the spatial
#: intersect.
CUDEM_URLLIST_URL = CUDEM_COLLECTION_ROOT + "urllist8483.txt"

#: Each CUDEM tile is a 0.25-degree square; the filename encodes its NW corner.
_CUDEM_TILE_DEG = 0.25

#: Target output CRS — UTM 16N (covers the SFINCS North Star demo AOI, the
#: Florida panhandle / Mexico Beach). NAVD88 vertical is preserved (the merge
#: + reproject only touches the horizontal grid).
TARGET_CRS = "EPSG:32616"

#: US coastal envelope (incl. AK + HI + territories) — a coarse pre-screen so a
#: clearly-inland or foreign bbox fails fast with TopobathyInputError before we
#: download the manifest. The live manifest intersect is the authoritative
#: coverage check.
_US_COASTAL_BBOX: tuple[float, float, float, float] = (-180.0, 13.0, -64.0, 72.0)

#: Filename → (NW-lat, NW-lon) parser. Example: ``ncei19_n30X00_w085X25_2019v1``.
_TILE_NAME_RE = re.compile(
    r"ncei19_n(?P<lat_i>\d{2})X(?P<lat_f>\d{2})_w(?P<lon_i>\d{2,3})X(?P<lon_f>\d{2})",
    re.IGNORECASE,
)

#: 6-dp bbox quantization (~0.1 m) for cache-key stability.
_BBOX_DECIMALS = 6

#: Single shared style preset (same continuous-DEM ramp as fetch_dem).
_STYLE_PRESET = "continuous_dem"

#: GDAL no-sign-request env for anonymous public-S3 /vsicurl/ reads — mirrors
#: the scoped rasterio.Env in data_fetch.py:289.
_VSICURL_ENV_KW = dict(
    AWS_NO_SIGN_REQUEST="YES",
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff,.vrt",
    VSI_CACHE=True,
)


# ---------------------------------------------------------------------------
# AtomicToolMetadata.
# ---------------------------------------------------------------------------


_METADATA = AtomicToolMetadata(
    name="fetch_topobathy",
    ttl_class="static-30d",
    source_class="topobathy",
    cacheable=True,
    supports_global_query=False,
    payload_mb_estimator_name="estimate_payload_mb",
)


# ---------------------------------------------------------------------------
# Result wrapper — a LayerURI plus an honest bathymetry-present flag.
#
# The tool returns a ``LayerURI`` for byte-format compatibility with
# fetch_dem / fetch_3dep_extra (downstream build_sfincs_model treats the
# return as a LayerURI), BUT the data-source fallback norm requires the
# CUDEM-missing degrade to surface an honest warning. ``TopobathyResult``
# subclasses ``LayerURI`` so callers that only look at ``.uri`` /
# ``.style_preset`` (build_sfincs_model) are unaffected, while a
# coastal-aware caller can read ``.bathymetry_present`` /
# ``.fallback_warning`` to flag a no-bathy coastal run.
# ---------------------------------------------------------------------------


class TopobathyResult(LayerURI):
    """A merged-topobathy ``LayerURI`` carrying the bathymetry-present flag.

    Extra fields beyond ``LayerURI``:

    - ``bathymetry_present`` — True when at least one CUDEM tile contributed
      bathymetry to the merge; False when the tool degraded to 3DEP-land-only
      (CUDEM tiles missing / unreachable for the AOI).
    - ``fallback_warning`` — a human-readable honest warning when
      ``bathymetry_present`` is False (None otherwise). NEVER a fabricated
      success; a coastal run that consumes a no-bathy DEM is flagged.
    - ``cudem_tile_count`` — number of CUDEM tiles merged (0 on the land-only
      fallback path).
    """

    bathymetry_present: bool = True
    fallback_warning: str | None = None
    cudem_tile_count: int = 0


# ---------------------------------------------------------------------------
# Payload estimator (Wave 1.5 chat-warning gate).
# ---------------------------------------------------------------------------


def estimate_payload_mb(
    bbox: tuple[float, float, float, float] | None = None,
    **_kw: Any,
) -> float:
    """Estimate emitted COG size in MB.

    CUDEM 1/9 arc-second (~3 m) merged with 3DEP and re-tiled to a
    LZW-compressed COG runs ~400 MB / sq-deg of merged land+water (coastal
    AOIs are mostly water, which compresses well, so this is conservative).
    Scales linearly with bbox area; floored so the Wave-1.5 payload-warning
    gate never under-reports.
    """
    if bbox is None:
        return 50.0
    try:
        west, south, east, north = bbox
        sq_deg = max(0.0, (east - west)) * max(0.0, (north - south))
    except (TypeError, ValueError):
        return 50.0
    return max(0.5, sq_deg * 400.0)


# ---------------------------------------------------------------------------
# bbox helpers.
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    if len(bbox) != 4:
        raise TopobathyInputError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    min_lon, min_lat, max_lon, max_lat = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise TopobathyInputError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise TopobathyInputError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise TopobathyInputError(f"bbox lat out of [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise TopobathyInputError(
            f"bbox is degenerate (min must be < max on both axes): {bbox!r}"
        )
    west, south, east, north = _US_COASTAL_BBOX
    if max_lon < west or min_lon > east or max_lat < south or min_lat > north:
        raise TopobathyInputError(
            f"bbox {bbox} does not intersect the US coastal envelope "
            f"{_US_COASTAL_BBOX}; NOAA NCEI CUDEM is US-coast-only"
        )


def _round_bbox_to_6dp(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return tuple(round(v, _BBOX_DECIMALS) for v in bbox)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# CUDEM tile-index intersect.
# ---------------------------------------------------------------------------


def _parse_tile_nw_corner(url_or_name: str) -> tuple[float, float] | None:
    """Parse the NW (upper-left) corner (lat, lon) of a CUDEM tile from its
    filename. Returns ``None`` if the name does not match the expected scheme.

    ``ncei19_n30X00_w085X25_...`` → NW corner lat=+30.00, lon=-85.25.
    """
    m = _TILE_NAME_RE.search(url_or_name)
    if m is None:
        return None
    lat = float(m.group("lat_i")) + float(m.group("lat_f")) / 100.0
    lon = float(m.group("lon_i")) + float(m.group("lon_f")) / 100.0
    # 'n' = north (positive lat); 'w' = west (negative lon).
    return (lat, -lon)


def _tile_intersects_bbox(
    nw_lat: float,
    nw_lon: float,
    bbox: tuple[float, float, float, float],
) -> bool:
    """A 0.25-deg CUDEM tile (NW corner at nw_lat/nw_lon) intersects the AOI?

    The tile spans lat [nw_lat - 0.25, nw_lat] and lon [nw_lon, nw_lon + 0.25].
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    tile_south = nw_lat - _CUDEM_TILE_DEG
    tile_north = nw_lat
    tile_west = nw_lon
    tile_east = nw_lon + _CUDEM_TILE_DEG
    # Standard AABB overlap test.
    return not (
        tile_east < min_lon
        or tile_west > max_lon
        or tile_north < min_lat
        or tile_south > max_lat
    )


def _fetch_cudem_urllist(timeout_s: float) -> list[str]:
    """Download the CUDEM per-tile URL manifest (urllist8483.txt).

    Returns the list of tile URLs (one .tif per line). Raises
    ``TopobathyUpstreamError`` on a network / HTTP failure — that is a real
    upstream problem, distinct from "no tiles intersect the AOI" (which is an
    empty-intersection on a successfully-downloaded manifest).
    """
    import requests  # lazy — keep module import cheap

    try:
        resp = requests.get(CUDEM_URLLIST_URL, timeout=timeout_s)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise TopobathyUpstreamError(
            f"could not download CUDEM tile manifest {CUDEM_URLLIST_URL}: {exc}"
        ) from exc
    lines = [
        ln.strip()
        for ln in resp.text.splitlines()
        if ln.strip().lower().endswith(".tif")
    ]
    if not lines:
        raise TopobathyUpstreamError(
            f"CUDEM tile manifest {CUDEM_URLLIST_URL} parsed to zero .tif URLs "
            "(manifest format may have changed)"
        )
    return lines


def _select_cudem_tiles(
    bbox: tuple[float, float, float, float],
    timeout_s: float,
) -> list[str]:
    """Return the CUDEM tile URLs whose 0.25-deg footprint intersects the AOI.

    Empty list == no CUDEM coverage for the AOI (the manifest downloaded fine
    but nothing overlapped) — the caller degrades to 3DEP-land-only.
    """
    urls = _fetch_cudem_urllist(timeout_s)
    selected: list[str] = []
    for url in urls:
        corner = _parse_tile_nw_corner(url)
        if corner is None:
            continue
        nw_lat, nw_lon = corner
        if _tile_intersects_bbox(nw_lat, nw_lon, bbox):
            selected.append(url)
    logger.info(
        "fetch_topobathy: %d/%d CUDEM tiles intersect bbox=%s",
        len(selected), len(urls), bbox,
    )
    return selected


# ---------------------------------------------------------------------------
# Vertical-datum gate (Invariant 7).
# ---------------------------------------------------------------------------


def _assert_navd88(
    vsicurl_path: str,
    navd88_offset_m: float | None,
) -> float:
    """Datum gate for one CUDEM tile.

    Reads the tile's vertical-datum signal (CRS WKT vertical CS name + GDAL
    band/dataset metadata) and asserts it is NAVD88. If a non-NAVD88 datum
    (MHW / MSL / LMSL / tidal) is detected:

    - if ``navd88_offset_m`` is supplied (a DOCUMENTED tide-to-NAVD88 offset
      for this AOI), return it so the merge step can add it to the tile's
      elevations (bringing the tile onto NAVD88); else
    - raise ``TopobathyDatumError`` — NEVER silently merge mismatched datums.

    Returns the offset (metres) to ADD to the tile's elevations to bring them
    onto NAVD88 — 0.0 for a confirmed-NAVD88 tile.

    NOTE: the CUDEM 1/9 arc-second collection is NAVD88 by construction (the
    collection metadata XML states "The DEMs are referenced vertically to the
    North American Vertical Datum of 1988", verified live 2026-06-18). Per-tile
    inspection still runs so a future mixed collection / a relabelled tile is
    caught rather than silently merged.
    """
    datum_text = ""
    try:
        import rasterio

        with rasterio.Env(**_VSICURL_ENV_KW):
            with rasterio.open(vsicurl_path) as ds:
                # 1) vertical CS name from the CRS WKT (if a compound/vertical
                #    CRS is present).
                try:
                    crs = ds.crs
                    if crs is not None:
                        datum_text += " " + (crs.to_wkt() or "")
                except Exception:  # noqa: BLE001
                    pass
                # 2) dataset + band tags often carry an explicit vertical-datum
                #    string for CUDEM-family products.
                try:
                    for k, v in (ds.tags() or {}).items():
                        datum_text += f" {k}={v}"
                    for k, v in (ds.tags(1) or {}).items():
                        datum_text += f" {k}={v}"
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        # Could not read the header at all — that is an upstream read failure,
        # not a datum decision. Surface it so the caller can degrade / retry.
        raise TopobathyUpstreamError(
            f"could not read CUDEM tile header for datum check ({vsicurl_path}): {exc}"
        ) from exc

    return _classify_vertical_datum(datum_text, navd88_offset_m, vsicurl_path)


def _classify_vertical_datum(
    datum_text: str,
    navd88_offset_m: float | None,
    tile_id: str,
) -> float:
    """Pure decision function over a vertical-datum description string.

    Factored out so tests can exercise the gate without a live raster header.
    Returns the metres-to-add-to-reach-NAVD88 offset (0.0 for NAVD88), or
    raises ``TopobathyDatumError`` for a non-NAVD88 datum with no offset.
    """
    text = (datum_text or "").lower()
    # Positive NAVD88 signal — accept, no offset.
    if "navd88" in text or "navd 88" in text or "navd_88" in text:
        return 0.0
    # Explicit tidal / mean-water datums — the gate target.
    tidal_markers = ("mhw", "mhhw", "mllw", "mlw", "lmsl", "msl", "mean sea level",
                     "mean high water", "mean low water", "tidal")
    if any(mk in text for mk in tidal_markers):
        if navd88_offset_m is not None:
            logger.warning(
                "fetch_topobathy: tile %s reports a non-NAVD88 tidal datum; "
                "applying supplied NAVD88 offset %.4f m (documented)",
                tile_id, navd88_offset_m,
            )
            return float(navd88_offset_m)
        raise TopobathyDatumError(
            f"CUDEM tile {tile_id} reports a non-NAVD88 vertical datum "
            f"(detected tidal datum in: {datum_text.strip()[:200]!r}); refusing "
            "to merge mismatched datums. Supply a documented navd88_offset_m for "
            "this AOI to convert, or use a NAVD88 tile."
        )
    # No vertical-datum signal at all. The CUDEM 1/9 collection is NAVD88 by
    # construction (collection-level metadata), and bare GeoTIFF tiles commonly
    # omit a vertical CS in the per-file WKT. Treat absence-of-signal as the
    # collection default (NAVD88) — this is NOT a silent cross-datum merge: a
    # POSITIVE non-NAVD88 marker is what trips the gate.
    if navd88_offset_m is not None:
        # Caller explicitly asserted an offset — honour it.
        return float(navd88_offset_m)
    logger.info(
        "fetch_topobathy: tile %s carries no per-file vertical-CS tag; "
        "accepting CUDEM collection default (NAVD88, positive-up)",
        tile_id,
    )
    return 0.0


# ---------------------------------------------------------------------------
# 3DEP land DEM (REUSE fetch_dem — do NOT reimplement 3DEP).
# ---------------------------------------------------------------------------


def _fetch_3dep_land_to_file(
    bbox: tuple[float, float, float, float],
    resolution_m: int,
) -> str | None:
    """Fetch the 3DEP land DEM for the AOI by reusing ``fetch_dem`` and stage
    its bytes to a local temp .tif. Returns the temp path, or ``None`` if the
    3DEP fetch fails (so the caller can decide whether CUDEM-alone is enough).

    We reuse ``fetch_dem`` (NOT a fresh py3dep call) so the 3DEP land path,
    its CRS/units, and its cache key are identical to the canonical tool.
    """
    try:
        from .data_fetch import fetch_dem
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_topobathy: could not import fetch_dem: %s", exc)
        return None
    try:
        land_layer = fetch_dem(bbox, resolution_m=resolution_m)
    except Exception as exc:  # noqa: BLE001 — land DEM is best-effort here
        logger.warning(
            "fetch_topobathy: 3DEP land fetch_dem failed for bbox=%s: %s",
            bbox, exc,
        )
        return None
    uri = land_layer.uri
    if not uri:
        return None
    return _stage_uri_to_local(uri)


def _stage_uri_to_local(uri: str) -> str | None:
    """Stage an ``s3://`` / local DEM URI to a local temp .tif for the merge.

    GCP is decommissioned: ``s3://`` is read via boto3 (the cache shim's
    ``read_object_bytes_s3``); a local path is returned as-is.
    """
    if uri.startswith("/") or uri.startswith("file://"):
        return uri[len("file://"):] if uri.startswith("file://") else uri
    try:
        if uri.startswith("s3://"):
            from .cache import read_object_bytes_s3

            data = read_object_bytes_s3(uri)
        else:
            logger.warning("fetch_topobathy: unknown DEM URI scheme: %s", uri)
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_topobathy: could not stage DEM %s locally: %s", uri, exc)
        return None
    with tempfile.NamedTemporaryFile(
        suffix=".tif", delete=False, prefix="grace2_topobathy_3dep_"
    ) as f:
        f.write(data)
        return f.name


# ---------------------------------------------------------------------------
# Merge + reproject + COG.
# ---------------------------------------------------------------------------


def _gdal_bin(name: str) -> str | None:
    """Resolve a GDAL CLI binary (gdalbuildvrt / gdalwarp), honouring the same
    env overrides clip_raster_to_bbox uses. Returns None if not found."""
    import shutil

    env_key = {
        "gdalbuildvrt": "GRACE2_GDALBUILDVRT_BIN",
        "gdalwarp": "GRACE2_GDALWARP_BIN",
    }.get(name)
    candidate = (env_key and os.environ.get(env_key)) or shutil.which(name)
    if candidate:
        return candidate
    # conda grace2 env fallback (same as clip_raster_to_bbox).
    home = os.path.expanduser("~")
    conda = os.path.join(home, "miniforge3", "envs", "grace2", "bin", name)
    if os.path.isfile(conda):
        return conda
    return None


def _build_merged_topobathy(
    cudem_vsicurl_paths: list[str],
    land_local_path: str | None,
    datum_offsets: list[float],
    bbox: tuple[float, float, float, float],
    target_crs: str,
) -> tuple[bytes, bool, int]:
    """Merge CUDEM (coast) + 3DEP land into one EPSG:32616 NAVD88 float32 COG.

    Precedence: CUDEM is listed LAST so it WINS in the overlap (nearshore /
    shoreline); 3DEP fills the land where CUDEM has nodata. Mutual nodata fill.
    Clip + reproject to ``target_crs`` via ``clip_raster_to_bbox._run_gdalwarp_clip``.

    Returns ``(cog_bytes, bathymetry_present, cudem_tile_count)``.
    Pure GDAL where the binaries exist; rasterio fallback otherwise so the
    merge runs in environments without the GDAL CLI (e.g. CI).
    """
    import rasterio

    have_cudem = len(cudem_vsicurl_paths) > 0
    have_land = land_local_path is not None
    if not have_cudem and not have_land:
        raise TopobathyEmptyError(
            f"no CUDEM tiles AND no 3DEP land DEM for bbox={bbox} — no elevation "
            "data available for this AOI"
        )

    # Build the source list with 3DEP land FIRST and CUDEM LAST so the
    # last-listed CUDEM wins on the coast (gdalbuildvrt: later sources paint
    # over earlier ones in the VRT; rasterio.merge: we order accordingly).
    sources_in_precedence: list[str] = []
    if have_land:
        sources_in_precedence.append(land_local_path)  # type: ignore[arg-type]
    sources_in_precedence.extend(cudem_vsicurl_paths)  # CUDEM last = wins

    tmp_paths: list[str] = []
    try:
        # --- Apply any documented datum offsets to CUDEM tiles up-front ---
        # (offset == 0.0 for confirmed-NAVD88 tiles, the normal case — no copy).
        adjusted_cudem: list[str] = []
        for path, offset in zip(cudem_vsicurl_paths, datum_offsets):
            if offset and abs(offset) > 1e-9:
                shifted = _apply_vertical_offset(path, offset)
                tmp_paths.append(shifted)
                adjusted_cudem.append(shifted)
            else:
                adjusted_cudem.append(path)
        sources_in_precedence = (
            ([land_local_path] if have_land else []) + adjusted_cudem  # type: ignore[list-item]
        )

        merged_path = _merge_sources(sources_in_precedence, target_crs, bbox)
        tmp_paths.append(merged_path)

        # --- Re-emit as a single-band float32 COG (LZW, BIGTIFF) ---
        import numpy as np
        import rioxarray as rxr

        da = rxr.open_rasterio(merged_path, masked=True).squeeze(drop=True)
        # Single band, float32, positive-up preserved (NO sign flip anywhere).
        da = da.astype("float32")
        # Carry an explicit NaN nodata onto the COG so downstream consumers
        # (HydroMT setup_dep, QGIS, and masked reads) treat warp-edge / no-cover
        # cells as nodata rather than as bogus 0/NaN elevations. The merge
        # mosaic edges (where no source covers the warped grid corner) are NaN;
        # without an encoded nodata the COG reports nodata=None and those cells
        # leak into statistics. byte-format match to fetch_dem (masked COG).
        try:
            da.rio.write_nodata(np.float32("nan"), encoded=True, inplace=True)
        except Exception:  # noqa: BLE001 — older rioxarray signature
            da.rio.write_nodata(float("nan"), inplace=True)
        with tempfile.NamedTemporaryFile(
            suffix=".tif", delete=False, prefix="grace2_topobathy_cog_"
        ) as f:
            cog_path = f.name
        tmp_paths.append(cog_path)
        da.rio.to_raster(
            cog_path,
            driver="COG",
            compress="LZW",
            BIGTIFF="IF_SAFER",
            dtype="float32",
            nodata=float("nan"),
        )
        with open(cog_path, "rb") as fh:
            cog_bytes = fh.read()

        # Sanity: confirm single-band float32 in the requested CRS.
        with rasterio.open(cog_path) as ds:
            assert ds.count == 1, f"expected single-band COG, got {ds.count}"
            assert str(ds.dtypes[0]) == "float32", (
                f"expected float32, got {ds.dtypes[0]}"
            )
        logger.info(
            "fetch_topobathy: merged %d CUDEM + %s land -> %d-byte COG (%s)",
            len(cudem_vsicurl_paths),
            "1" if have_land else "0",
            len(cog_bytes),
            target_crs,
        )
        return cog_bytes, have_cudem, len(cudem_vsicurl_paths)
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def _apply_vertical_offset(vsicurl_path: str, offset_m: float) -> str:
    """Add a constant vertical offset (metres) to a tile's elevations, writing
    a local temp .tif. Used only when a documented non-NAVD88→NAVD88 offset is
    supplied for a tidal-datum tile."""
    import numpy as np
    import rasterio

    with rasterio.Env(**_VSICURL_ENV_KW):
        with rasterio.open(vsicurl_path) as ds:
            profile = ds.profile.copy()
            arr = ds.read(1, masked=True).astype("float32")
    arr = arr + np.float32(offset_m)
    profile.update(dtype="float32", driver="GTiff")
    with tempfile.NamedTemporaryFile(
        suffix=".tif", delete=False, prefix="grace2_topobathy_voffset_"
    ) as f:
        out = f.name
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(arr.filled(profile.get("nodata", np.nan)).astype("float32"), 1)
    return out


def _merge_sources(
    sources_in_precedence: list[str],
    target_crs: str,
    bbox: tuple[float, float, float, float],
) -> str:
    """Mosaic ``sources_in_precedence`` (last wins) onto a common grid and
    reproject/clip to ``target_crs``.

    GDAL-CLI path (preferred, per spec): gdalbuildvrt (later sources paint
    over earlier) → clip_raster_to_bbox._run_gdalwarp_clip (reproject to
    target_crs + clip to the AOI bbox, bbox given in EPSG:4326).

    rasterio fallback (CI / no-CLI environments): rasterio.merge with
    ``method='last'`` then rasterio.warp reproject. Functionally identical
    precedence (CUDEM, listed last, wins).
    """
    gdalbuildvrt = _gdal_bin("gdalbuildvrt")
    gdalwarp = _gdal_bin("gdalwarp")
    if gdalbuildvrt and gdalwarp:
        return _merge_sources_gdal_cli(sources_in_precedence, target_crs, bbox)
    logger.info(
        "fetch_topobathy: GDAL CLI (gdalbuildvrt/gdalwarp) not found; using the "
        "rasterio merge+warp fallback (functionally identical precedence)"
    )
    return _merge_sources_rasterio(sources_in_precedence, target_crs, bbox)


def _merge_sources_gdal_cli(
    sources_in_precedence: list[str],
    target_crs: str,
    bbox: tuple[float, float, float, float],
) -> str:
    """gdalbuildvrt (later wins) + clip_raster_to_bbox._run_gdalwarp_clip."""
    import subprocess

    gdalbuildvrt = _gdal_bin("gdalbuildvrt")
    assert gdalbuildvrt is not None
    with tempfile.NamedTemporaryFile(
        suffix=".vrt", delete=False, prefix="grace2_topobathy_"
    ) as f:
        vrt_path = f.name
    # -allow_projdiff: CUDEM is EPSG:4269 (NAD83) + 3DEP is EPSG:5070 — the VRT
    # collects them in their native CRS; the gdalwarp reproject step lands the
    # mosaic on the single target CRS. gdalbuildvrt paints later inputs over
    # earlier ones, so listing CUDEM LAST makes it win on the coast.
    cmd = [
        gdalbuildvrt,
        "-allow_projection_difference",
        "-resolution", "highest",
        vrt_path,
        *sources_in_precedence,
    ]
    env = dict(os.environ)
    env.update({k: str(v) for k, v in _VSICURL_ENV_KW.items()})
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise TopobathyUpstreamError(
            f"gdalbuildvrt failed (rc={proc.returncode}): {proc.stderr.strip()[:400]}"
        )
    # Reproject + clip to the AOI via the shared clip seam. The bbox is in
    # EPSG:4326; _run_gdalwarp_clip passes -te_srs EPSG:4326 + -t_srs target_crs.
    from .clip_raster_to_bbox import _run_gdalwarp_clip

    with tempfile.NamedTemporaryFile(
        suffix=".tif", delete=False, prefix="grace2_topobathy_merged_"
    ) as f:
        merged_path = f.name
    try:
        _run_gdalwarp_clip(
            input_path=vrt_path,
            output_path=merged_path,
            bbox=bbox,
            bbox_crs="EPSG:4326",
            target_crs=target_crs,
        )
    finally:
        try:
            os.unlink(vrt_path)
        except OSError:
            pass
    return merged_path


def _merge_sources_rasterio(
    sources_in_precedence: list[str],
    target_crs: str,
    bbox: tuple[float, float, float, float],
) -> str:
    """rasterio.merge(method='last') + reproject — the no-GDAL-CLI fallback."""
    import numpy as np
    import rasterio
    from rasterio.merge import merge as rio_merge
    from rasterio.warp import calculate_default_transform, reproject, Resampling

    open_handles = []
    try:
        with rasterio.Env(**_VSICURL_ENV_KW):
            for src in sources_in_precedence:
                open_handles.append(rasterio.open(src))
            # method='last': the LAST source in the list wins where data
            # overlaps — CUDEM is listed last, so CUDEM wins on the coast.
            mosaic, mosaic_transform = rio_merge(
                open_handles, method="last", nodata=open_handles[0].nodata
            )
            src_crs = open_handles[0].crs
            src_nodata = open_handles[0].nodata
        mosaic = mosaic.astype("float32")

        # Reproject the (native-CRS) mosaic to the target CRS.
        height, width = mosaic.shape[-2], mosaic.shape[-1]
        dst_transform, dst_w, dst_h = calculate_default_transform(
            src_crs, target_crs, width, height,
            left=mosaic_transform.c,
            top=mosaic_transform.f,
            right=mosaic_transform.c + mosaic_transform.a * width,
            bottom=mosaic_transform.f + mosaic_transform.e * height,
        )
        dst = np.full((1, dst_h, dst_w), np.nan, dtype="float32")
        reproject(
            source=mosaic[0],
            destination=dst[0],
            src_transform=mosaic_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=target_crs,
            src_nodata=src_nodata,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        with tempfile.NamedTemporaryFile(
            suffix=".tif", delete=False, prefix="grace2_topobathy_merged_"
        ) as f:
            merged_path = f.name
        profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "height": dst_h,
            "width": dst_w,
            "crs": target_crs,
            "transform": dst_transform,
            "nodata": float("nan"),
        }
        with rasterio.open(merged_path, "w", **profile) as out:
            out.write(dst[0], 1)
        return merged_path
    finally:
        for h in open_handles:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Orchestration — the fetch_fn handed to the cache shim.
# ---------------------------------------------------------------------------


def _fetch_topobathy_bytes_and_flags(
    bbox: tuple[float, float, float, float],
    resolution_m: int,
    target_crs: str,
    navd88_offset_m: float | None,
    timeout_s: float,
) -> tuple[bytes, bool, str | None, int]:
    """Produce the merged-topobathy COG bytes + the bathymetry-present flags.

    Returns ``(cog_bytes, bathymetry_present, fallback_warning, cudem_count)``.
    """
    # 1) Select intersecting CUDEM tiles (best-effort — empty == no coverage).
    cudem_urls: list[str] = []
    try:
        cudem_urls = _select_cudem_tiles(bbox, timeout_s)
    except TopobathyUpstreamError as exc:
        # Manifest unreachable — treat as "CUDEM unavailable" and degrade to
        # land-only (data-source fallback norm); do NOT abort the coastal run.
        logger.warning(
            "fetch_topobathy: CUDEM tile-index unreachable (%s); degrading to "
            "3DEP-land-only", exc,
        )
        cudem_urls = []

    cudem_vsicurl: list[str] = [f"/vsicurl/{u}" for u in cudem_urls]

    # 2) Datum gate on each selected CUDEM tile (Invariant 7).
    datum_offsets: list[float] = []
    gated_paths: list[str] = []
    for vp in cudem_vsicurl:
        # _assert_navd88 raises TopobathyDatumError for a non-NAVD88 tile with
        # no offset (propagates — never a silent cross-datum merge). A header
        # read failure raises TopobathyUpstreamError; we skip that single tile
        # rather than abort, but keep the rest.
        try:
            offset = _assert_navd88(vp, navd88_offset_m)
        except TopobathyUpstreamError as exc:
            logger.warning(
                "fetch_topobathy: skipping CUDEM tile (header unreadable): %s", exc
            )
            continue
        gated_paths.append(vp)
        datum_offsets.append(offset)
    cudem_vsicurl = gated_paths

    # 3) 3DEP land DEM (REUSE fetch_dem) — best-effort.
    land_local = _fetch_3dep_land_to_file(bbox, resolution_m)

    # 4) Merge / reproject / COG.
    cog_bytes, bathy_present, cudem_count = _build_merged_topobathy(
        cudem_vsicurl_paths=cudem_vsicurl,
        land_local_path=land_local,
        datum_offsets=datum_offsets,
        bbox=bbox,
        target_crs=target_crs,
    )

    # 5) Honest fallback warning when CUDEM is absent (data-source norm).
    fallback_warning: str | None = None
    if not bathy_present:
        fallback_warning = (
            "BATHYMETRY ABSENT: no NOAA NCEI CUDEM topo-bathy tiles were found "
            f"for this AOI {bbox}; the elevation surface is 3DEP LAND-ONLY "
            "(below-waterline cells are nodata). A coastal flood / surge run on "
            "this DEM has NO nearshore bed and will under-represent inundation. "
            "Treat results as land-pluvial only until bathymetry is available."
        )
        logger.warning("fetch_topobathy: %s", fallback_warning)

    # Clean up the staged land file (the merge already read it).
    if land_local and land_local.startswith(tempfile.gettempdir()):
        try:
            os.unlink(land_local)
        except OSError:
            pass

    return cog_bytes, bathy_present, fallback_warning, cudem_count


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(
    _METADATA,
    # Annotations: readOnlyHint=True (read-only; no state mutation),
    # openWorldHint=True (calls external public S3 endpoints),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_topobathy(
    bbox: tuple[float, float, float, float],
    resolution_m: int = 10,
    target_crs: str = TARGET_CRS,
    navd88_offset_m: float | None = None,
    timeout_s: float = 120.0,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> TopobathyResult:
    """Fetch a SEAMLESS coastal topo-bathymetry DEM (land + sea floor) for a bbox.

    **What it does:**
        Builds ONE continuous elevation surface across the shoreline by merging
        NOAA NCEI CUDEM 1/9 arc-second topo-bathymetry tiles (the canonical US
        coastal merged topo+bathy product — only the 1/9 arc-second tiles
        integrate BOTH bathymetry and topography) with the USGS 3DEP land DEM
        for the same area. CUDEM wins on the coast / nearshore; 3DEP fills the
        land. Output is a single-band float32 Cloud-Optimized GeoTIFF in
        EPSG:32616 (UTM 16N), NAVD88 metres, **positive-up** (land positive,
        bathymetry NEGATIVE, NO sign flip) — byte-format identical to
        ``fetch_dem`` so ``build_sfincs_model``'s ``setup_dep`` consumes it
        unchanged.

    **When to use:**
        - A COASTAL flood / surge / run-up workflow (SFINCS coastal) that needs
          a continuous bed from the hills to the deep water — the canonical
          North Star entry point. ``fetch_dem`` alone is LAND-ONLY and leaves
          the nearshore as nodata.
        - User asks for "topobathy", "bathymetry + topography", "the sea floor
          and the land together", or "a DEM that includes the water depth".

    **When NOT to use:**
        - A pure inland / pluvial flood with no coast — use ``fetch_dem`` (no
          bathymetry needed; CUDEM has no inland coverage anyway).
        - Outside the US coast — NOAA NCEI CUDEM is US-coast-only; the validator
          raises ``TopobathyInputError`` for a bbox that misses the US coastal
          envelope.

    **Parameters:**
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326. Must
            intersect the US coastal envelope. Demo AOI (Florida panhandle /
            Mexico Beach): ``(-85.75, 29.55, -85.25, 30.20)``.
        resolution_m: 3DEP land-DEM grid spacing in metres (default 10). The
            CUDEM tiles are native ~3 m; the merge re-grids onto the warp output
            grid.
        target_crs: output CRS (default ``EPSG:32616`` / UTM 16N for the demo
            AOI). NAVD88 vertical is preserved (only the horizontal grid is
            reprojected).
        navd88_offset_m: OPTIONAL documented vertical offset (metres) to ADD to
            a CUDEM tile's elevations to bring a tidal-datum (MHW/MSL/LMSL) tile
            onto NAVD88. Leave ``None`` for the normal NAVD88 path — a
            non-NAVD88 tile with no offset raises ``TopobathyDatumError`` rather
            than silently merging (Invariant 7).
        timeout_s: tile-index download timeout (seconds, default 120).

    **Returns:**
        A ``TopobathyResult`` (a ``LayerURI`` subclass) pointing at the merged
        COG in the cache bucket
        (``.../cache/static-30d/topobathy/<key>.tif``).
        ``layer_type="raster"``, ``role="input"``,
        ``style_preset="continuous_dem"``, ``units="meters"``. Extra honesty
        fields: ``bathymetry_present`` (False on the land-only fallback),
        ``fallback_warning`` (set when bathy is absent), ``cudem_tile_count``.

    **Fallback (data-source norm):**
        If CUDEM tiles are missing / unreachable for the AOI, the tool DEGRADES
        to a 3DEP-LAND-ONLY DEM and returns a result with
        ``bathymetry_present=False`` + an honest ``fallback_warning`` — never a
        silent dead-end or a fabricated bathymetry. Only if BOTH CUDEM and 3DEP
        are unavailable does it raise ``TopobathyEmptyError``.

    **Errors (FR-AS-11 typed-error surface):**
        - ``TopobathyInputError``: bad bbox / outside US coast (retryable=False).
        - ``TopobathyDatumError``: a CUDEM tile is non-NAVD88 with no offset
          (retryable=False) — Invariant 7.
        - ``TopobathyUpstreamError``: tile read / merge / COG failure
          (retryable=True).
        - ``TopobathyEmptyError``: no CUDEM AND no 3DEP for the AOI
          (retryable=False).

    **Cross-tool dependencies (FR-TA-3):**
        - REUSES ``fetch_dem`` for the 3DEP land DEM (does NOT reimplement 3DEP).
        - Composes INTO ``build_sfincs_model`` (``setup_dep`` elevtn) — drop-in
          for ``fetch_dem`` on the coastal path.
        - Upstream sources: NOAA NCEI CUDEM 1/9 arc-second (public S3
          ``noaa-nos-coastal-lidar-pds``) + USGS 3DEP (via ``fetch_dem``).

    Cache: ``ttl_class="static-30d"``, ``source_class="topobathy"``.
    Tier-1 free. No API key. ``supports_global_query=False``.
    """
    if not isinstance(bbox, tuple):
        try:
            bbox = tuple(bbox)  # type: ignore[arg-type]
        except TypeError as exc:
            raise TopobathyInputError(
                f"bbox must be a 4-tuple or list; got {type(bbox).__name__}"
            ) from exc
    _validate_bbox(bbox)  # type: ignore[arg-type]
    q_bbox = _round_bbox_to_6dp(bbox)  # type: ignore[arg-type]

    try:
        res_m = int(resolution_m)
    except (TypeError, ValueError) as exc:
        raise TopobathyInputError(
            f"resolution_m must be an integer; got {resolution_m!r}"
        ) from exc
    if not (1 <= res_m <= 1000):
        raise TopobathyInputError(
            f"resolution_m must be in [1, 1000]; got {res_m}"
        )

    if navd88_offset_m is not None:
        try:
            navd88_offset_m = float(navd88_offset_m)
        except (TypeError, ValueError) as exc:
            raise TopobathyInputError(
                f"navd88_offset_m must be a finite number or None; got "
                f"{navd88_offset_m!r}"
            ) from exc
        if not math.isfinite(navd88_offset_m):
            raise TopobathyInputError(
                f"navd88_offset_m must be finite; got {navd88_offset_m!r}"
            )

    tcrs = (target_crs or TARGET_CRS).strip()

    try:
        t_s = float(timeout_s)
    except (TypeError, ValueError) as exc:
        raise TopobathyInputError(
            f"timeout_s must be a finite number; got {timeout_s!r}"
        ) from exc
    if not math.isfinite(t_s) or t_s <= 0:
        raise TopobathyInputError(f"timeout_s must be > 0 and finite; got {t_s!r}")

    # The bathymetry-present flag + warning are produced by fetch_fn but the
    # cache shim only persists/returns bytes. We capture them in a closure cell
    # so the LayerURI we build below carries them whether the bytes came from a
    # fresh fetch OR a cache hit (a cache hit means a prior fetch already proved
    # CUDEM coverage for this exact bbox — bathy was present then).
    _flags: dict[str, Any] = {
        "bathymetry_present": True,
        "fallback_warning": None,
        "cudem_tile_count": 0,
    }

    def _fetch() -> bytes:
        cog, bathy, warn, count = _fetch_topobathy_bytes_and_flags(
            q_bbox, res_m, tcrs, navd88_offset_m, t_s
        )
        _flags["bathymetry_present"] = bathy
        _flags["fallback_warning"] = warn
        _flags["cudem_tile_count"] = count
        return cog

    result = read_through(
        metadata=_METADATA,
        params={
            "bbox": list(q_bbox),
            "resolution_m": res_m,
            "target_crs": tcrs,
            # offset participates in the key so a different documented offset
            # produces a distinct artifact.
            "navd88_offset_m": navd88_offset_m,
        },
        ext="tif",
        fetch_fn=_fetch,
    )
    assert result.uri is not None, (
        "fetch_topobathy is cacheable; uri must be set by read_through"
    )

    return TopobathyResult(
        layer_id=(
            "topobathy-"
            f"{q_bbox[0]:.4f}-{q_bbox[1]:.4f}-{q_bbox[2]:.4f}-{q_bbox[3]:.4f}"
        ),
        name=(
            "Coastal topo-bathymetry DEM (NOAA CUDEM 1/9\" + USGS 3DEP, "
            "NAVD88 m) — bbox "
            f"({q_bbox[0]:.2f},{q_bbox[1]:.2f},{q_bbox[2]:.2f},{q_bbox[3]:.2f})"
        ),
        layer_type="raster",
        uri=result.uri,
        style_preset=_STYLE_PRESET,
        role="input",
        units="meters",
        bbox=q_bbox,
        bathymetry_present=bool(_flags["bathymetry_present"]),
        fallback_warning=_flags["fallback_warning"],
        cudem_tile_count=int(_flags["cudem_tile_count"]),
    )
