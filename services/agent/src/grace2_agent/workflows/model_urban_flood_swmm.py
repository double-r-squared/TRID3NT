"""PySWMM quasi-2D urban-flood composer (sprint-16 P4, Path A — the LOCAL lane).

The SWMM analogue of ``model_flood_scenario`` (SFINCS) /
``model_groundwater_contamination_scenario`` (MODFLOW). A deterministic
orchestrator-style workflow (Invariant 2 — no LLM in the chain) that composes
the urban-flood engine end-to-end on NATE's PCSWMM screenshot path:

    fetch DEM (fetch_3dep_extra 1m -> fetch_dem 10m fallback)
      -> fetch_buildings(source=osm)
      -> lookup_precip_return_period (Atlas-14 design-storm depth)
      -> build_swmm_mesh (P2: quasi-2D node/link SWMM deck; barriers/buildings/
         infiltration/single-outfall/nested-hyetograph/mass-balance gate)
      -> run_swmm_local (P4: pyswmm IN-PROCESS — the dev primary path)
      -> postprocess_swmm (P3: rasterize per-timestep node INVERT_DEPTH ->
         peak primary COG + per-frame COGs)
      -> publish the peak primary + emit the frames via the Phase-1 Step-9b
         emitter block (frames out-of-band via emitter.add_loaded_layer; the
         peak is the single returned LayerURI).

Returns the PEAK ``SWMMDepthLayerURI`` directly (a ``LayerURI`` subtype) so the
``emit_tool_call`` ``add_loaded_layer`` gate fires on it — exactly like
``run_modflow_job`` returns a ``PlumeLayerURI``. The per-frame depth COGs are
emitted OUT-OF-BAND through ``emitter.add_loaded_layer`` (distinct runs-bucket
keys -> distinct TiTiler url -> no dedup collapse) so the web
``detectSequentialGroups`` LayerPanel scrubber group forms WITHOUT changing the
single-LayerURI return shape (no re-publish trip in ``summarize_tool_result``).

Determinism boundary (Invariant 1): every depth number the agent narrates comes
from the typed ``SWMMDepthLayerURI.max_depth_m`` / ``.flooded_area_km2`` /
``.n_buildings_affected`` fields the postprocess computed with plain arithmetic
— never free-generated.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from grace2_contracts.swmm_contracts import SWMMRunArgs
from grace2_contracts.swmm_contracts import SWMMDepthLayerURI

from ..pipeline_emitter import current_emitter
from .postprocess_swmm import (
    PostprocessSWMMError,
    postprocess_swmm,
)
from .run_swmm import (
    SWMMWorkflowError,
    build_and_stage_swmm_deck,
    is_local_mode,
    run_swmm_local,
)

logger = logging.getLogger("grace2_agent.workflows.model_urban_flood_swmm")

__all__ = [
    "model_urban_flood_swmm",
    "UrbanFloodWorkflowError",
]

#: Inches -> mm (Atlas-14 PFDS returns inches; the hyetograph builder wants mm).
_INCH_TO_MM: float = 25.4


class UrbanFloodWorkflowError(RuntimeError):
    """Raised on a fatal composer failure (carries an open-set ``error_code``)."""

    error_code: str = "URBAN_FLOOD_WORKFLOW_FAILED"

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# --------------------------------------------------------------------------- #
# DEM acquisition (1 m 3DEP -> 10 m fallback) with localization.
# --------------------------------------------------------------------------- #
def _bbox_centroid_latlon(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Return the ``(lat, lon)`` centroid of a ``(min_lon, min_lat, max_lon,
    max_lat)`` bbox (the lat-first point the precip lookup wants)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return (0.5 * (min_lat + max_lat), 0.5 * (min_lon + max_lon))


def _localize_to_dem_path(uri: str) -> str:
    """Resolve a DEM ``LayerURI.uri`` (gs:// / s3:// / file:// / local) to an
    on-disk GeoTIFF path the mesh builder can read with rasterio.

    The mesh builder reads a local filesystem path; ``fetch_3dep_extra`` /
    ``fetch_dem`` return a cache URI. ``gs://`` / ``s3://`` objects are staged
    down to a temp file (boto3 for s3, google-cloud-storage for gs — matching
    the sfincs_builder staging seam); ``file://`` + bare local paths pass
    through. On a synthetic / test path the URI is already local.
    """
    if uri.startswith("file://"):
        return uri[len("file://"):]
    if not (uri.startswith("gs://") or uri.startswith("s3://")):
        return uri

    import hashlib

    cache_dir = Path(tempfile.gettempdir()) / "grace2-swmm-dem-stage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(uri).suffix or ".tif"
    local = cache_dir / (hashlib.sha256(uri.encode()).hexdigest()[:24] + suffix)
    if local.exists() and local.stat().st_size > 0:
        return str(local)
    tmp = local.with_suffix(local.suffix + ".part")
    if uri.startswith("s3://"):
        from ..tools.solver import _get_s3_client

        bucket_name, _, obj_key = uri[len("s3://"):].partition("/")
        resp = _get_s3_client().get_object(Bucket=bucket_name, Key=obj_key)
        with tmp.open("wb") as fh:
            shutil.copyfileobj(resp["Body"], fh)
    else:
        from google.cloud import storage

        bucket_name, _, blob_name = uri[len("gs://"):].partition("/")
        client = storage.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod")
        )
        client.bucket(bucket_name).blob(blob_name).download_to_filename(str(tmp))
    os.replace(tmp, local)
    logger.info("staged DEM %s -> %s (%d bytes)", uri, local, local.stat().st_size)
    return str(local)


def _fetch_dem_for_urban(
    bbox: tuple[float, float, float, float],
) -> tuple[str, str]:
    """Fetch a DEM for the AOI: try ``fetch_3dep_extra`` 1 m first, fall back to
    ``fetch_dem`` 10 m (the data-source fallback norm: primary -> fallback,
    honest typed error if both fail).

    Returns ``(local_dem_path, source_label)``. Raises
    ``UrbanFloodWorkflowError("SWMM_DEM_FETCH_FAILED")`` only when BOTH fail.
    """
    from ..tools.data_fetch import fetch_dem
    from ..tools.fetch_3dep_extra import fetch_3dep_extra

    # Primary: 1 m LiDAR (building-scale resolution the screenshot path wants).
    try:
        layer = fetch_3dep_extra(bbox, resolution="1 meter")
        return _localize_to_dem_path(layer.uri), "USGS 3DEP 1m LiDAR"
    except Exception as exc:  # noqa: BLE001 — fall through to the 10 m fallback
        logger.info(
            "fetch_3dep_extra(1m) failed (%s); falling back to fetch_dem(10m)", exc
        )

    # Fallback: 10 m 3DEP (the canonical default).
    try:
        layer = fetch_dem(bbox, resolution_m=10)
        return _localize_to_dem_path(layer.uri), "USGS 3DEP 10m"
    except Exception as exc:  # noqa: BLE001
        raise UrbanFloodWorkflowError(
            "SWMM_DEM_FETCH_FAILED",
            f"both DEM sources failed for bbox {bbox}: 3DEP-1m + fetch_dem-10m: {exc}",
        ) from exc


def _fetch_buildings_for_urban(
    bbox: tuple[float, float, float, float],
) -> Any:
    """Fetch OSM building footprints for the AOI (the reliable footprint source,
    per memory project_building_footprints_source). Returns the GeoJSON
    FeatureCollection dict, or ``None`` on failure (footprints are an enhancement,
    not a hard gate — the mesh still builds without obstructions)."""
    from ..tools.data_fetch import fetch_buildings

    try:
        layer = fetch_buildings(bbox, source="osm")
    except Exception as exc:  # noqa: BLE001 — buildings are optional
        logger.info("fetch_buildings(osm) failed (%s); proceeding without footprints", exc)
        return None
    # The footprints come back as an inline GeoJSON FeatureCollection on the
    # LayerURI (job-0175 inline-GeoJSON convention) or as a cache URI; the mesh
    # builder accepts the FeatureCollection dict directly.
    fc = getattr(layer, "inline_geojson", None) or getattr(layer, "geojson", None)
    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
        return fc
    return None


def _atlas14_total_depth_mm(
    bbox: tuple[float, float, float, float],
    return_period_yr: int,
    storm_duration_hr: float,
) -> float | None:
    """Look up the Atlas-14 design-storm depth (mm) for the AOI centroid.

    Returns the total storm depth in mm, or ``None`` on lookup failure (the
    builder then uses its sane hyetograph default — never a silent dead-end).
    """
    from ..tools.data_fetch import lookup_precip_return_period

    lat, lon = _bbox_centroid_latlon(bbox)
    try:
        result = lookup_precip_return_period(
            location=(lat, lon),
            return_period_years=int(return_period_yr),
            duration_hours=float(storm_duration_hr),
        )
    except Exception as exc:  # noqa: BLE001 — fall back to the builder default
        logger.info(
            "lookup_precip_return_period failed (%s); using the builder's "
            "hyetograph default depth", exc
        )
        return None
    inches = result.get("precip_inches") if isinstance(result, dict) else None
    if inches is None:
        return None
    return float(inches) * _INCH_TO_MM


# --------------------------------------------------------------------------- #
# The composer.
# --------------------------------------------------------------------------- #
async def model_urban_flood_swmm(
    run_args: SWMMRunArgs,
    *,
    dem_path: str | None = None,
    building_footprints: Any = None,
    run_id: str | None = None,
    compute_class: str = "standard",
    cleanup_deck: bool = True,
) -> SWMMDepthLayerURI:
    """Compose the full quasi-2D PySWMM urban-flood chain end-to-end (LOCAL lane).

    Args:
        run_args: the validated ``SWMMRunArgs`` (bbox + design storm + building
            representation + infiltration + optional barriers).
        dem_path: optional on-disk DEM path. When ``None`` the composer fetches
            it (``fetch_3dep_extra`` 1 m -> ``fetch_dem`` 10 m fallback) from the
            ``run_args.bbox``. Tests pass a synthetic GeoTIFF to skip the fetch.
        building_footprints: optional GeoJSON FeatureCollection. When ``None``
            (and ``dem_path`` was NOT supplied) the composer fetches OSM
            footprints; when ``dem_path`` IS supplied, footprints are used as
            given (tests control them explicitly).
        run_id: optional ULID; minted by the staging step if absent.
        compute_class: FR-CE-3 compute class (carried for provenance; the LOCAL
            lane runs in-process regardless).
        cleanup_deck: when True, the scratch deck dir is removed after
            postprocess (the COGs were already uploaded). Tests pass False to
            inspect the deck.

    Returns:
        The PEAK ``SWMMDepthLayerURI`` (role ``"primary"``, name
        ``"Peak flood depth"``) carrying the three narration scalars + the echoed
        barrier geometry. Per-frame depth layers are emitted out-of-band via the
        emitter (Step-9b) so the web scrubber group forms.

    Raises:
        UrbanFloodWorkflowError / SWMMWorkflowError / PostprocessSWMMError on a
        fatal stage failure (the tool wrapper catches these and returns a typed
        error dict so the agent narrates honestly).
    """
    bbox = tuple(run_args.bbox)  # (min_lon, min_lat, max_lon, max_lat)
    emitter = current_emitter()

    # --- Zoom-on-area-first (job-0160): the map zooms before the solve runs. ---
    if emitter is not None:
        try:
            await emitter.emit_map_command("zoom-to", {"bbox": list(bbox)})
        except Exception as exc:  # noqa: BLE001 — non-fatal UX hint
            logger.warning("model_urban_flood_swmm: zoom-to emit failed: %s", exc)

    # --- Step 1: DEM (1 m 3DEP primary -> 10 m fallback) --------------------
    deck_dir_to_clean: str | None = None
    if dem_path is None:
        local_dem_path, dem_source = _fetch_dem_for_urban(bbox)
    else:
        local_dem_path, dem_source = dem_path, "supplied"
    logger.info("model_urban_flood_swmm: DEM=%s (%s)", local_dem_path, dem_source)

    # --- Step 2: building footprints (OSM) ----------------------------------
    if building_footprints is None and dem_path is None:
        building_footprints = _fetch_buildings_for_urban(bbox)

    # --- Step 3: Atlas-14 design-storm depth (populate run_args if unset) ----
    effective_args = run_args
    if run_args.total_rain_depth_mm is None:
        depth_mm = _atlas14_total_depth_mm(
            bbox, run_args.return_period_yr, run_args.storm_duration_hr
        )
        if depth_mm is not None:
            effective_args = run_args.model_copy(
                update={"total_rain_depth_mm": depth_mm}
            )
            logger.info(
                "model_urban_flood_swmm: Atlas-14 depth=%.1f mm (%d-yr, %.0f-hr)",
                depth_mm,
                run_args.return_period_yr,
                run_args.storm_duration_hr,
            )

    try:
        # --- Step 4: build the quasi-2D SWMM deck (build_swmm_mesh) ----------
        staging = build_and_stage_swmm_deck(
            effective_args,
            dem_path=local_dem_path,
            building_footprints=building_footprints,
            run_id=run_id,
        )
        deck_dir_to_clean = str(Path(staging.inp_path).parent)

        # --- Step 5: solve (pyswmm in-process — the dev primary path) -------
        # is_local_mode() is True by default; the out-of-process staged-manifest
        # lane (run_solver(solver='swmm') + wait_for_completion) is wired via the
        # SWMM LocalSolverSpec but the urban engine's primary path is in-process.
        if not is_local_mode():
            logger.info(
                "model_urban_flood_swmm: GRACE2_SWMM_LOCAL=0 set, but the v0.1 "
                "primary path runs pyswmm in-process; using run_swmm_local."
            )
        run = run_swmm_local(staging)

        # --- Step 6: postprocess (rasterize node depths -> peak + frames) ---
        layers, metrics = postprocess_swmm(
            run,
            staging.build,
            run_id=staging.run_id,
            building_footprints=building_footprints,
        )
    except (SWMMWorkflowError, PostprocessSWMMError):
        # Cleanup before re-raising — the tool wrapper turns these into a typed
        # error dict.
        if cleanup_deck and deck_dir_to_clean:
            _cleanup_deck_dir(deck_dir_to_clean)
        raise

    if not layers:
        if cleanup_deck and deck_dir_to_clean:
            _cleanup_deck_dir(deck_dir_to_clean)
        raise UrbanFloodWorkflowError(
            "SWMM_NO_LAYERS",
            "postprocess_swmm produced no depth layers (empty solve?)",
        )

    peak = layers[0]
    frame_layers = layers[1:]

    # --- Step 7 / 9b: emit the per-frame animation layers OUT-OF-BAND --------
    # Mirrors model_flood_scenario Step-9b: each frame is a DISTINCT COG (distinct
    # runs-bucket key -> distinct TiTiler url -> no dedup collapse). We emit in
    # ascending step order via emitter.add_loaded_layer so all N frames arrive as
    # one contiguous sequential group. Frames are emitted ONLY through the emitter
    # (NOT returned), so they never reach summarize_tool_result. When the emitter
    # is None (direct/smoke/test) frame emission is skipped — the frames still
    # live in `layers` for tests to assert on.
    emitted_frames = await _emit_frame_layers(emitter, frame_layers, staging.run_id)

    logger.info(
        "model_urban_flood_swmm complete run_id=%s max_depth_m=%.4g "
        "flooded_area_km2=%.6g n_buildings_affected=%d frames_emitted=%d/%d "
        "continuity=%+.3f%%",
        staging.run_id,
        peak.max_depth_m,
        peak.flooded_area_km2,
        peak.n_buildings_affected,
        emitted_frames,
        len(frame_layers),
        run.continuity_error_pct,
    )

    # --- Step 8: cleanup the scratch deck (COGs already uploaded) -----------
    if cleanup_deck and deck_dir_to_clean:
        _cleanup_deck_dir(deck_dir_to_clean)

    # The PEAK SWMMDepthLayerURI is returned directly — the emit_tool_call
    # add_loaded_layer gate fires on it (a LayerURI subtype). Invariant 1: the
    # agent narrates peak.max_depth_m / .flooded_area_km2 / .n_buildings_affected.
    return peak


async def _emit_frame_layers(
    emitter: Any, frame_layers: list[SWMMDepthLayerURI], run_id: str
) -> int:
    """Emit per-frame depth COGs out-of-band so the web scrubber group forms.

    Returns the number of frames emitted (0 when no emitter is bound — the
    direct/smoke/test path). Never raises — a frame emit failure must not sink
    the peak layer (the postprocess_flood honesty stance carried into the
    composer).
    """
    if not frame_layers or emitter is None:
        if frame_layers:
            logger.info(
                "model_urban_flood_swmm: %d animation frames available but no "
                "emitter bound (direct/smoke/test) — frames not emitted.",
                len(frame_layers),
            )
        return 0
    emitted = 0
    for lyr in frame_layers:
        try:
            await emitter.add_loaded_layer(lyr)
            emitted += 1
        except Exception as exc:  # noqa: BLE001 — never break the solve
            logger.warning(
                "model_urban_flood_swmm: frame add_loaded_layer failed for %s: %s",
                lyr.layer_id,
                exc,
            )
    if emitted:
        logger.info(
            "model_urban_flood_swmm: emitted %d/%d animation frames as a "
            "sequential group (run_id=%s)",
            emitted,
            len(frame_layers),
            run_id,
        )
    return emitted


def _cleanup_deck_dir(deck_dir: str) -> None:
    """Best-effort removal of the scratch deck dir (mirrors run_modflow_tool)."""
    try:
        p = Path(deck_dir)
        # Only remove a temp dir we created (prefix swmm-).
        base = p
        for _ in range(3):
            if base.name.startswith("swmm-"):
                shutil.rmtree(base, ignore_errors=True)
                return
            base = base.parent
        shutil.rmtree(p, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
