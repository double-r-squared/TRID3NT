"""``fetch_goes_active_fire`` -- standalone GOES split-window active-fire detector.

Exposes the Matson-Dozier C07-vs-C13 split-window active-fire discriminator as a
PROPER registered atomic tool. The discriminator itself lives in
``fetch_goes_archive_animation._detect_active_fire_mask`` (where the
``fire_hotspots`` band uses it); this tool surfaces it on its OWN so the agent can
discover + run "detect the active fire in this AOI" without composing the whole
GOES animation pipeline.

It fetches the most-recent (or in-window) raw ``ABI-L2-MCMIPC`` frame(s) from the
public ``noaa-goes18`` S3 archive (anonymous / no key), runs the split-window
detector, and returns the active-fire hotspots as TRANSPARENT RGBA hotspot
``LayerURI`` raster(s) -- the SAME ``fire_hotspots`` composite path the archive
animation emits -- so a single detection is a usable map overlay.

Split-window active-fire discriminator (Matson & Dozier 1981; MODIS MOD14 /
VIIRS active-fire heritage):
  A pixel is flagged active-fire when BOTH hold:
    * its 3.9um brightness temperature (ABI C07) is hot
      (``C07 >= bt_c07_min_k``), AND
    * the 3.9um - 10.3um brightness-temperature difference (C07 - C13) is large
      (``(C07 - C13) >= bt_diff_min_k``).
  The 3.9um channel saturates over a sub-pixel fire far more than the 10.3um
  longwave window channel, so a big positive split-window difference isolates
  combustion from uniformly warm bare land (hot in BOTH channels -> SMALL
  difference). The thresholds are tunable (the shared defaults 320 K / 10 K) and
  flag a small active-fire fraction over a real fire AOI rather than warm land.

This is the EXPLICITLY-DEFINED (Class B) tool surface for the discriminator; it
reuses the shared archive band-read core + the hotspot composite so it does NOT
duplicate any netCDF I/O or detection logic.

ASCII only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through
from .fetch_goes_archive_animation import (
    FIRE_BT_C07_MIN_K,
    FIRE_BT_DIFF_MIN_K,
    GOES_ARCHIVE_SATELLITES,
    GOESArchiveEmptyError,
    GOESArchiveInputError,
    GOESArchiveUpstreamError,
    _OUT_RES_DEG,
    _fetch_archive_frame_cog_bytes,
    _iso_z,
    _list_archive_keys_in_window,
    _parse_utc,
    _round_bbox,
    _select_window_keys,
    _validate_bbox,
)
# Shared satellite-identifier normalizer (base GOES module; acyclic -- it imports
# none of the siblings). Canonicalizes every spelling (GOES-18/goes18/G18/"GOES
# West"/18) to the goes-NN token, so the membership check below sees the SAME
# canonical form GOES_ARCHIVE_SATELLITES holds instead of rejecting valid birds.
from .fetch_goes_satellite import _normalize_satellite

__all__ = [
    "fetch_goes_active_fire",
    "MAX_ACTIVE_FIRE_FRAMES",
]

logger = logging.getLogger("grace2_agent.tools.fetch_goes_active_fire")

#: Cap on emitted detection frames. The detector is most useful as a single most-
#: recent overlay or a short loop; a wider window even-subsamples down to this cap.
MAX_ACTIVE_FIRE_FRAMES = 24

#: Style preset for the transparent RGBA hotspot overlay (matches the archive
#: animation's hotspot band so the web client composites the alpha identically).
_HOTSPOT_STYLE_PRESET = "goes_fire_hotspots_rgba"

#: Product label / id slug for the LayerURI.
_PRODUCT_LABEL = "GOES Active Fire"
_ID_TAG = "goes-activefire"


def _build_metadata() -> AtomicToolMetadata:
    common = dict(
        name="fetch_goes_active_fire",
        ttl_class="dynamic-1h",
        source_class="goes_animation",
        cacheable=True,
    )
    try:
        return AtomicToolMetadata(**common, supports_global_query=False)  # type: ignore[call-arg]
    except Exception:
        return AtomicToolMetadata(**common)


_METADATA = _build_metadata()


@register_tool(
    _METADATA,
    # readOnlyHint=True, openWorldHint=True (anonymous NOAA S3),
    # destructiveHint=False, idempotentHint=True (per-frame cache dedupes).
    open_world_hint=True,
)
def fetch_goes_active_fire(
    bbox: tuple[float, float, float, float],
    satellite: str = "goes-18",
    start_utc: str | None = None,
    end_utc: str | None = None,
    bt_c07_min_k: float | None = None,
    bt_diff_min_k: float | None = None,
    # job-0164: absorb LLM-invented kwargs.
    **_extra_ignored: Any,
) -> list[LayerURI]:
    """GOES active-fire detection RASTER frames via the Matson-Dozier split-window discriminator (auto-render). [fire-animation | geostationary raster]

    Fetches raw GOES ABI-L2-MCMIPC frame(s) for the AOI + window from the public
    noaa-goes18 S3 archive (anonymous), runs the C07(3.9um)-vs-C13(10.3um)
    split-window active-fire detector on each, and returns the flagged hot pixels
    as TRANSPARENT RGBA hotspot raster LayerURI(s) -- opaque only where fire is
    detected, so they overlay a basemap / true-color frame directly.

    Use this when:
    - "Where is the active fire in this AOI right now / on this date?" as a
      geostationary hot-pixel overlay (single frame or a short loop).
    - The hot-pixel detection step feeding a perimeter / spread analysis.

    Do NOT use this for:
    - A scrubbable true-color / Fire-Temperature loop -- use
      fetch_goes_animation or fetch_goes_archive_animation (matching band).
    - A polar (VIIRS) multi-day fire loop -- use fetch_viirs_day_fire.
    - Discrete active-fire POINTS -- use fetch_firms_active_fire.
    - Fire perimeters -- use fetch_nifc_fire_perimeters.
    - Resolving a fire by NAME -- use fetch_wfigs_incident.

    Honesty: a window with no archived frames OR no detected hot pixels raises a
    typed error -- it never emits a blank "fire" overlay.

    Returns an ordered list[LayerURI] of transparent RGBA hotspot rasters
    (ascending UTC) named "GOES Active Fire step <N> <ISO> (<SAT>)".

    The discriminator (Matson & Dozier 1981; MODIS MOD14 / VIIRS heritage): a
    pixel is flagged when its 3.9um brightness temperature is hot AND the
    3.9um-10.3um difference is large -- the 3.9um channel saturates over a
    sub-pixel fire while uniformly warm bare land stays small-difference.

    Parameters:
    - bbox (tuple): (min_lon, min_lat, max_lon, max_lat) EPSG:4326. Required.
    - satellite (str, default "goes-18"): GOES bird; accepts GOES-18/goes18/G18/
      "GOES West"/18 spellings (also goes-19 East, goes-16 historical East).
    - start_utc / end_utc (ISO-8601 UTC): window; default = most-recent ~20 min.
    - bt_c07_min_k (float, default 320 K): 3.9um brightness-temperature floor.
    - bt_diff_min_k (float, default 10 K): minimum 3.9um-10.3um split-window
      difference (the bare-warm-land discriminator).

    Upstream: fetch_wfigs_incident (AOI + window floor). Pairs with
    fetch_goes_animation (the true-color base under the hotspots).
    """
    q_bbox = _round_bbox(_validate_bbox(bbox))
    # Normalize-then-validate: canonicalize GOES-18/goes18/G18/"GOES West"/18 to
    # the goes-NN token BEFORE it is used to build any bucket/key/path (it feeds
    # the archive listing + every cache-key param below). A truly-unknown bird
    # fails LOUD via the shared normalizer; a real GOES bird this tool does not
    # serve still raises THIS tool's own GOESArchiveInputError (no base-error leak).
    satellite = _normalize_satellite(satellite)
    if satellite not in GOES_ARCHIVE_SATELLITES:
        raise GOESArchiveInputError(
            f"unknown satellite={satellite!r}; allowed: "
            f"{list(GOES_ARCHIVE_SATELLITES)}"
        )
    af_c07 = float(bt_c07_min_k) if bt_c07_min_k is not None else FIRE_BT_C07_MIN_K
    af_diff = float(bt_diff_min_k) if bt_diff_min_k is not None else FIRE_BT_DIFF_MIN_K

    # Resolve the window. Default: most-recent ~20 min ending now (UTC) -- a
    # single detection, not a long loop.
    now = datetime.now(timezone.utc)
    end_dt = _parse_utc(end_utc) if end_utc else now
    start_dt = _parse_utc(start_utc) if start_utc else (end_dt - timedelta(minutes=20))
    if start_dt >= end_dt:
        raise GOESArchiveInputError(
            f"start_utc ({start_dt.isoformat()}) must be before end_utc "
            f"({end_dt.isoformat()})"
        )

    pairs = _list_archive_keys_in_window(satellite, start_dt, end_dt)
    if not pairs:
        raise GOESArchiveEmptyError(
            f"no MCMIPC frames in the noaa-{satellite.replace('-', '')} archive for "
            f"window {_iso_z(start_dt)}..{_iso_z(end_dt)} -- the date may pre-date "
            f"the {satellite} operational record or fall in an ingest gap"
        )
    keys_only = [k for _, k in pairs]
    kept_keys = set(_select_window_keys(keys_only, cap=MAX_ACTIVE_FIRE_FRAMES))
    frames = [(t, k) for (t, k) in pairs if k in kept_keys]

    sat_label = satellite.upper()
    layers: list[LayerURI] = []
    n_empty = 0
    last_err: Exception | None = None
    for frame_no, (t, key) in enumerate(frames, start=1):
        iso = _iso_z(t)
        ts_tag = t.strftime("%Y%m%d%H%M%S")
        params = {
            "bbox": list(q_bbox),
            "product": "fire_hotspots",
            "satellite": satellite,
            "ts_start": ts_tag,
            "bt_c07_min_k": round(af_c07, 3),
            "bt_diff_min_k": round(af_diff, 3),
            "tool": "fetch_goes_active_fire",
        }
        try:
            result = read_through(
                metadata=_METADATA,
                params=params,
                ext="tif",
                # Reuse the archive module's fire_hotspots composite path (shared
                # band-read core + split-window detector + RGBA writer). MAIN
                # signature: positional (satellite, key, bbox, band) then the two
                # thresholds; res_deg defaults to _OUT_RES_DEG (the 2 km thermal
                # grid the detector runs on).
                fetch_fn=lambda s=satellite, k=key: _fetch_archive_frame_cog_bytes(
                    s, k, q_bbox, "fire_hotspots", af_c07, af_diff, _OUT_RES_DEG
                ),
            )
        except GOESArchiveEmptyError as exc:
            # No hot pixels in this frame -> skip it (not every scan has fire).
            n_empty += 1
            last_err = exc
            logger.info(
                "fetch_goes_active_fire: no hot pixels ts=%s skipped (%s)", iso, exc
            )
            continue
        except GOESArchiveUpstreamError as exc:
            n_empty += 1
            last_err = exc
            logger.warning(
                "fetch_goes_active_fire: frame ts=%s upstream-failed (%s)", iso, exc
            )
            continue
        assert result.uri is not None
        layers.append(
            LayerURI(
                layer_id=f"{_ID_TAG}-{ts_tag}-{q_bbox[0]:.3f}-{q_bbox[1]:.3f}",
                name=f"{_PRODUCT_LABEL} step {frame_no} {iso} ({sat_label})",
                layer_type="raster",
                uri=result.uri,
                style_preset=_HOTSPOT_STYLE_PRESET,
                role="context",
                units=None,
                bbox=q_bbox,
            )
        )

    if not layers:
        raise GOESArchiveEmptyError(
            f"the split-window active-fire detector flagged no hot pixels in any of "
            f"{len(frames)} {satellite} frames over the AOI "
            f"(thresholds C07>={af_c07}K, diff>={af_diff}K)"
            + (f": {last_err}" if last_err else "")
        )
    logger.info(
        "fetch_goes_active_fire: %d hotspot frame(s) (%d empty/failed) for %s "
        "window %s..%s",
        len(layers),
        n_empty,
        satellite,
        _iso_z(start_dt),
        _iso_z(end_dt),
    )
    return layers
