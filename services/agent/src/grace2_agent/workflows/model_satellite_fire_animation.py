"""``model_satellite_fire_animation`` workflow -- satellite fire-animation composer (fire demos S5/J5, generalized).

ONE generalized composer for BOTH fire-animation demos (GOES geostationary +
JPSS/VIIRS polar). It chains:

    fetch_wfigs_incident(name [, state])           -> authoritative point + bbox
      -> derive the AOI bbox + the (start_utc, end_utc) window
      -> peek the SLIDER frame list (NO imagery fetched yet)
      -> STOP at a bbox/window REVIEW gate (review-gated, like
         model_news_event_ingest): return the AOI bbox + the planned frame list
         + a human-readable summary so the user can SEE + ADJUST the bbox and the
         window BEFORE all frames are fetched (the #154 confirm / granularity-
         gate philosophy, applied at the workflow layer).
    -- on confirm=True --
      -> dispatch the RIGHT imagery fetcher per product via the TOOL_REGISTRY
         (fetch_goes_animation for GOES geostationary, fetch_viirs_day_fire for
         JPSS polar), each run in asyncio.to_thread (NEVER block the asyncio loop)
      -> the fetchers already emit per-frame LayerURIs in the postprocess_flood
         SHAPE (distinct keys + shared style_preset + a '<PRODUCT> <ISO-time>
         (<sat>)' NAME token + identical bbox), so detectSequentialGroups +
         SequenceScrubber animate them with NO web change
      -> overlay fetch_firms_active_fire (historical date) + fetch_nifc_fire_
         perimeters as static co-registered layers
      -> publish every layer via publish_layer (TiTiler) in asyncio.to_thread

Honesty floor: a run that produced NO imagery frames does NOT report status=ok --
it returns ``status="empty"`` with an honest message. The imagery is the real
CIRA SLIDER product at the real cadence; georeferencing is the approximate
sector-extent mapping documented in tools/_satellite_slider.py.

Registry discipline (kickoff hard rule): every atomic tool is dispatched via
``TOOL_REGISTRY[name].fn`` -- never imported and called directly.

ASCII only.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from ..tools import TOOL_REGISTRY, register_tool

if TYPE_CHECKING:
    from ..pipeline_emitter import PipelineEmitter

__all__ = [
    "model_satellite_fire_animation",
    "run_model_satellite_fire_animation",
    "SatelliteFireAnimationError",
    "SatelliteFireAnimationInputError",
    "SUPPORTED_PRODUCTS",
    "GOES_PRODUCTS",
    "VIIRS_PRODUCTS",
    "_product_to_fetcher",
    "_default_window_for_product",
    "_compose_review_text",
]

logger = logging.getLogger("grace2_agent.workflows.model_satellite_fire_animation")


# --------------------------------------------------------------------------- #
# Typed errors (FR-AS-11)
# --------------------------------------------------------------------------- #


class SatelliteFireAnimationError(RuntimeError):
    """Base class for model_satellite_fire_animation failures."""

    error_code: str = "SAT_FIRE_ANIM_ERROR"
    retryable: bool = False


class SatelliteFireAnimationInputError(SatelliteFireAnimationError):
    """Caller passed a bad product / window / incident name."""

    error_code = "SAT_FIRE_ANIM_INPUT_INVALID"
    retryable = False


# --------------------------------------------------------------------------- #
# Product routing
# --------------------------------------------------------------------------- #

#: GOES geostationary products -> fetch_goes_animation.
GOES_PRODUCTS: tuple[str, ...] = ("geocolor", "fire_temperature")

#: JPSS/VIIRS polar products -> fetch_viirs_day_fire.
VIIRS_PRODUCTS: tuple[str, ...] = ("day_fire",)

SUPPORTED_PRODUCTS: tuple[str, ...] = GOES_PRODUCTS + VIIRS_PRODUCTS


def _product_to_fetcher(product: str) -> str:
    """Map a product to the registered imagery-fetcher tool name.

    GOES geostationary products (geocolor / fire_temperature) route to
    ``fetch_goes_animation``; the JPSS/VIIRS polar Day Fire product routes to
    ``fetch_viirs_day_fire``. Raises ``SatelliteFireAnimationInputError`` for an
    unknown product.
    """
    if product in GOES_PRODUCTS:
        return "fetch_goes_animation"
    if product in VIIRS_PRODUCTS:
        return "fetch_viirs_day_fire"
    raise SatelliteFireAnimationInputError(
        f"unknown product={product!r}; allowed: {list(SUPPORTED_PRODUCTS)}"
    )


def _is_polar_product(product: str) -> bool:
    return product in VIIRS_PRODUCTS


# --------------------------------------------------------------------------- #
# Window derivation
# --------------------------------------------------------------------------- #


def _parse_utc(value: Any) -> datetime | None:
    """Parse an ISO-8601 string / datetime -> aware UTC, or None for a falsy value."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(str(value).strip().replace(" ", "T", 1), fmt)
                break
            except ValueError:
                continue
        else:
            raise SatelliteFireAnimationInputError(
                f"could not parse UTC time {value!r}; use ISO-8601 "
                "(e.g. '2026-06-22T13:30:00Z')"
            )
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _default_window_for_product(
    product: str,
    discovery_iso: str | None,
    end_utc: datetime | None,
) -> tuple[datetime, datetime]:
    """Derive a (start, end) window from the product family + the discovery floor.

    - GOES (intra-day): a ~6.5h window ending at ``end`` (default: the discovery
      day's ~20:00Z, else now), the CIRA loop length.
    - VIIRS (multi-day): a 4-day window ending at ``end`` (default: now).

    The WFIGS FireDiscoveryDateTime, when present, is the sanity floor: the start
    never precedes it. ``end`` defaults to now when unspecified.
    """
    now = datetime.now(timezone.utc)
    end = end_utc or now
    if _is_polar_product(product):
        start = end - timedelta(days=4)
    else:
        start = end - timedelta(hours=6, minutes=30)
    disc = _parse_utc(discovery_iso) if discovery_iso else None
    if disc is not None and start < disc:
        start = disc
    if start >= end:
        # Degenerate floor (discovery after end): widen the end past the floor.
        end = start + (timedelta(days=4) if _is_polar_product(product) else timedelta(hours=6, minutes=30))
    return start, end


# --------------------------------------------------------------------------- #
# Review-text composition (deterministic)
# --------------------------------------------------------------------------- #


def _compose_review_text(
    incident_name: str,
    bbox: tuple[float, float, float, float],
    products: list[str],
    start: datetime,
    end: datetime,
    frame_counts: dict[str, int],
) -> str:
    """Build the human-readable bbox/window REVIEW summary (deterministic)."""
    lines: list[str] = []
    lines.append(f"Satellite fire-animation plan -- {incident_name}")
    lines.append(
        f"AOI bbox: ({bbox[0]:.4f}, {bbox[1]:.4f}, {bbox[2]:.4f}, {bbox[3]:.4f}) EPSG:4326"
    )
    lines.append(
        f"Time window (UTC): {start.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"-> {end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    for product in products:
        n = frame_counts.get(product, 0)
        lines.append(f"  - {product}: {n} frame(s) planned")
    lines.append(
        "Review the AOI bbox and the time window before all frames are fetched; "
        "adjust them and re-run, or confirm to fetch + animate."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Registry helpers
# --------------------------------------------------------------------------- #


def _registry_fn(name: str) -> Any:
    """Resolve ``name`` -> the registered tool callable (registry-as-source rule)."""
    entry = TOOL_REGISTRY.get(name)
    if entry is None:
        raise SatelliteFireAnimationError(
            f"required atomic tool {name!r} is not registered "
            f"(known: {sorted(TOOL_REGISTRY)[:8]}...)"
        )
    return entry.fn


def _peek_frame_count(
    product: str,
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
) -> int:
    """Count planned frames for a product over the window WITHOUT fetching imagery.

    Reads only the SLIDER JSON time index (cheap) + applies the same window /
    day-filter / cap the fetcher will. Pure-ish (one small JSON GET). Returns 0
    and logs on any upstream hiccup -- the review gate stays informative even if
    the index is briefly unreachable.
    """
    try:
        if _is_polar_product(product):
            from ..tools.fetch_viirs_day_fire import (
                DAY_FIRE_PRODUCT_SLUG,
                _build_pass_list,
            )
            from ..tools._satellite_slider import fetch_slider_timestamps

            all_ts = fetch_slider_timestamps("jpss", "conus", DAY_FIRE_PRODUCT_SLUG)
            center_lon = (bbox[0] + bbox[2]) / 2.0
            return len(_build_pass_list(all_ts, start, end, center_lon, day_only=True))
        else:
            from ..tools.fetch_goes_animation import (
                _band_to_slider_product,
                _build_frame_list,
            )
            from ..tools._satellite_slider import fetch_slider_timestamps

            slug = _band_to_slider_product(product)
            all_ts = fetch_slider_timestamps("goes-18", "conus", slug)
            return len(_build_frame_list(all_ts, start, end))
    except Exception as exc:  # noqa: BLE001 -- review-gate peek is best-effort
        logger.warning(
            "model_satellite_fire_animation: frame-count peek for %s failed (%s)",
            product,
            exc,
        )
        return 0


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #


async def model_satellite_fire_animation(
    incident_name: str,
    products: list[str] | None = None,
    state: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    satellite: str | None = None,
    confirm: bool = False,
    overlay_firms: bool = True,
    overlay_perimeters: bool = True,
    *,
    pipeline_emitter: "PipelineEmitter | None" = None,
) -> dict[str, Any]:
    """Compose a satellite fire animation (GOES or JPSS) with a bbox/window review gate.

    Two phases:

    1. ``confirm=False`` (default) -- PLAN + REVIEW: resolve the incident,
       derive the AOI bbox + window, peek the planned frame count per product,
       and STOP, returning ``status="review"`` with the bbox + window + frame
       counts + a deterministic review summary. The user sees + adjusts the bbox
       and window BEFORE any imagery is fetched.
    2. ``confirm=True`` -- EXECUTE: dispatch the right imagery fetcher per product
       (each frame fetch in asyncio.to_thread), overlay FIRMS (historical date) +
       NIFC perimeters, publish every layer via TiTiler, and return
       ``status="ok"`` (or ``status="empty"`` if no imagery frames were produced
       -- the honesty floor).

    Args:
        incident_name: the named fire incident (e.g. "Iron", "Santa Rosa Island").
        products: imagery products to animate. Defaults: GOES
            ["geocolor", "fire_temperature"] unless a polar product is named.
            Allowed: "geocolor", "fire_temperature" (GOES), "day_fire" (VIIRS).
        state: optional US state filter for the incident lookup ("UT"/"US-UT").
        start_utc / end_utc: ISO-8601 UTC window bounds (override the defaults).
        bbox: optional AOI override (else derived from the incident point).
        satellite: optional satellite override ("goes-18"/"goes-19" for GOES;
            "suomi-npp"/"noaa-20"/"noaa-21"/"all" for VIIRS).
        confirm: False = stop at the review gate; True = fetch + publish.
        overlay_firms / overlay_perimeters: include the static co-registered
            FIRMS hot-pixel + NIFC perimeter overlays (confirm phase only).
        pipeline_emitter: optional live progress emitter.

    Returns:
        A JSON-compatible dict. Review phase: ``{status:"review", incident,
        bbox, start_utc, end_utc, products, frame_counts, presentation_text}``.
        Execute phase: ``{status:"ok"|"empty", incident, bbox, start_utc,
        end_utc, layers:[...], frame_counts, n_frames, n_overlays, message}``.
    """
    if not isinstance(incident_name, str) or not incident_name.strip():
        raise SatelliteFireAnimationInputError(
            f"incident_name must be a non-empty string; got {incident_name!r}"
        )
    products = list(products) if products else list(GOES_PRODUCTS)
    for p in products:
        if p not in SUPPORTED_PRODUCTS:
            raise SatelliteFireAnimationInputError(
                f"product {p!r} not in {list(SUPPORTED_PRODUCTS)}"
            )
    if not products:
        raise SatelliteFireAnimationInputError("at least one product is required")

    # --- Stage 1: resolve the named incident -> point + bbox + discovery floor.
    if pipeline_emitter is not None:
        step = await pipeline_emitter.add_step(
            name=f"Look up incident: {incident_name}", tool_name="fetch_wfigs_incident"
        )
        await pipeline_emitter.mark_running(step)
    else:
        step = None
    try:
        wfigs_fn = _registry_fn("fetch_wfigs_incident")
        incident = await asyncio.to_thread(wfigs_fn, incident_name, state)
    except Exception:
        if pipeline_emitter is not None and step is not None:
            await pipeline_emitter.mark_failed(
                step, "WFIGS_LOOKUP_FAILED", f"incident lookup failed for {incident_name!r}"
            )
        raise
    if pipeline_emitter is not None and step is not None:
        await pipeline_emitter.mark_complete(step)

    resolved_bbox = tuple(bbox) if bbox else tuple(incident.get("bbox") or ())
    if not resolved_bbox or len(resolved_bbox) != 4:
        raise SatelliteFireAnimationError(
            f"could not resolve an AOI bbox for incident {incident_name!r}"
        )
    resolved_bbox = (
        float(resolved_bbox[0]),
        float(resolved_bbox[1]),
        float(resolved_bbox[2]),
        float(resolved_bbox[3]),
    )
    discovery_iso = incident.get("fire_discovery_datetime")

    # --- Stage 2: derive the window (per the first product's family) + peek frames.
    end_dt_arg = _parse_utc(end_utc)
    primary_product = products[0]
    start_dt, end_dt = _default_window_for_product(
        primary_product, discovery_iso, end_dt_arg
    )
    start_override = _parse_utc(start_utc)
    if start_override is not None:
        start_dt = start_override
    if end_dt_arg is not None:
        end_dt = end_dt_arg
    if start_dt >= end_dt:
        raise SatelliteFireAnimationInputError(
            f"start_utc ({start_dt.isoformat()}) must be before end_utc "
            f"({end_dt.isoformat()})"
        )

    frame_counts: dict[str, int] = {}
    for product in products:
        frame_counts[product] = await asyncio.to_thread(
            _peek_frame_count, product, resolved_bbox, start_dt, end_dt
        )

    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Review gate: STOP unless confirmed (Invariant 9; the #154 philosophy). ---
    if not confirm:
        review_text = _compose_review_text(
            str(incident.get("incident_name") or incident_name),
            resolved_bbox,
            products,
            start_dt,
            end_dt,
            frame_counts,
        )
        return {
            "status": "review",
            "incident": incident,
            "bbox": list(resolved_bbox),
            "start_utc": start_iso,
            "end_utc": end_iso,
            "products": products,
            "frame_counts": frame_counts,
            "presentation_text": review_text,
            "message": (
                "Review the AOI bbox and time window. Re-run with confirm=true "
                "(optionally adjusting bbox/start_utc/end_utc) to fetch + animate."
            ),
        }

    # --- Stage 3 (confirmed): dispatch the imagery fetcher per product. ---
    all_layers: list[LayerURI] = []
    per_product_frames: dict[str, int] = {}
    for product in products:
        fetcher_name = _product_to_fetcher(product)
        fetcher = _registry_fn(fetcher_name)
        if pipeline_emitter is not None:
            step = await pipeline_emitter.add_step(
                name=f"Fetch {product} frames", tool_name=fetcher_name
            )
            await pipeline_emitter.mark_running(step)
        else:
            step = None
        try:
            if _is_polar_product(product):
                frames = await asyncio.to_thread(
                    fetcher,
                    resolved_bbox,
                    satellite or "all",
                    product,
                    "conus",
                    start_iso,
                    end_iso,
                )
            else:
                frames = await asyncio.to_thread(
                    fetcher,
                    resolved_bbox,
                    product,
                    satellite or "goes-18",
                    "conus",
                    start_iso,
                    end_iso,
                )
        except Exception as exc:  # noqa: BLE001 -- one empty product must not sink the rest
            if pipeline_emitter is not None and step is not None:
                await pipeline_emitter.mark_failed(
                    step, "IMAGERY_FETCH_FAILED", f"{fetcher_name} failed: {exc}"
                )
            logger.warning(
                "model_satellite_fire_animation: %s for product=%s produced no "
                "frames (%s)",
                fetcher_name,
                product,
                exc,
            )
            per_product_frames[product] = 0
            continue
        frame_list = list(frames) if isinstance(frames, list) else [frames]
        per_product_frames[product] = len(frame_list)
        all_layers.extend(frame_list)
        if pipeline_emitter is not None and step is not None:
            await pipeline_emitter.mark_complete(step)

    # --- Stage 4 (confirmed): static co-registered overlays (best-effort). ---
    overlay_layers: list[LayerURI] = []
    overlay_date = start_dt.strftime("%Y-%m-%d")
    if overlay_firms:
        firms_layer = await _safe_overlay_firms(resolved_bbox, overlay_date, pipeline_emitter)
        if firms_layer is not None:
            overlay_layers.append(firms_layer)
    if overlay_perimeters:
        nifc_layer = await _safe_overlay_perimeters(resolved_bbox, pipeline_emitter)
        if nifc_layer is not None:
            overlay_layers.append(nifc_layer)

    # --- Stage 5 (confirmed): publish every layer via TiTiler (to_thread). ---
    published = await _publish_layers(all_layers + overlay_layers, pipeline_emitter)

    n_frames = len(all_layers)
    # Honesty floor: no imagery frames -> NOT ok.
    if n_frames == 0:
        return {
            "status": "empty",
            "incident": incident,
            "bbox": list(resolved_bbox),
            "start_utc": start_iso,
            "end_utc": end_iso,
            "products": products,
            "frame_counts": per_product_frames,
            "n_frames": 0,
            "n_overlays": len(overlay_layers),
            "layers": [],
            "message": (
                "No imagery frames were produced for the requested products over "
                "the AOI and window (no SLIDER coverage / AOI off-grid). Nothing "
                "to animate -- adjust the bbox, window, or product and re-run."
            ),
        }

    return {
        "status": "ok",
        "incident": incident,
        "bbox": list(resolved_bbox),
        "start_utc": start_iso,
        "end_utc": end_iso,
        "products": products,
        "frame_counts": per_product_frames,
        "n_frames": n_frames,
        "n_overlays": len(overlay_layers),
        "layers": [_layer_summary(layer, published) for layer in all_layers + overlay_layers],
        "message": (
            f"Animated {n_frames} frame(s) across {len(products)} product(s) with "
            f"{len(overlay_layers)} overlay(s) for {incident_name}."
        ),
    }


# --------------------------------------------------------------------------- #
# Overlay + publish helpers
# --------------------------------------------------------------------------- #


async def _safe_overlay_firms(
    bbox: tuple[float, float, float, float],
    date_iso: str,
    pipeline_emitter: "PipelineEmitter | None",
) -> LayerURI | None:
    """Fetch the FIRMS historical-date hot-pixel overlay (best-effort)."""
    if pipeline_emitter is not None:
        step = await pipeline_emitter.add_step(
            name="Overlay FIRMS hot pixels", tool_name="fetch_firms_active_fire"
        )
        await pipeline_emitter.mark_running(step)
    else:
        step = None
    try:
        firms_fn = _registry_fn("fetch_firms_active_fire")
        # VIIRS_NOAA20_NRT is the JPSS sibling; date forces the single past day.
        layer = await asyncio.to_thread(
            firms_fn, bbox, 1, "VIIRS_NOAA20_NRT", date_iso
        )
    except Exception as exc:  # noqa: BLE001 -- overlay is non-fatal
        logger.warning("model_satellite_fire_animation: FIRMS overlay failed (%s)", exc)
        if pipeline_emitter is not None and step is not None:
            await pipeline_emitter.mark_failed(step, "FIRMS_OVERLAY_FAILED", str(exc))
        return None
    if pipeline_emitter is not None and step is not None:
        await pipeline_emitter.mark_complete(step)
    return layer if isinstance(layer, LayerURI) else None


async def _safe_overlay_perimeters(
    bbox: tuple[float, float, float, float],
    pipeline_emitter: "PipelineEmitter | None",
) -> LayerURI | None:
    """Fetch the NIFC perimeter overlay (best-effort)."""
    if pipeline_emitter is not None:
        step = await pipeline_emitter.add_step(
            name="Overlay NIFC perimeters", tool_name="fetch_nifc_fire_perimeters"
        )
        await pipeline_emitter.mark_running(step)
    else:
        step = None
    try:
        nifc_fn = _registry_fn("fetch_nifc_fire_perimeters")
        layer = await asyncio.to_thread(nifc_fn, bbox)
    except Exception as exc:  # noqa: BLE001 -- overlay is non-fatal
        logger.warning("model_satellite_fire_animation: NIFC overlay failed (%s)", exc)
        if pipeline_emitter is not None and step is not None:
            await pipeline_emitter.mark_failed(step, "NIFC_OVERLAY_FAILED", str(exc))
        return None
    if pipeline_emitter is not None and step is not None:
        await pipeline_emitter.mark_complete(step)
    return layer if isinstance(layer, LayerURI) else None


async def _publish_layers(
    layers: list[LayerURI],
    pipeline_emitter: "PipelineEmitter | None",
) -> dict[str, str]:
    """Publish each layer via publish_layer (TiTiler) in asyncio.to_thread.

    Returns a map ``layer_id -> published WMS url`` for successfully-published
    layers. Publish failures are non-fatal (the COG/FGB still exists at its
    cache URI); they are logged and skipped so a publish hiccup does not sink the
    whole animation. On AWS publish_layer can fail until QGIS-on-AWS lands -- the
    frames are still cached + the LayerURIs are still returned.
    """
    published: dict[str, str] = {}
    try:
        publish_fn = _registry_fn("publish_layer")
    except SatelliteFireAnimationError:
        logger.warning("model_satellite_fire_animation: publish_layer not registered; skipping publish")
        return published
    for layer in layers:
        try:
            url = await asyncio.to_thread(
                publish_fn,
                layer.uri,
                layer.layer_id,
                layer.style_preset,
            )
            if isinstance(url, str) and url:
                published[layer.layer_id] = url
        except Exception as exc:  # noqa: BLE001 -- publish is non-fatal
            logger.warning(
                "model_satellite_fire_animation: publish_layer(%s) failed (%s)",
                layer.layer_id,
                exc,
            )
    return published


def _layer_summary(layer: LayerURI, published: dict[str, str]) -> dict[str, Any]:
    """Compact JSON summary of one layer (the producing URI + any published URL)."""
    return {
        "layer_id": layer.layer_id,
        "name": layer.name,
        "layer_type": layer.layer_type,
        "style_preset": layer.style_preset,
        "role": layer.role,
        "uri": layer.uri,
        "published_url": published.get(layer.layer_id),
    }


# --------------------------------------------------------------------------- #
# LLM-exposed thin atomic-tool wrapper (workflow_dispatch source class)
# --------------------------------------------------------------------------- #


_RUN_METADATA = AtomicToolMetadata(
    name="run_model_satellite_fire_animation",
    ttl_class="live-no-cache",
    source_class="workflow_dispatch",
    cacheable=False,
)


@register_tool(_RUN_METADATA)
async def run_model_satellite_fire_animation(
    incident_name: str,
    products: list[str] | None = None,
    state: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    satellite: str | None = None,
    confirm: bool = False,
    overlay_firms: bool = True,
    overlay_perimeters: bool = True,
    # job-0164: absorb LLM-invented kwargs.
    **_extra_ignored: Any,
) -> dict[str, Any]:
    """Recreate a CIRA-style satellite fire animation (GOES or JPSS/VIIRS), review-gated.

    Composes the full fire-animation pipeline: resolve the named incident
    (NIFC/WFIGS) -> AOI bbox + time window -> per-frame satellite imagery
    (GOES-18 GeoColor + Fire Temperature for an intra-day 5-minute loop, OR
    JPSS/VIIRS Day Fire for a multi-day irregular polar series) -> a scrubbable
    animation, with FIRMS hot pixels + the NIFC perimeter overlaid. STOPS at a
    bbox/window REVIEW gate first so the user sees + can adjust the AOI and the
    window BEFORE all frames are fetched.

    When to use:
        - "Recreate the CIRA GOES fire animation of the fires near Eureka Utah."
        - "Recreate the JPSS VIIRS Day Fire animation of the Santa Rosa Island
          fire over the four days it grew."
        - Any "pull the news on this fire and animate it from satellite imagery"
          request. Pick GOES-18 / 5-minute for an intra-day loop; pick
          JPSS/VIIRS Day Fire for a multi-day timelapse. ALWAYS hit the review
          gate (confirm=false) first.

    When NOT to use:
        - A single most-recent satellite frame (use fetch_goes_satellite).
        - Active-fire detections only (fetch_firms_active_fire) or perimeters only
          (fetch_nifc_fire_perimeters) with no animation.
        - A flood / surge / seismic scenario (use the matching run_model_* engine).

    Params:
        incident_name: the named fire incident (e.g. "Iron", "Santa Rosa Island").
        products: list of imagery products. GOES: "geocolor", "fire_temperature";
            VIIRS: "day_fire". Default ["geocolor", "fire_temperature"] (GOES).
        state: optional US state filter for the incident lookup ("UT"/"US-UT").
        start_utc / end_utc: ISO-8601 UTC window bounds. Defaults: GOES ~6.5h,
            VIIRS ~4 days, never before the WFIGS discovery time.
        bbox: optional AOI override [min_lon, min_lat, max_lon, max_lat].
        satellite: GOES "goes-18"/"goes-19"; VIIRS "suomi-npp"/"noaa-20"/
            "noaa-21"/"all".
        confirm: false (default) = stop at the review gate and return the bbox +
            window + planned frame counts; true = fetch + publish + animate.
        overlay_firms / overlay_perimeters: include the static co-registered
            FIRMS hot-pixel + NIFC perimeter overlays (confirm phase).

    Returns:
        Review phase (confirm=false): a dict with status="review", the AOI bbox,
        the time window, the planned per-product frame counts, and a
        presentation_text the UI shows for approval.
        Execute phase (confirm=true): a dict with status="ok" (or "empty" if no
        imagery frames were produced -- the honesty floor), the bbox, window,
        the published layer summaries, and frame/overlay counts.

    Cross-tool dependencies:
        Upstream (step chain): fetch_wfigs_incident -> fetch_goes_animation /
        fetch_viirs_day_fire (per product, per frame) -> fetch_firms_active_fire
        (historical date) + fetch_nifc_fire_perimeters (overlays) -> publish_layer.
    """
    return await model_satellite_fire_animation(
        incident_name=incident_name,
        products=products,
        state=state,
        start_utc=start_utc,
        end_utc=end_utc,
        bbox=bbox,
        satellite=satellite,
        confirm=confirm,
        overlay_firms=overlay_firms,
        overlay_perimeters=overlay_perimeters,
        pipeline_emitter=None,
    )
