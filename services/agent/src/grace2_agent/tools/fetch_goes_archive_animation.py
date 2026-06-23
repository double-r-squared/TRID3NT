"""``fetch_goes_archive_animation`` -- GOES Fire Temperature animation from the RAW noaa-goes18 S3 ARCHIVE (PATH B).

The HISTORICAL-capable companion to ``fetch_goes_animation``. Where
``fetch_goes_animation`` pulls the ready-made CIRA/RAMMB SLIDER tiles (which only
serve ~100 RECENT frames -- NO historical archive), this tool reads the RAW
``ABI-L2-MCMIPC`` netCDFs from the public ``noaa-goes18`` S3 bucket (a FULL
historical archive, anonymous, no key) and composites the NOAA-NESDIS / CIRA
**Fire Temperature** RGB per-frame, for ANY date (including the distant past).

This unlocks BOTH:
  (b) HISTORICAL dates -- the S3 archive has every 5-minute CONUS scan going back
      to the GOES-18 operational start (vs SLIDER's ~100 recent frames), and
  (c) Fire Temperature -- composited here from the raw C07/C06/C05 CMI bands with
      full control (vs SLIDER's pre-rendered tiles, which had a zoom-coverage gap).

Fire Temperature RGB recipe (NOAA-NESDIS / CIRA Quick Guide, design spike S.12):
  R = ABI C07 (3.9um) BRIGHTNESS TEMPERATURE, stretch 0-60 C (273.15-333.15 K),
      gamma 1, NOT inverted.
  G = ABI C06 (2.2um) REFLECTANCE, stretch 0-100 % (0-1.0 factor), gamma 1.
  B = ABI C05 (1.6um) REFLECTANCE, stretch 0-75 % (0-0.75 factor), gamma 1.
  Per channel: linear stretch -> clip to [0, 1] -> gamma 1 -> scale to 0-255 uint8.
Hot fires read RED -> YELLOW -> WHITE (a hot 3.9um core saturates RED, then the
2.2um + 1.6um reflectance climb pushes G and B up so the very hottest pixels go
white). Water / cool land / cloud render dark / blue-grey.

UNITS WARNING (carried from the spike S.7 gotchas):
  - C07 is brightness TEMPERATURE in KELVIN (subtract 273.15 for the 0-60 C
    stretch). C06 / C05 are REFLECTANCE (0..1 factor, multiply by 100 for the %
    stretch). Mixing the units yields an all-dark or saturated image.
  - MCMIPC CMI bands are stored as scaled int16 with CF ``scale_factor`` /
    ``add_offset`` that rasterio's NETCDF driver does NOT auto-apply -- we apply
    them per band (the same lesson ``fetch_goes_satellite`` codifies).

Strategy:
  1. ``_list_archive_keys_in_window`` -- walk the ``ABI-L2-MCMIPC/<YYYY>/<DOY>/<HH>/``
     S3 partitions (JULIAN day-of-year) across the (start_utc, end_utc) window,
     collect every MCMIPC key whose ``_s<YYYYDOYHHMMSSf>`` start-time falls in the
     window, ordered ascending. Reuses ``fetch_goes_satellite``'s anonymous
     ``?list-type=2`` lister + ``_doy_hour`` + ``_KEY_START_TIME_RE``.
  2. ``_select_window_keys`` -- even-subsample to a frame cap (first + last kept;
     mirrors ``fetch_goes_animation._select_frame_indices`` /
     ``postprocess_flood._select_frame_time_indices``).
  3. Per frame: ONE ``read_through`` (independent cache key per timestamp) ->
     download the MCMIPC netCDF -> read C07/C06/C05 -> apply CF scaling -> reproject
     the geostationary fixed grid to EPSG:4326 over the AOI -> Fire-Temp composite
     -> 3-band uint8 RGB COG (``publish_layer``'s multiband passthrough renders it).
  4. Return an ordered ``list[LayerURI]`` in the SAME shape ``fetch_goes_animation``
     returns -- ``style_preset="goes_rgb_animation"``, the ``"GOES Fire Temperature
     (Archive) step <N> <ISO> (<SAT>)"`` name token, same bbox -- so Track A's
     composer and the web scrubber consume it UNCHANGED.

Honesty floor (the render-chokepoint norm): a window with no MCMIPC keys raises a
typed ``GOESArchiveEmptyError``; a frame whose AOI crop has no valid Fire-Temp
pixels is skipped, and a run that yields ZERO frames raises rather than emitting a
blank animation.

Cache key (per frame): SHA-256 over ``(bbox-6dp, product='fire_temperature',
satellite, ts_start, gamma)`` -- the per-frame start-time makes each key distinct.

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
from .fetch_goes_satellite import (
    _KEY_START_TIME_RE,
    _PRODUCT_PREFIX,
    _SATELLITE_BUCKETS,
    _doy_hour,
    _download_to_tempfile,
    _list_keys_for_prefix,
)
from ._satellite_slider import rgb_array_to_cog_bytes

__all__ = [
    "fetch_goes_archive_animation",
    "GOESArchiveError",
    "GOESArchiveInputError",
    "GOESArchiveBboxRequiredError",
    "GOESArchiveUpstreamError",
    "GOESArchiveEmptyError",
    "GOES_ARCHIVE_SATELLITES",
    "MAX_ARCHIVE_FRAMES",
    "FIRE_TEMP_BANDS",
    "FIRE_TEMP_RED_KELVIN_RANGE",
    "FIRE_TEMP_GREEN_REFL_MAX",
    "FIRE_TEMP_BLUE_REFL_MAX",
    "_parse_utc",
    "_key_start_datetime",
    "_select_window_keys",
    "_list_archive_keys_in_window",
    "_stretch_brightness_temp_red",
    "_stretch_reflectance",
    "_fire_temperature_rgb",
]

logger = logging.getLogger("grace2_agent.tools.fetch_goes_archive_animation")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class GOESArchiveError(RuntimeError):
    """Base class for fetch_goes_archive_animation failures."""

    error_code: str = "GOES_ARCHIVE_ERROR"
    retryable: bool = True


class GOESArchiveInputError(GOESArchiveError):
    """Invalid input (unknown satellite, bad window, bad bbox)."""

    error_code = "GOES_ARCHIVE_INPUT_INVALID"
    retryable = False


class GOESArchiveBboxRequiredError(GOESArchiveError):
    """bbox is required (a sector-wide archive animation would be enormous)."""

    error_code = "BBOX_REQUIRED"
    retryable = False


class GOESArchiveUpstreamError(GOESArchiveError):
    """S3 listing or netCDF download/parse failed."""

    error_code = "GOES_ARCHIVE_UPSTREAM_ERROR"
    retryable = True


class GOESArchiveEmptyError(GOESArchiveError):
    """The window matched no MCMIPC keys, or every frame crop was empty."""

    error_code = "GOES_ARCHIVE_EMPTY"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

#: Satellites with a full raw MCMIPC archive in the public S3 buckets. GOES-18 is
#: GOES-West (Utah / Nevada fire AOIs); GOES-19 is the GOES-East replacement;
#: GOES-16 is the historical East. All carry ABI-L2-MCMIPC.
GOES_ARCHIVE_SATELLITES = ("goes-16", "goes-18", "goes-19")

#: The three ABI CMI bands the Fire Temperature RGB composites (CONFIRMED from the
#: NOAA-NESDIS / CIRA Fire Temperature RGB Quick Guide). One MCMIPC netCDF carries
#: all 16 CMI bands so a single download yields all three.
FIRE_TEMP_BANDS = {
    "red": "CMI_C07",    # ABI band 7, 3.9um, brightness temperature (K)
    "green": "CMI_C06",  # ABI band 6, 2.2um, reflectance (0..1 factor)
    "blue": "CMI_C05",   # ABI band 5, 1.6um, reflectance (0..1 factor)
}

#: RED brightness-temperature stretch: 0-60 C == 273.15-333.15 K (gamma 1).
FIRE_TEMP_RED_KELVIN_RANGE = (273.15, 333.15)

#: GREEN reflectance stretch upper bound: 100 % == 1.0 reflectance factor.
FIRE_TEMP_GREEN_REFL_MAX = 1.0

#: BLUE reflectance stretch upper bound: 75 % == 0.75 reflectance factor.
FIRE_TEMP_BLUE_REFL_MAX = 0.75

#: Upper bound on emitted frames (mirrors fetch_goes_animation.MAX_ANIM_FRAMES /
#: postprocess_flood.MAX_FLOOD_FRAMES=144). A wider window even-subsamples down
#: (first + last kept). Overridable via env.
MAX_ARCHIVE_FRAMES: int = int(os.environ.get("GRACE2_MAX_ARCHIVE_FRAMES", "144"))

#: Output resolution (degrees) for the EPSG:4326 reproject (~2 km, matching the
#: ABI nominal sub-satellite resolution -- same as fetch_goes_satellite).
_OUT_RES_DEG = 0.02

#: Bbox quantization (6dp) for cache-key stability.
_BBOX_QUANTIZE_DP = 6

#: Shared style preset across every frame -- the SAME preset fetch_goes_animation
#: emits, so Track A + the web scrubber consume the archive frames UNCHANGED. A
#: 3-band RGB COG renders via publish_layer's multiband passthrough (no colormap).
_GOES_ARCHIVE_STYLE_PRESET = "goes_rgb_animation"

#: Product label for the LayerURI name. "(Archive)" distinguishes the raw-S3
#: historical path from the SLIDER recent path in the scrubber-group STEM, so the
#: two never collide into one group.
_PRODUCT_LABEL = "Fire Temperature (Archive)"


# ---------------------------------------------------------------------------
# AtomicToolMetadata.
# ---------------------------------------------------------------------------


def _build_metadata() -> AtomicToolMetadata:
    common = dict(
        name="fetch_goes_archive_animation",
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
# Time helpers.
# ---------------------------------------------------------------------------


def _parse_utc(value: Any) -> datetime:
    """Parse an ISO-8601 (or 'YYYY-MM-DD HH:MM') string / datetime -> aware UTC.

    Accepts a trailing 'Z', '+00:00', a space or 'T' separator, and a bare date.
    Raises ``GOESArchiveInputError`` for an unparseable value.
    """
    if isinstance(value, datetime):
        dt = value
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise GOESArchiveInputError(
            f"time must be an ISO-8601 string or datetime; got {value!r}"
        )
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
            raise GOESArchiveInputError(
                f"could not parse UTC time {value!r}; use ISO-8601 "
                "(e.g. '2026-06-22T13:30:00Z')"
            )
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _key_start_datetime(key: str) -> datetime | None:
    """Parse the ``_s<YYYYDOYHHMMSSf>`` start-time of an MCMIPC key -> aware UTC.

    The ABI naming convention is ``_s`` + 4-digit year + 3-digit day-of-year +
    2-digit hour + 2-digit minute + 2-digit second + 1-digit tenth-of-second
    (14 digits total). Returns ``None`` if the key has no recognizable start-time.
    """
    m = _KEY_START_TIME_RE.search(key)
    if not m:
        return None
    s = m.group(1)  # 14 digits: YYYYDDDHHMMSSf
    try:
        year = int(s[0:4])
        doy = int(s[4:7])
        hour = int(s[7:9])
        minute = int(s[9:11])
        second = int(s[11:13])
    except (ValueError, IndexError):
        return None
    try:
        base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
        return base.replace(hour=hour, minute=minute, second=second)
    except (ValueError, OverflowError):
        return None


def _iso_z(dt: datetime) -> str:
    """Render an aware UTC datetime as an ISO-8601 'Z' string (second precision)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Frame-list assembly (pure).
# ---------------------------------------------------------------------------


def _select_window_keys(keys: list[str], cap: int = MAX_ARCHIVE_FRAMES) -> list[str]:
    """Even-subsample an ASCENDING list of keys down to ``cap``, endpoints kept.

    Mirrors ``fetch_goes_animation._select_frame_indices`` /
    ``postprocess_flood._select_frame_time_indices``: when ``len(keys) <= cap`` the
    list is returned unchanged; otherwise an even ``linspace`` (rounded + unique)
    keeps the first + last and subsamples the middle. Logs a subsample. Pure.
    """
    n = len(keys)
    if n <= 0:
        return []
    if n <= cap:
        return list(keys)
    import numpy as np

    idx = np.linspace(0, n - 1, cap).round().astype(int)
    kept_idx = [int(i) for i in np.unique(idx)]
    logger.info(
        "fetch_goes_archive_animation: %d in-window MCMIPC keys exceed cap=%d; "
        "subsampling evenly to %d (first+last kept).",
        n,
        cap,
        len(kept_idx),
    )
    return [keys[i] for i in kept_idx]


def _hours_in_window(start_utc: datetime, end_utc: datetime) -> list[datetime]:
    """Return every top-of-hour datetime whose hour overlaps [start, end] (UTC).

    The MCMIPC S3 keys are partitioned by ``<YYYY>/<DOY>/<HH>/``; a frame at
    HH:MM lives under the HH partition, so we must list every hour partition the
    window touches (inclusive of the hour containing ``end_utc``).
    """
    start_h = start_utc.replace(minute=0, second=0, microsecond=0)
    out: list[datetime] = []
    cur = start_h
    # Cap the walk defensively so a malformed huge window cannot list forever.
    max_hours = 24 * 31  # one month of hour-partitions
    while cur <= end_utc and len(out) < max_hours:
        out.append(cur)
        cur = cur + timedelta(hours=1)
    return out


def _list_archive_keys_in_window(
    satellite: str,
    start_utc: datetime,
    end_utc: datetime,
    *,
    session: Any = None,
) -> list[tuple[datetime, str]]:
    """List MCMIPC ``(start_time, key)`` pairs in [start, end] from the S3 archive.

    Walks every ``ABI-L2-MCMIPC/<YYYY>/<DOY>/<HH>/`` partition the window touches
    (JULIAN day-of-year key layout), parses each key's ``_s<...>`` start-time, and
    keeps the ones with ``start_utc <= t <= end_utc``. Returns the pairs ORDERED
    ASCENDING by start time. Reuses ``fetch_goes_satellite``'s anonymous
    ``?list-type=2`` lister + ``_doy_hour``.

    Raises:
        ``GOESArchiveInputError``: unknown satellite.
        ``GOESArchiveUpstreamError``: every probed hour partition failed.
    """
    bucket = _SATELLITE_BUCKETS.get(satellite)
    if bucket is None:
        raise GOESArchiveInputError(
            f"unknown satellite={satellite!r}; allowed: {sorted(_SATELLITE_BUCKETS)}"
        )

    pairs: list[tuple[datetime, str]] = []
    hours = _hours_in_window(start_utc, end_utc)
    n_fail = 0
    last_exc: Exception | None = None
    for probe in hours:
        year, doy, hour = _doy_hour(probe)
        prefix = f"{_PRODUCT_PREFIX}/{year}/{doy:03d}/{hour:02d}/"
        try:
            keys = _list_keys_for_prefix(bucket, prefix, session=session)
        except Exception as exc:  # noqa: BLE001 -- per-hour failure tolerated
            n_fail += 1
            last_exc = exc
            logger.warning(
                "fetch_goes_archive_animation: listing prefix=%s failed: %s",
                prefix,
                exc,
            )
            continue
        for k in keys:
            # MCMIPC product only (the prefix already scopes it, but guard).
            if "MCMIPC" not in k:
                continue
            t = _key_start_datetime(k)
            if t is None:
                continue
            if start_utc <= t <= end_utc:
                pairs.append((t, k))

    # All probed hours failed (and there were hours to probe) -> upstream error.
    if not pairs and hours and n_fail == len(hours):
        raise GOESArchiveUpstreamError(
            f"every one of {n_fail} S3 hour-partition listings failed for "
            f"{satellite} in window {_iso_z(start_utc)}..{_iso_z(end_utc)}"
            + (f": {last_exc}" if last_exc else "")
        )

    # Sort ascending by start time; dedupe on start time (a scan can have a
    # mode-change duplicate key) keeping the first.
    pairs.sort(key=lambda p: (p[0], p[1]))
    deduped: list[tuple[datetime, str]] = []
    seen_ts: set[str] = set()
    for t, k in pairs:
        tag = _iso_z(t)
        if tag in seen_ts:
            continue
        seen_ts.add(tag)
        deduped.append((t, k))
    return deduped


# ---------------------------------------------------------------------------
# Fire Temperature band math (pure -- the testable core).
# ---------------------------------------------------------------------------


def _stretch_brightness_temp_red(bt_kelvin: Any) -> Any:
    """Stretch a C07 brightness-temperature (K) array to [0,1] RED per the recipe.

    Linear 273.15 K (0 C) -> 0.0, 333.15 K (60 C) -> 1.0, clipped to [0,1],
    gamma 1 (no exponent). NaN -> 0.0 (transparent / no-data reads dark).
    """
    import numpy as np

    lo, hi = FIRE_TEMP_RED_KELVIN_RANGE
    arr = np.asarray(bt_kelvin, dtype=np.float32)
    out = (arr - np.float32(lo)) / np.float32(hi - lo)
    out = np.clip(out, 0.0, 1.0)
    out = np.where(np.isfinite(out), out, 0.0)
    return out.astype(np.float32)


def _stretch_reflectance(refl: Any, refl_max: float) -> Any:
    """Stretch a reflectance (0..1 factor) array to [0,1] per ``refl_max``.

    Linear 0.0 -> 0.0, ``refl_max`` -> 1.0, clipped to [0,1], gamma 1. NaN -> 0.0.
    Used for GREEN (C06, refl_max=1.0 == 100 %) and BLUE (C05, refl_max=0.75 ==
    75 %).
    """
    import numpy as np

    arr = np.asarray(refl, dtype=np.float32)
    denom = max(1e-6, float(refl_max))
    out = arr / np.float32(denom)
    out = np.clip(out, 0.0, 1.0)
    out = np.where(np.isfinite(out), out, 0.0)
    return out.astype(np.float32)


def _fire_temperature_rgb(
    c07_bt_kelvin: Any,
    c06_reflectance: Any,
    c05_reflectance: Any,
) -> Any:
    """Composite the Fire Temperature RGB from the three CF-scaled CMI band arrays.

    R = C07 (3.9um) BT stretched 273.15-333.15 K.
    G = C06 (2.2um) reflectance stretched 0-100 %.
    B = C05 (1.6um) reflectance stretched 0-75 %.
    Each channel clipped to [0,1], gamma 1, scaled to 0-255 uint8. Returns a
    ``(3, H, W)`` uint8 array (band-first, the rasterio write order).

    Inputs must already carry physical units (CF scale_factor/add_offset applied)
    and be co-registered (same shape) -- the per-frame reproject upstream ensures
    that. Pure function (the testable Fire-Temp core).
    """
    import numpy as np

    red = _stretch_brightness_temp_red(c07_bt_kelvin)
    green = _stretch_reflectance(c06_reflectance, FIRE_TEMP_GREEN_REFL_MAX)
    blue = _stretch_reflectance(c05_reflectance, FIRE_TEMP_BLUE_REFL_MAX)
    if not (red.shape == green.shape == blue.shape):
        raise GOESArchiveUpstreamError(
            f"Fire Temperature band shapes differ: R={red.shape} G={green.shape} "
            f"B={blue.shape}; bands must be co-registered before compositing"
        )
    rgb = np.stack([red, green, blue], axis=0)  # (3, H, W) in [0,1]
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# netCDF band read + CF scaling + reproject (the I/O core).
# ---------------------------------------------------------------------------


def _reproject_fire_temperature(
    nc_path: str,
    bbox: tuple[float, float, float, float],
) -> Any:
    """Read C07/C06/C05 from an MCMIPC netCDF, CF-scale + reproject each to EPSG:4326 over ``bbox``, composite Fire Temperature.

    For each of the three bands:
      1. Read CF ``scale_factor`` / ``add_offset`` / ``_FillValue`` (netCDF4).
      2. Read the raw int16 DN + inherit the geostationary CRS (rasterio NETCDF
         subdataset), warp to a regular EPSG:4326 grid over ``bbox`` at
         ``_OUT_RES_DEG`` with nearest-neighbor (clean int16 fill propagation).
      3. Apply ``scale_factor * DN + add_offset`` -> physical units (K for C07,
         reflectance for C06/C05), masking the warp sentinel + CF fill + out-of-
         valid-range DN to NaN.
    Then composite the three physical-unit arrays with ``_fire_temperature_rgb``.

    Returns a ``(3, H, W)`` uint8 RGB array plus the output ``(transform, W, H)``:
    ``(rgb, transform, width, height)``.

    Raises:
        ``GOESArchiveUpstreamError``: rasterio/netCDF open / reproject failure.
        ``GOESArchiveEmptyError``: bbox produces no valid pixels (off the disk).
    """
    import numpy as np
    import netCDF4  # type: ignore[import-not-found]
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    min_lon, min_lat, max_lon, max_lat = bbox
    width = max(1, int(math.ceil((max_lon - min_lon) / _OUT_RES_DEG)))
    height = max(1, int(math.ceil((max_lat - min_lat) / _OUT_RES_DEG)))
    out_transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    warp_sentinel = int(np.iinfo(np.int16).min)  # -32768, outside the valid range

    def _warp_band(variable: str) -> Any:
        # CF attrs.
        try:
            with netCDF4.Dataset(nc_path) as ncds:
                if variable not in ncds.variables:
                    raise GOESArchiveUpstreamError(
                        f"MCMIPC netCDF {nc_path} has no variable {variable!r}; "
                        f"available CMI vars: "
                        f"{[v for v in ncds.variables if v.startswith('CMI_')]}"
                    )
                ncvar = ncds.variables[variable]
                scale_factor = float(getattr(ncvar, "scale_factor", 1.0))
                add_offset = float(getattr(ncvar, "add_offset", 0.0))
                fill_raw = getattr(ncvar, "_FillValue", None)
                fill_value = float(fill_raw) if fill_raw is not None else None
        except GOESArchiveError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GOESArchiveUpstreamError(
                f"netCDF metadata read failed for {variable} in {nc_path}: {exc}"
            ) from exc

        sub_uri = f'NETCDF:"{nc_path}":{variable}'
        try:
            src = rasterio.open(sub_uri)
        except Exception as exc:  # noqa: BLE001
            raise GOESArchiveUpstreamError(
                f"rasterio could not open netCDF subdataset {sub_uri}: {exc}"
            ) from exc
        try:
            if src.crs is None:
                raise GOESArchiveUpstreamError(
                    f"netCDF subdataset {variable} has no CRS metadata; cannot "
                    "reproject (expected the ABI geostationary projection)"
                )
            warped = np.full((height, width), warp_sentinel, dtype=np.int16)
            src_nodata = src.nodata if src.nodata is not None else fill_value
            try:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=warped,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=out_transform,
                    dst_crs="EPSG:4326",
                    resampling=Resampling.nearest,
                    src_nodata=src_nodata,
                    dst_nodata=warp_sentinel,
                )
            except Exception as exc:  # noqa: BLE001
                raise GOESArchiveUpstreamError(
                    f"rasterio reproject failed for {variable}: {exc}"
                ) from exc
        finally:
            src.close()

        # CF unscale -> physical units; mask sentinel + CF fill + out-of-range DN.
        phys = warped.astype(np.float32) * np.float32(scale_factor) + np.float32(add_offset)
        mask = warped == warp_sentinel
        if fill_value is not None:
            mask |= warped == int(fill_value)
        mask |= (warped < 0) | (warped > 4095)  # CF valid_range [0, 4095]
        phys[mask] = np.nan
        return phys

    c07 = _warp_band(FIRE_TEMP_BANDS["red"])
    c06 = _warp_band(FIRE_TEMP_BANDS["green"])
    c05 = _warp_band(FIRE_TEMP_BANDS["blue"])

    # Honesty floor: refuse an all-NaN crop (bbox missed the disk / sector).
    if not (
        np.isfinite(c07).any() or np.isfinite(c06).any() or np.isfinite(c05).any()
    ):
        raise GOESArchiveEmptyError(
            f"bbox={bbox} produces no valid Fire Temperature pixels "
            "(likely outside the CONUS sector or behind the disk limb)"
        )

    rgb = _fire_temperature_rgb(c07, c06, c05)
    if not rgb.any():
        raise GOESArchiveEmptyError(
            f"bbox={bbox} Fire Temperature composite is all-black "
            "(no thermal / reflectance signal in the AOI crop)"
        )
    return rgb, out_transform, width, height


# ---------------------------------------------------------------------------
# Per-frame fetch (the read_through fetch_fn).
# ---------------------------------------------------------------------------


def _fetch_archive_frame_cog_bytes(
    satellite: str,
    key: str,
    bbox: tuple[float, float, float, float],
) -> bytes:
    """Download one MCMIPC netCDF -> Fire-Temp composite -> 3-band RGB COG bytes."""
    bucket = _SATELLITE_BUCKETS[satellite]
    url = f"https://{bucket}.s3.amazonaws.com/{key}"
    nc_path = _download_to_tempfile(url)
    try:
        rgb, transform, width, height = _reproject_fire_temperature(nc_path, bbox)
        return rgb_array_to_cog_bytes(rgb, transform, width, height)
    finally:
        try:
            os.unlink(nc_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# bbox helpers.
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: Any) -> tuple[float, float, float, float]:
    if bbox is None:
        raise GOESArchiveBboxRequiredError(
            "bbox is required for fetch_goes_archive_animation (a sector-wide raw "
            "MCMIPC animation is enormous); pass (min_lon, min_lat, max_lon, "
            "max_lat)."
        )
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        raise GOESArchiveInputError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    vals = tuple(float(v) for v in bbox)
    if not all(math.isfinite(v) for v in vals):
        raise GOESArchiveInputError(f"bbox contains non-finite values: {bbox!r}")
    min_lon, min_lat, max_lon, max_lat = vals
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise GOESArchiveInputError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise GOESArchiveInputError(f"bbox lat out of [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise GOESArchiveInputError(
            f"bbox is degenerate (min<max on both axes): {bbox!r}"
        )
    return (min_lon, min_lat, max_lon, max_lat)


def _round_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(round(v, _BBOX_QUANTIZE_DP) for v in bbox)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(
    _METADATA,
    # readOnlyHint=True, openWorldHint=True (anonymous NOAA S3),
    # destructiveHint=False, idempotentHint=True (per-frame cache dedupes).
    open_world_hint=True,
)
def fetch_goes_archive_animation(
    bbox: tuple[float, float, float, float],
    satellite: str = "goes-18",
    start_utc: str | None = None,
    end_utc: str | None = None,
    step_minutes: int = 5,
    band: str = "fire_temperature",
    # job-0164: absorb LLM-invented kwargs.
    **_extra_ignored: Any,
) -> list[LayerURI]:
    """Build a HISTORICAL GOES Fire Temperature animation from the RAW noaa-goes18 S3 archive (any past date).

    **What it does:** Reads the RAW ``ABI-L2-MCMIPC`` netCDFs from the public
    ``noaa-goes18`` S3 bucket (a FULL historical archive, anonymous / no key)
    across a UTC time window for ANY date -- including the distant past -- and
    composites the NOAA-NESDIS / CIRA **Fire Temperature** RGB per frame (R = ABI
    C07 3.9um brightness-temp 0-60 C, G = C06 2.2um reflectance 0-100 %, B = C05
    1.6um reflectance 0-75 %, gamma 1). Returns an ORDERED list of per-frame
    EPSG:4326 RGB COGs over the AOI -- one frame per CONUS 5-minute scan -- in the
    SAME shape ``fetch_goes_animation`` returns, so the workflow composer and the
    web scrubber animate them UNCHANGED.

    This is the HISTORICAL companion to ``fetch_goes_animation``: the SLIDER tiles
    that tool uses only serve ~100 RECENT frames (no archive), and their pre-
    rendered Fire Temperature had a zoom-coverage gap. This tool composites Fire
    Temperature from the raw bands and reaches any archived date.

    **When to use:**
    - "Animate the GOES Fire Temperature loop for a fire on a PAST date" (e.g.
      "recreate the Iron Fire GOES animation for 2026-06-22"); any historical
      intra-day GOES Fire Temperature timelapse.
    - When ``fetch_goes_animation`` returns no frames because the requested window
      is older than the SLIDER recent-frame horizon.

    **When NOT to use:**
    - A single most-recent frame (use ``fetch_goes_satellite``).
    - A GeoColor loop or a near-real-time recent loop (use
      ``fetch_goes_animation`` / ``fetch_goes_blend_animation`` -- GeoColor is a
      proprietary CIRA product not reconstructable from raw bands here).
    - A multi-day polar VIIRS timelapse (use ``fetch_viirs_day_fire``).

    **Parameters:**
    - ``bbox`` (tuple): ``(min_lon, min_lat, max_lon, max_lat)`` EPSG:4326.
      Required. Example (Utah fire cluster): ``(-114.05, 37.0, -109.04, 42.0)``.
    - ``satellite`` (str, default ``"goes-18"``): ``"goes-18"`` (West / Utah-
      Nevada fires), ``"goes-19"`` (East), or ``"goes-16"`` (historical East).
    - ``start_utc`` / ``end_utc`` (str): ISO-8601 UTC window bounds (e.g.
      ``"2026-06-22T13:30:00Z"`` .. ``"2026-06-22T20:00:00Z"``). When omitted, the
      most-recent ~6.5h is used. Works for ANY past date in the archive.
    - ``step_minutes`` (int, default 5): informational; the CONUS archive is
      natively 5-minute. Frames are taken at the archived scan times in the
      window, then even-subsampled to the frame cap.
    - ``band`` (str, default ``"fire_temperature"``): only ``"fire_temperature"``
      is supported on the raw-archive path.

    **Returns:** an ORDERED ``list[LayerURI]`` (ascending UTC). Each is a 3-band
    uint8 RGB COG (``layer_type="raster"``, ``role="context"``,
    ``style_preset="goes_rgb_animation"``, same ``bbox``) whose ``name`` is
    ``"GOES Fire Temperature (Archive) step <N> <ISO> (<SAT>)"`` -- the SAME
    scrubber-group contract ``fetch_goes_animation`` emits: the ``step <N>`` token
    is the monotonic frame value the web ``detectSequentialGroups`` parser keys on,
    the product label keeps the archive stem distinct, and the ISO valid-time is
    the per-frame display label.

    NOTE: an AOI / window with no archived frames raises a typed error (honesty
    floor) -- it never emits a blank animation.

    **Cross-tool dependencies:**
    - Upstream: ``fetch_wfigs_incident`` (the AOI bbox + the window floor).
    - Pairs with: ``fetch_firms_active_fire`` (historical-date hot-pixel overlay)
      + ``fetch_nifc_fire_perimeters`` (perimeter overlay).
    - Driven by: ``run_model_satellite_fire_animation`` (the historical GOES path).
    """
    q_bbox = _round_bbox(_validate_bbox(bbox))
    if satellite not in GOES_ARCHIVE_SATELLITES:
        raise GOESArchiveInputError(
            f"unknown satellite={satellite!r}; allowed: "
            f"{list(GOES_ARCHIVE_SATELLITES)}"
        )
    if band != "fire_temperature":
        raise GOESArchiveInputError(
            f"unknown band/product={band!r}; the raw-archive path supports only "
            "'fire_temperature' (GeoColor is a proprietary CIRA product -- use "
            "fetch_goes_animation for the recent GeoColor loop)"
        )

    # Resolve the window. Default: most-recent ~6.5h ending now (UTC).
    now = datetime.now(timezone.utc)
    end_dt = _parse_utc(end_utc) if end_utc else now
    start_dt = _parse_utc(start_utc) if start_utc else (end_dt - timedelta(hours=6, minutes=30))
    if start_dt >= end_dt:
        raise GOESArchiveInputError(
            f"start_utc ({start_dt.isoformat()}) must be before end_utc "
            f"({end_dt.isoformat()})"
        )

    # 1. List the in-window MCMIPC keys + even-subsample to the frame cap.
    pairs = _list_archive_keys_in_window(satellite, start_dt, end_dt)
    if not pairs:
        raise GOESArchiveEmptyError(
            f"no MCMIPC frames in the noaa-{satellite.replace('-', '')} archive for "
            f"window {_iso_z(start_dt)}..{_iso_z(end_dt)} -- the date may pre-date "
            f"the {satellite} operational record or fall in an ingest gap"
        )
    keys_only = [k for _, k in pairs]
    kept_keys = set(_select_window_keys(keys_only, cap=MAX_ARCHIVE_FRAMES))
    frames = [(t, k) for (t, k) in pairs if k in kept_keys]

    sat_label = satellite.upper()

    # 2. Per-frame fetch (one read_through each -> independent cache key).
    layers: list[LayerURI] = []
    n_empty = 0
    last_err: Exception | None = None
    for frame_no, (t, key) in enumerate(frames, start=1):
        iso = _iso_z(t)
        ts_tag = t.strftime("%Y%m%d%H%M%S")
        params = {
            "bbox": list(q_bbox),
            "product": "fire_temperature",
            "satellite": satellite,
            "ts_start": ts_tag,
            "gamma": 1,
        }
        try:
            result = read_through(
                metadata=_METADATA,
                params=params,
                ext="tif",
                fetch_fn=lambda s=satellite, k=key: _fetch_archive_frame_cog_bytes(
                    s, k, q_bbox
                ),
            )
        except GOESArchiveEmptyError as exc:
            n_empty += 1
            last_err = exc
            logger.warning(
                "fetch_goes_archive_animation: empty frame ts=%s skipped (%s)",
                iso,
                exc,
            )
            continue
        except GOESArchiveUpstreamError as exc:
            n_empty += 1
            last_err = exc
            logger.warning(
                "fetch_goes_archive_animation: frame ts=%s upstream-failed (%s)",
                iso,
                exc,
            )
            continue
        assert result.uri is not None
        # NAME token = "GOES Fire Temperature (Archive) step <N> <ISO> (<SAT>)".
        # The "step <N>" token is the MONOTONIC frame value the web
        # detectSequentialGroups parser keys on; the "(Archive)" product label
        # keeps the raw-S3 historical path's stem distinct from the SLIDER recent
        # path; the ISO valid-time is the per-frame display label.
        layers.append(
            LayerURI(
                layer_id=f"goes-arch-firetemp-{ts_tag}-{q_bbox[0]:.3f}-{q_bbox[1]:.3f}",
                name=f"GOES {_PRODUCT_LABEL} step {frame_no} {iso} ({sat_label})",
                layer_type="raster",
                uri=result.uri,
                style_preset=_GOES_ARCHIVE_STYLE_PRESET,
                role="context",
                units=None,
                bbox=q_bbox,
            )
        )

    # Honesty floor: a run that produced NO frames is not success.
    if not layers:
        raise GOESArchiveEmptyError(
            f"every one of {len(frames)} archive Fire Temperature frames was "
            f"empty/failed for {satellite} over the AOI"
            + (f": {last_err}" if last_err else "")
        )
    logger.info(
        "fetch_goes_archive_animation: %d Fire Temperature frames (%d empty "
        "skipped) for %s archive window %s..%s",
        len(layers),
        n_empty,
        satellite,
        _iso_z(start_dt),
        _iso_z(end_dt),
    )
    return layers
