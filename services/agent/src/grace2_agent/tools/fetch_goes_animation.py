"""``fetch_goes_animation`` atomic tool -- GOES-18 GeoColor + Fire Temperature animation frames (fire demo S3).

PATH A (ready-made CIRA/RAMMB SLIDER tiles). Builds an ORDERED list of per-frame
EPSG:4326 COGs over a (start_utc, end_utc) window for a GOES geostationary
product (GeoColor or Fire Temperature) at the CONUS 5-minute cadence -- the exact
imagery + cadence the CIRA Instagram fire animations are made from. Each frame is
a 3-band RGB COG (publish_layer's multiband passthrough renders it directly, no
colormap), labelled with its REAL UTC valid-time so the web scrubber's frame
labels match the CIRA caption.

This is the GOES analogue of ``fetch_goes_satellite`` (which fetches only the
single MOST-RECENT MCMIPC frame): it anchors on a requested TIME RANGE and emits
one frame per scan time, and it pulls the ready-made GeoColor / Fire Temperature
RGB products (which fetch_goes_satellite cannot composite -- GeoColor is a
proprietary CIRA algorithm; Fire Temperature is a C07/C06/C05 SWIR composite).

Strategy (per the design spike S3):

1. ``_build_frame_list`` -- read the SLIDER ``latest_times.json`` time index for
   (goes-18, conus, <product>), window to start <= t <= end, then even-subsample
   down to a frame cap (first + last always kept, mirroring postprocess_flood's
   ``_select_frame_time_indices``).
2. Per frame: ONE ``read_through`` (so each timestamp caches independently) ->
   stitch the SLIDER tile grid covering the AOI -> reproject the fixed grid ->
   EPSG:4326 COG via the shared ``_satellite_slider`` substrate.
3. Return an ordered ``list[LayerURI]`` each carrying its real UTC valid-time in
   the NAME token (the postprocess_flood frame contract: distinct per-frame cache
   keys + shared style_preset + same bbox + an ISO-time name token), so
   ``detectSequentialGroups`` + the SequenceScrubber animate them with NO web
   change.

Georeferencing is the APPROXIMATE sector-extent mapping documented in
``_satellite_slider`` (SLIDER ships no projection metadata). The imagery + cadence
are the real CIRA product; the pixel-to-ground registration is a sector-extent
approximation (LIVE-VERIFY for sub-pixel accuracy). The honesty floor holds: an
AOI crop with no imagery pixels raises a typed error rather than emitting a blank
frame.

PATH B (raw noaa-goes18 ABI-L2 C07/C06/C05 Fire Temperature composite) is the
optional full-control fallback noted in the spike S4; it is left as a commented
seam below, NOT built here.

Cache key (per frame): SHA-256 over ``(bbox-6dp, product, satellite, sector,
ts_int, zoom)`` -- the frame timestamp makes each frame's key distinct.

ASCII only.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through
from ._satellite_slider import (
    SliderEmptyError,
    SliderError,
    SliderUpstreamError,
    fetch_slider_timestamps,
    mosaic_to_cog_bytes,
    pick_zoom_for_aoi,
    stitch_slider_mosaic,
    ts_int_to_datetime,
    ts_int_to_iso,
)

__all__ = [
    "fetch_goes_animation",
    "GOESAnimError",
    "GOESAnimInputError",
    "GOESAnimBboxRequiredError",
    "GOESAnimUpstreamError",
    "GOESAnimEmptyError",
    "GOES_ANIM_PRODUCTS",
    "GOES_ANIM_SATELLITES",
    "MAX_ANIM_FRAMES",
    "_parse_utc",
    "_band_to_slider_product",
    "_select_frame_indices",
    "_build_frame_list",
]

logger = logging.getLogger("grace2_agent.tools.fetch_goes_animation")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class GOESAnimError(RuntimeError):
    """Base class for fetch_goes_animation failures."""

    error_code: str = "GOES_ANIM_ERROR"
    retryable: bool = True


class GOESAnimInputError(GOESAnimError):
    """Invalid input (unknown band/product, unknown satellite, bad window)."""

    error_code = "GOES_ANIM_INPUT_INVALID"
    retryable = False


class GOESAnimBboxRequiredError(GOESAnimError):
    """bbox is required (a sector-wide animation would be enormous)."""

    error_code = "BBOX_REQUIRED"
    retryable = False


class GOESAnimUpstreamError(GOESAnimError):
    """SLIDER time-index or tile fetch failed."""

    error_code = "GOES_ANIM_UPSTREAM_ERROR"
    retryable = True


class GOESAnimEmptyError(GOESAnimError):
    """The window matched no SLIDER frames, or every frame crop was empty."""

    error_code = "GOES_ANIM_EMPTY"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

#: band/product name -> SLIDER product slug (CONFIRMED from define-products.js).
_BAND_TO_SLIDER_PRODUCT: dict[str, str] = {
    "geocolor": "geocolor",
    "fire_temperature": "fire_temperature",
}

GOES_ANIM_PRODUCTS = tuple(_BAND_TO_SLIDER_PRODUCT.keys())

#: Supported GOES satellites for the SLIDER path (West + East operational).
GOES_ANIM_SATELLITES = ("goes-18", "goes-19")

#: SLIDER product label for the LayerURI name.
_PRODUCT_LABEL: dict[str, str] = {
    "geocolor": "GeoColor",
    "fire_temperature": "Fire Temperature",
}

#: Shared style preset name for every frame (RGB COG -> publish_layer multiband
#: passthrough; no colormap). A consistent preset across frames is part of the
#: scrubber-group contract.
_GOES_ANIM_STYLE_PRESET = "goes_rgb_animation"

#: Upper bound on emitted frames (mirrors postprocess_flood.MAX_FLOOD_FRAMES=144).
#: ~6.5h / 5min ~= 78 frames sits comfortably under this; a larger window
#: even-subsamples down (first + last kept). Overridable via env.
MAX_ANIM_FRAMES: int = int(os.environ.get("GRACE2_MAX_ANIM_FRAMES", "144"))

#: Bbox quantization (6dp) for cache-key stability.
_BBOX_QUANTIZE_DP = 6


# ---------------------------------------------------------------------------
# AtomicToolMetadata.
# ---------------------------------------------------------------------------


def _build_metadata() -> AtomicToolMetadata:
    common = dict(
        name="fetch_goes_animation",
        ttl_class="dynamic-1h",
        source_class="goes_animation",
        cacheable=True,
    )
    try:
        return AtomicToolMetadata(**common, supports_global_query=False)  # type: ignore[call-arg]
    except Exception:
        return AtomicToolMetadata(**common)


_METADATA = _build_metadata()


# ---------------------------------------------------------------------------
# Pure helpers (also importable for tests).
# ---------------------------------------------------------------------------


def _parse_utc(value: Any) -> datetime:
    """Parse an ISO-8601 (or 'YYYY-MM-DD HH:MM') string / datetime -> aware UTC.

    Accepts a trailing 'Z', '+00:00', a space or 'T' separator, and a bare date.
    Raises ``GOESAnimInputError`` for an unparseable value.
    """
    if isinstance(value, datetime):
        dt = value
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise GOESAnimInputError(f"time must be an ISO-8601 string or datetime; got {value!r}")
    s = value.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value.strip().replace(" ", "T", 1), fmt)
                break
            except ValueError:
                continue
        else:
            raise GOESAnimInputError(
                f"could not parse UTC time {value!r}; use ISO-8601 "
                "(e.g. '2026-06-22T13:30:00Z')"
            )
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _band_to_slider_product(band: str) -> str:
    """Map a band/product name to the SLIDER product slug."""
    try:
        return _BAND_TO_SLIDER_PRODUCT[band]
    except KeyError as exc:
        raise GOESAnimInputError(
            f"unknown band/product={band!r}; allowed: {sorted(_BAND_TO_SLIDER_PRODUCT)}"
        ) from exc


def _select_frame_indices(n: int, cap: int = MAX_ANIM_FRAMES) -> list[int]:
    """Pick up to ``cap`` evenly-spaced indices over ``n`` items, endpoints kept.

    Mirrors ``postprocess_flood._select_frame_time_indices``: when ``n <= cap``
    every index is returned; otherwise an even ``linspace`` (rounded + unique)
    keeps the first + last and subsamples the middle. Logs a subsample.
    """
    if n <= 0:
        return []
    if n <= cap:
        return list(range(n))
    import numpy as np

    idx = np.linspace(0, n - 1, cap).round().astype(int)
    kept = [int(i) for i in np.unique(idx)]
    logger.info(
        "fetch_goes_animation: %d in-window frames exceed cap=%d; "
        "subsampling evenly to %d (first+last kept).",
        n,
        cap,
        len(kept),
    )
    return kept


def _build_frame_list(
    timestamps_int: list[int],
    start_utc: datetime,
    end_utc: datetime,
    cap: int = MAX_ANIM_FRAMES,
) -> list[int]:
    """Window the SLIDER time index to [start, end] and even-subsample to ``cap``.

    ``timestamps_int`` is the ascending SLIDER ``timestamps_int`` list. Returns
    the ORDERED (ascending) list of selected timestamp ints. Pure function.
    """
    in_window = [
        ts
        for ts in timestamps_int
        if start_utc <= ts_int_to_datetime(ts) <= end_utc
    ]
    in_window.sort()
    keep = _select_frame_indices(len(in_window), cap=cap)
    return [in_window[i] for i in keep]


def _validate_bbox(bbox: Any) -> tuple[float, float, float, float]:
    if bbox is None:
        raise GOESAnimBboxRequiredError(
            "bbox is required for fetch_goes_animation (a sector-wide animation "
            "is enormous); pass (min_lon, min_lat, max_lon, max_lat)."
        )
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        raise GOESAnimInputError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    vals = tuple(float(v) for v in bbox)
    if not all(math.isfinite(v) for v in vals):
        raise GOESAnimInputError(f"bbox contains non-finite values: {bbox!r}")
    min_lon, min_lat, max_lon, max_lat = vals
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise GOESAnimInputError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise GOESAnimInputError(f"bbox lat out of [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise GOESAnimInputError(f"bbox is degenerate (min<max on both axes): {bbox!r}")
    return (min_lon, min_lat, max_lon, max_lat)


def _round_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(round(v, _BBOX_QUANTIZE_DP) for v in bbox)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Per-frame fetch (the read_through fetch_fn).
# ---------------------------------------------------------------------------


def _fetch_frame_cog_bytes(
    sat: str,
    sector: str,
    product: str,
    ts_int: int,
    zoom: int,
    bbox: tuple[float, float, float, float],
) -> bytes:
    """Stitch + reproject one SLIDER frame -> 3-band EPSG:4326 RGB COG bytes."""
    rgb, mosaic_extent = stitch_slider_mosaic(sat, sector, product, ts_int, zoom, bbox)
    return mosaic_to_cog_bytes(rgb, mosaic_extent, bbox)


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(
    _METADATA,
    # readOnlyHint=True, openWorldHint=True (external SLIDER tiles),
    # destructiveHint=False, idempotentHint=True (per-frame cache dedupes).
    open_world_hint=True,
)
def fetch_goes_animation(
    bbox: tuple[float, float, float, float],
    band: str = "geocolor",
    satellite: str = "goes-18",
    sector: str = "conus",
    start_utc: str | None = None,
    end_utc: str | None = None,
    step_minutes: int = 5,
    # job-0164: absorb LLM-invented kwargs.
    **_extra_ignored: Any,
) -> list[LayerURI]:
    """Build a GOES GeoColor / Fire Temperature animation (ordered per-frame RGB COGs) over a time window.

    **What it does:** Pulls the ready-made CIRA/RAMMB SLIDER GeoColor or Fire
    Temperature RGB imagery for a GOES geostationary satellite (default GOES-18 /
    GOES-West, CONUS sector, 5-minute cadence) across a UTC time window, and
    returns an ORDERED list of per-frame EPSG:4326 RGB COGs over the AOI -- one
    frame per SLIDER scan time, each labelled with its real UTC valid-time. This
    is how you RECREATE a CIRA-style intra-day fire animation (the imagery and
    cadence are the exact product the CIRA loops are made from). Emit the frames
    through publish_layer and the web scrubber animates them.

    **When to use:**
    - "Recreate the GOES fire animation over this window", "animate the GOES-18
      GeoColor + Fire Temperature loop for 2026-06-22", "show the fire evolving
      on satellite over a 6-hour window at 5-minute cadence".
    - Any intra-day (minutes-to-hours) geostationary timelapse over a CONUS AOI.

    **When NOT to use:**
    - A single most-recent frame (use ``fetch_goes_satellite``).
    - A MULTI-DAY polar timelapse / VIIRS Day Fire (use ``fetch_viirs_day_fire``;
      JPSS polar passes, not a 5-minute geostationary cadence).
    - Active-fire pixel detections (``fetch_firms_active_fire``) or perimeters
      (``fetch_nifc_fire_perimeters``).

    **Parameters:**
    - ``bbox`` (tuple): ``(min_lon, min_lat, max_lon, max_lat)`` EPSG:4326.
      Required.
    - ``band`` (str, default ``"geocolor"``): ``"geocolor"`` or
      ``"fire_temperature"`` (the two CIRA fire-animation products).
    - ``satellite`` (str, default ``"goes-18"``): ``"goes-18"`` (West) or
      ``"goes-19"`` (East).
    - ``sector`` (str, default ``"conus"``): SLIDER sector slug (``"conus"`` /
      ``"full_disk"``).
    - ``start_utc`` / ``end_utc`` (str): ISO-8601 UTC window bounds
      (e.g. ``"2026-06-22T13:30:00Z"`` .. ``"2026-06-22T20:00:00Z"``). When
      omitted, the most-recent ~6.5h is used.
    - ``step_minutes`` (int, default 5): the requested cadence; the CONUS SLIDER
      index is natively 5-minute, so this is informational (frames are taken at
      the SLIDER timestamps inside the window, then even-subsampled to the cap).

    **Returns:** an ORDERED ``list[LayerURI]`` (ascending UTC). Each is a 3-band
    uint8 RGB COG (``layer_type="raster"``, ``role="context"``,
    ``style_preset="goes_rgb_animation"``, same ``bbox``) whose ``name`` carries
    the product + ISO-UTC valid-time + satellite -- the scrubber-group contract,
    so the web SequenceScrubber animates the set with no web change.

    NOTE: georeferencing is the approximate SLIDER sector-extent mapping (SLIDER
    ships no projection); the imagery + cadence are the real CIRA product. An AOI
    with no imagery pixels raises a typed error (honesty floor).

    **Cross-tool dependencies:**
    - Upstream: ``fetch_wfigs_incident`` (the AOI bbox + the window floor).
    - Pairs with: ``fetch_firms_active_fire`` (historical-date hot-pixel overlay)
      + ``fetch_nifc_fire_perimeters`` (perimeter overlay).
    - Driven by: ``run_model_satellite_fire_animation``.
    """
    q_bbox = _round_bbox(_validate_bbox(bbox))
    product = _band_to_slider_product(band)
    if satellite not in GOES_ANIM_SATELLITES:
        raise GOESAnimInputError(
            f"unknown satellite={satellite!r}; allowed: {list(GOES_ANIM_SATELLITES)}"
        )

    # Resolve the window. Default: most-recent ~6.5h ending now.
    now = datetime.now(timezone.utc)
    end_dt = _parse_utc(end_utc) if end_utc else now
    start_dt = _parse_utc(start_utc) if start_utc else (end_dt - timedelta(hours=6, minutes=30))
    if start_dt >= end_dt:
        raise GOESAnimInputError(
            f"start_utc ({start_dt.isoformat()}) must be before end_utc "
            f"({end_dt.isoformat()})"
        )

    # 1. Read the SLIDER time index + window + subsample the frame list.
    try:
        all_ts = fetch_slider_timestamps(satellite, sector, product)
    except SliderError as exc:
        raise GOESAnimUpstreamError(str(exc)) from exc
    frame_ts = _build_frame_list(all_ts, start_dt, end_dt, cap=MAX_ANIM_FRAMES)
    if not frame_ts:
        raise GOESAnimEmptyError(
            f"no SLIDER {product} frames for {satellite}/{sector} in window "
            f"{start_dt.isoformat()}..{end_dt.isoformat()} "
            f"(index has {len(all_ts)} timestamps)"
        )

    zoom = pick_zoom_for_aoi(satellite, sector, q_bbox)
    sat_label = satellite.upper()
    product_label = _PRODUCT_LABEL.get(product, product)

    # 2. Per-frame fetch (one read_through each -> independent cache key).
    layers: list[LayerURI] = []
    n_empty = 0
    last_err: SliderError | None = None
    for frame_no, ts_int in enumerate(frame_ts, start=1):
        iso = ts_int_to_iso(ts_int)
        params = {
            "bbox": list(q_bbox),
            "product": product,
            "satellite": satellite,
            "sector": sector,
            "ts_int": ts_int,
            "zoom": zoom,
        }
        try:
            result = read_through(
                metadata=_METADATA,
                params=params,
                ext="tif",
                fetch_fn=lambda s=satellite, p=product, t=ts_int: _fetch_frame_cog_bytes(
                    s, sector, p, t, zoom, q_bbox
                ),
            )
        except SliderEmptyError as exc:
            # A single empty frame (transparent crop) is skipped, not fatal.
            n_empty += 1
            last_err = exc
            logger.warning("fetch_goes_animation: empty frame ts=%s skipped (%s)", iso, exc)
            continue
        except SliderUpstreamError as exc:
            n_empty += 1
            last_err = exc
            logger.warning("fetch_goes_animation: frame ts=%s upstream-failed (%s)", iso, exc)
            continue
        assert result.uri is not None
        layers.append(
            LayerURI(
                layer_id=f"goes-anim-{product}-{ts_int}-{q_bbox[0]:.3f}-{q_bbox[1]:.3f}",
                name=f"GOES {product_label} {iso} ({sat_label})",
                layer_type="raster",
                uri=result.uri,
                style_preset=_GOES_ANIM_STYLE_PRESET,
                role="context",
                units=None,
                bbox=q_bbox,
            )
        )

    # Honesty floor: a run that produced NO frames is not success.
    if not layers:
        raise GOESAnimEmptyError(
            f"every one of {len(frame_ts)} {product} frames was empty/failed for "
            f"{satellite}/{sector} over the AOI"
            + (f": {last_err}" if last_err else "")
        )
    logger.info(
        "fetch_goes_animation: %d %s frames (%d empty skipped) for %s/%s window "
        "%s..%s zoom=%d",
        len(layers),
        product,
        n_empty,
        satellite,
        sector,
        start_dt.isoformat(),
        end_dt.isoformat(),
        zoom,
    )
    return layers


# ---------------------------------------------------------------------------
# PATH B (raw noaa-goes18 ABI-L2 C07/C06/C05 Fire Temperature composite) SEAM.
#
# The spike S4 raw-band Fire Temperature path is intentionally NOT built here.
# It would read MCMIPC CMI_C07 (3.9um BT, R, 0-60 C), CMI_C06 (2.2um refl, G,
# 0-100%), CMI_C05 (1.6um refl, B, 0-75%), gamma 1, per the NOAA-NESDIS / CIRA
# Fire Temperature RGB Quick Guide, applying the CF scale_factor/add_offset per
# band (rasterio's NETCDF driver does not auto-apply CF scaling) and stacking
# into a 3-band uint8 COG. It reuses fetch_goes_satellite's S3 lister +
# _reproject_and_clip core. Left as a documented seam; PATH A (SLIDER) is the
# recommended demo path.
# ---------------------------------------------------------------------------
