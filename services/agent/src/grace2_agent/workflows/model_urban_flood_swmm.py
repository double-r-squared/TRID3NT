"""PySWMM quasi-2D urban-flood composer (sprint-16 P4, Path A - the LOCAL lane).

The SWMM analogue of ``model_flood_scenario`` (SFINCS) /
``model_groundwater_contamination_scenario`` (MODFLOW). A deterministic
orchestrator-style workflow (Invariant 2 - no LLM in the chain) that composes
the urban-flood engine end-to-end on NATE's PCSWMM screenshot path:

    fetch DEM (fetch_3dep_extra 1m -> fetch_dem 10m fallback)
      -> fetch_buildings(source=osm)
      -> lookup_precip_return_period (Atlas-14 design-storm depth)
      -> build_swmm_mesh (P2: quasi-2D node/link SWMM deck; barriers/buildings/
         infiltration/single-outfall/nested-hyetograph/mass-balance gate)
      -> run_swmm_local (P4: pyswmm IN-PROCESS - the dev primary path)
      -> postprocess_swmm (P3: rasterize per-timestep node INVERT_DEPTH ->
         peak primary COG + per-frame COGs)
      -> publish the peak primary + emit the frames via the Phase-1 Step-9b
         emitter block (frames out-of-band via emitter.add_loaded_layer; the
         peak is the single returned LayerURI).

Returns the PEAK ``SWMMDepthLayerURI`` directly (a ``LayerURI`` subtype) so the
``emit_tool_call`` ``add_loaded_layer`` gate fires on it - exactly like
``run_modflow_job`` returns a ``PlumeLayerURI``. The per-frame depth COGs are
emitted OUT-OF-BAND through ``emitter.add_loaded_layer`` (distinct runs-bucket
keys -> distinct TiTiler url -> no dedup collapse) so the web
``detectSequentialGroups`` LayerPanel scrubber group forms WITHOUT changing the
single-LayerURI return shape (no re-publish trip in ``summarize_tool_result``).

Determinism boundary (Invariant 1): every depth number the agent narrates comes
from the typed ``SWMMDepthLayerURI.max_depth_m`` / ``.flooded_area_km2`` /
``.n_buildings_affected`` fields the postprocess computed with plain arithmetic
- never free-generated.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from grace2_contracts.execution import LayerURI
from grace2_contracts.swmm_contracts import SWMMRunArgs
from grace2_contracts.swmm_contracts import SWMMDepthLayerURI

from ..pipeline_emitter import current_emitter
from ..tools.publish_layer import PublishLayerError, publish_layer
from .postprocess_swmm import (
    FLOOD_DEPTH_STYLE_PRESET,
    PostprocessSWMMError,
    postprocess_swmm,
)
from .run_swmm import (
    SWMM_SOLVER_NAME,
    SWMMWorkflowError,
    build_and_stage_swmm_deck,
    is_local_mode,
    run_swmm_local,
    stage_swmm_manifest,
)
from .solve_progress import drive_live_solve_progress
from .swmm_mesh_builder import estimate_swmm_solve_seconds

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
    ``fetch_dem`` return a cache URI. GCP is decommissioned: ``s3://`` objects
    are staged down to a temp file via boto3 (matching the sfincs_builder
    staging seam); ``file://`` + bare local paths pass through. On a synthetic /
    test path the URI is already local.
    """
    if uri.startswith("file://"):
        return uri[len("file://"):]
    if not uri.startswith("s3://"):
        return uri

    import hashlib

    cache_dir = Path(tempfile.gettempdir()) / "grace2-swmm-dem-stage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(uri).suffix or ".tif"
    local = cache_dir / (hashlib.sha256(uri.encode()).hexdigest()[:24] + suffix)
    if local.exists() and local.stat().st_size > 0:
        return str(local)
    tmp = local.with_suffix(local.suffix + ".part")
    from ..tools.solver import _get_s3_client

    bucket_name, _, obj_key = uri[len("s3://"):].partition("/")
    resp = _get_s3_client().get_object(Bucket=bucket_name, Key=obj_key)
    with tmp.open("wb") as fh:
        shutil.copyfileobj(resp["Body"], fh)
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
    except Exception as exc:  # noqa: BLE001 - fall through to the 10 m fallback
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
    not a hard gate - the mesh still builds without obstructions)."""
    from ..tools.data_fetch import fetch_buildings

    try:
        layer = fetch_buildings(bbox, source="osm")
    except Exception as exc:  # noqa: BLE001 - buildings are optional
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
    builder then uses its sane hyetograph default - never a silent dead-end).
    """
    from ..tools.data_fetch import lookup_precip_return_period

    lat, lon = _bbox_centroid_latlon(bbox)
    try:
        result = lookup_precip_return_period(
            location=(lat, lon),
            return_period_years=int(return_period_yr),
            duration_hours=float(storm_duration_hr),
        )
    except Exception as exc:  # noqa: BLE001 - fall back to the builder default
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
        except Exception as exc:  # noqa: BLE001 - non-fatal UX hint
            logger.warning("model_urban_flood_swmm: zoom-to emit failed: %s", exc)

    # --- Step 1: DEM (1 m 3DEP primary -> 10 m fallback) --------------------
    # BREAK B (event-loop starvation), pre-solve: _fetch_dem_for_urban is
    # SYNCHRONOUS blocking I/O (HTTP fetch + boto3 S3 stage-down + GDAL VSI
    # reads). Run it OFF the loop in a worker thread so the WS keepalive ping
    # coroutine keeps running while the fetch churns (mirrors the SFINCS
    # _fetcher_chain asyncio.to_thread wrap). _fetch_dem_for_urban does NOT call
    # the loop-bound PipelineEmitter mid-call - it only logs + returns a tuple -
    # so a plain to_thread wrap is correct (no run_coroutine_threadsafe marshaling
    # is required). The async frame still emits around (before/after) the wrap.
    deck_dir_to_clean: str | None = None
    if dem_path is None:
        local_dem_path, dem_source = await asyncio.to_thread(
            _fetch_dem_for_urban, bbox
        )
    else:
        local_dem_path, dem_source = dem_path, "supplied"
    logger.info("model_urban_flood_swmm: DEM=%s (%s)", local_dem_path, dem_source)

    # --- Step 2: building footprints (OSM) ----------------------------------
    # BREAK B, pre-solve: _fetch_buildings_for_urban is a SYNCHRONOUS HTTP fetch
    # (OSM Overpass). Offload it off the loop too - it is emitter-free (logs +
    # returns a FeatureCollection dict / None), so a plain to_thread wrap is safe.
    if building_footprints is None and dem_path is None:
        building_footprints = await asyncio.to_thread(_fetch_buildings_for_urban, bbox)

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
        # BREAK B, pre-solve: build_and_stage_swmm_deck is a SYNCHRONOUS compute
        # (rasterio DEM read + adaptive-mesh build + .inp staging) with NO
        # loop-bound emitter calls, so offload it off the loop too (mirrors the
        # SFINCS deck-build asyncio.to_thread wrap). A plain to_thread wrap is
        # correct - no run_coroutine_threadsafe marshaling required.
        staging = await asyncio.to_thread(
            build_and_stage_swmm_deck,
            effective_args,
            dem_path=local_dem_path,
            building_footprints=building_footprints,
            run_id=run_id,
        )
        deck_dir_to_clean = str(Path(staging.inp_path).parent)

        # --- Auto vertical scaling per case (NATE 2026-06-17) ----------------
        # Size the Batch compute_class from the built mesh's active-cell count
        # (the adaptive-mesh budget already coarsened the grid to fit a cap;
        # n_active_cells IS the element count) instead of the caller's blind
        # default. A big urban AOI grabs more compute (up to the new xlarge
        # 48-vCPU tier); a small one stays cheap. select_compute_class never
        # raises - a zero/absent count falls back to the caller's compute_class.
        from ..tools.solver import select_compute_class

        n_active = int(getattr(staging.build, "n_active_cells", 0) or 0)
        if n_active > 0:
            effective_compute_class = select_compute_class(n_active)
            logger.info(
                "model_urban_flood_swmm: auto vertical scaling n_active_cells=%d "
                "-> compute_class=%s (caller requested %s)",
                n_active,
                effective_compute_class,
                compute_class,
            )
        else:
            effective_compute_class = compute_class
            logger.info(
                "model_urban_flood_swmm: no active-cell count; using caller "
                "compute_class=%s for the dispatch",
                compute_class,
            )

        # --- Step 5+6: solve + postprocess ----------------------------------
        # is_local_mode() is True by DEFAULT (GRACE2_SWMM_LOCAL unset): the
        # urban engine's primary path is pyswmm IN-PROCESS (the `else` branch
        # below, byte-identical to the proven local lane). When the env is
        # flipped (GRACE2_SWMM_LOCAL=0) the `if not is_local_mode():` branch
        # routes the SAME staged deck through the GENERIC solver-dispatch seam
        # (run_solver -> wait_for_completion -> Batch output) instead. Zero
        # regression until the env is set.
        #
        # LIVE solve-progress heartbeat (NATE 2026-06-17): the solve emits
        # nothing for minutes (off-loop thread OR remote Batch job), so the
        # running card is a silent spinner. Drive the shared solve-progress
        # envelope ON the loop (the emitter is loop-bound) alongside the solve -
        # identical to the proven SFINCS pattern in model_flood_scenario.
        # Best-effort: emitter None -> no-op; cancelled + awaited in a finally
        # regardless of outcome. The heartbeat wraps BOTH lanes.
        from ..tools.solver import AWS_BATCH_COMPUTE_CLASS_SIZING

        _swmm_vcpus = AWS_BATCH_COMPUTE_CLASS_SIZING.get(
            effective_compute_class, {}
        ).get("vcpus")
        if not is_local_mode():
            # --- Out-of-process lane (GRACE2_SWMM_LOCAL=0): GENERIC Batch seam.
            # Stage the built deck + a worker-contract manifest to S3, then
            # dispatch through run_solver / wait_for_completion (the SAME seam
            # SFINCS uses in model_flood_scenario), PASSING the per-case computed
            # compute_class (auto vertical scaling). The SWMM Batch worker
            # (services/workers/swmm/entrypoint.py) solves the deck and writes
            # completion.json + the .out/.rpt to s3://<runs_bucket>/<run_id>/; we
            # download the .out/.rpt and postprocess from the BATCH output.
            from ..tools.solver import run_solver, wait_for_completion

            manifest_uri = await asyncio.to_thread(stage_swmm_manifest, staging)
            handle = run_solver(
                solver=SWMM_SOLVER_NAME,
                model_setup_uri=manifest_uri,
                compute_class=effective_compute_class,
            )
            _progress_task = asyncio.ensure_future(
                drive_live_solve_progress(
                    emitter=current_emitter(),
                    run_id=staging.run_id,
                    solver=SWMM_SOLVER_NAME,
                    grid_resolution_m=getattr(staging.build, "resolution_m", None),
                    active_cell_count=getattr(
                        staging.build, "n_active_cells", None
                    ),
                    vcpus=int(_swmm_vcpus) if _swmm_vcpus is not None else None,
                    eta_seconds=estimate_swmm_solve_seconds(
                        int(getattr(staging.build, "n_active_cells", 0) or 0)
                    ),
                )
            )
            try:
                run_result = await wait_for_completion(handle)
            except asyncio.CancelledError:
                # Invariant 8: the cancel chain is owned by wait_for_completion;
                # propagate immediately so the WS handler emits cancelled.
                logger.info("model_urban_flood_swmm cancelled while awaiting solver")
                raise
            finally:
                # Tear down the heartbeat (success, failure, OR cancel).
                _progress_task.cancel()
                try:
                    await _progress_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

            if run_result.status != "complete":
                # SOLVER_FAILED / SOLVER_TIMEOUT / cancelled -> typed failure
                # (mirror model_flood_scenario's non-complete guard). The
                # SWMMWorkflowError below is caught by the except clause + turned
                # into a typed error dict by the tool wrapper.
                raise SWMMWorkflowError(
                    "SWMM_LOCAL_RUN_FAILED",
                    message=(
                        "SWMM Batch solve did not complete "
                        f"(status={run_result.status}, "
                        f"error_code={run_result.error_code}): "
                        f"{run_result.error_message or run_result.cancellation_reason or ''}"
                    ),
                    details={
                        "run_id": staging.run_id,
                        "output_uri": run_result.output_uri,
                    },
                )

            # Download the Batch .out (+ .rpt for continuity provenance) to a
            # local tmp dir, then postprocess from a run-shim carrying the local
            # out_path (postprocess_swmm reads only run.out_path; the S_i_j
            # cell<->node map lives in staging.build, agent-side, unchanged).
            run, batch_out_dir = await asyncio.to_thread(
                _download_batch_swmm_outputs, run_result, staging.run_id
            )
            try:
                layers, metrics = await asyncio.to_thread(
                    postprocess_swmm,
                    run,
                    staging.build,
                    run_id=staging.run_id,
                    building_footprints=building_footprints,
                )
            finally:
                _cleanup_deck_dir(batch_out_dir)
        else:
            # --- In-process lane (DEFAULT): pyswmm in this venv ---------------
            # BREAK B (event-loop starvation): run_swmm_local is a SYNCHRONOUS
            # ~16-min pyswmm solve. Calling it inline on the async event loop
            # blocks the loop for the entire solve -> the WS keepalive ping
            # coroutine never runs -> the socket dies (ConnectionClosedError x40)
            # -> every later emit/persist lands on a dead socket and the terminal
            # layer never surfaces. The remedy is to push the blocking call OFF
            # the loop onto a worker thread so the loop stays responsive
            # (ping/pong keeps the WS alive) while pyswmm churns. run_swmm_deck
            # (the body of run_swmm_local) does NOT report progress through the
            # async PipelineEmitter mid-solve - it is a self-contained
            # synchronous compute with no loop-bound calls - so a plain to_thread
            # wrap is correct here: no asyncio.run_coroutine_threadsafe
            # marshaling / progress-queue draining is required (there are no
            # emitter calls to marshal back). When mid-solve emitter progress IS
            # added later, switch to run_coroutine_threadsafe(loop) inside the
            # worker. (Mirrors model_flood_scenario's asyncio.to_thread
            # off-loading of its blocking fetcher/solve stages.)
            _progress_task = asyncio.ensure_future(
                drive_live_solve_progress(
                    emitter=current_emitter(),
                    run_id=staging.run_id,
                    solver=SWMM_SOLVER_NAME,
                    grid_resolution_m=getattr(staging.build, "resolution_m", None),
                    active_cell_count=getattr(
                        staging.build, "n_active_cells", None
                    ),
                    vcpus=int(_swmm_vcpus) if _swmm_vcpus is not None else None,
                    eta_seconds=estimate_swmm_solve_seconds(
                        int(getattr(staging.build, "n_active_cells", 0) or 0)
                    ),
                )
            )
            try:
                run = await asyncio.to_thread(run_swmm_local, staging)
            finally:
                # Tear down the heartbeat (success, failure, OR cancel).
                _progress_task.cancel()
                try:
                    await _progress_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

            # --- Step 6: postprocess (rasterize node depths -> peak + frames) -
            # BREAK B, post-solve: postprocess_swmm is a SYNCHRONOUS compute
            # (pyswmm Output read + per-step grid scatter + COG rasterize/reproject
            # + S3 upload) - heavy blocking I/O + GDAL that would stall the loop
            # inline. It builds the peak + frame COGs OFF-LINE (its own internal
            # _emit_frame_layers only WRITES COGs - it does NOT touch the
            # loop-bound PipelineEmitter / add_loaded_layer; the emitter
            # add_loaded_layer happens back on the loop in _emit_frame_layers
            # below) so a plain to_thread wrap is correct - no
            # run_coroutine_threadsafe marshaling required.
            layers, metrics = await asyncio.to_thread(
                postprocess_swmm,
                run,
                staging.build,
                run_id=staging.run_id,
                building_footprints=building_footprints,
            )
    except (SWMMWorkflowError, PostprocessSWMMError):
        # Cleanup before re-raising - the tool wrapper turns these into a typed
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

    raw_peak = layers[0]
    frame_layers = layers[1:]

    # --- Step 7 (BREAK A): publish the PEAK COG through publish_layer ---------
    # postprocess_swmm returns the peak + frame COGs as RAW s3:// object URIs.
    # A raw object-store URI NEVER renders in MapLibre and the job-0254 emission
    # guardrail (layer_uri_emit) DROPS a renderable raster carrying s3:// - so
    # without publishing, the peak silently vanishes from the map and persists no
    # renderable loaded_layer (BREAK A). Mirror the SFINCS model_flood_scenario
    # Step-9 publish-or-honest-drop path: route the peak COG through publish_layer
    # (the _resolve_titiler_style_params render chokepoint) so it carries a
    # published /tiles or WMS URL before it is returned. The returned LayerURI's
    # dispatch-level emit_layer_uri seam then PASSES it (http(s) renders) and
    # persists it as a renderable primary loaded_layer.
    #
    # On publish failure we return the peak UNPUBLISHED (raw s3://): the dispatch
    # guardrail drops the dead raster from the map (honest - no broken row) while
    # the typed narration scalars (max_depth_m / flooded_area_km2 /
    # n_buildings_affected) still reach the LLM so the failure is narrated and the
    # job-0177 retry loop can re-attempt. The wrapper REQUIRES a SWMMDepthLayerURI
    # return, so we never drop the whole layer - only its renderability.
    # BREAK B, post-solve: _publish_peak_layer drives publish_layer (the COG
    # rasterize/reproject/upload + the publish-status time.sleep polls) - all
    # SYNCHRONOUS blocking work. It does NOT call the loop-bound PipelineEmitter
    # (the peak's add_loaded_layer fires at the dispatch site, held #6, on the
    # returned LayerURI - NOT inside this function), so offload the whole call off
    # the loop. A plain to_thread wrap is correct - no run_coroutine_threadsafe
    # marshaling required.
    peak = await asyncio.to_thread(_publish_peak_layer, raw_peak, staging.run_id)

    # --- Step 7b / 9b: publish + emit the per-frame animation layers OUT-OF-BAND
    # Mirrors model_flood_scenario Step-9b: each frame is a DISTINCT COG (distinct
    # runs-bucket key -> distinct published url -> no dedup collapse). Each frame
    # COG is published through publish_layer (renderable URL) and emitted in
    # ascending step order via emitter.add_loaded_layer so all N frames arrive as
    # one contiguous sequential group; the "Flood depth step N" name token is
    # preserved so the web detectSequentialGroups scrubber group forms. Frames are
    # emitted ONLY through the emitter (NOT returned), so they never reach
    # summarize_tool_result. When the emitter is None (direct/smoke/test) frame
    # emission is skipped - the frames still live in `layers` for tests to assert.
    emitted_frames = await _emit_frame_layers(emitter, frame_layers, staging.run_id)

    logger.info(
        "model_urban_flood_swmm complete run_id=%s max_depth_m=%.4g "
        "flooded_area_km2=%.6g n_buildings_affected=%d frames_emitted=%d/%d "
        "continuity=%+.3f%% peak_uri=%s",
        staging.run_id,
        peak.max_depth_m,
        peak.flooded_area_km2,
        peak.n_buildings_affected,
        emitted_frames,
        len(frame_layers),
        run.continuity_error_pct,
        peak.uri,
    )

    # --- Step 8: cleanup the scratch deck (COGs already uploaded) -----------
    if cleanup_deck and deck_dir_to_clean:
        _cleanup_deck_dir(deck_dir_to_clean)

    # The PEAK SWMMDepthLayerURI is returned directly - the emit_tool_call
    # add_loaded_layer gate fires on it (a LayerURI subtype) and persists it as a
    # renderable primary loaded_layer. Invariant 1: the agent narrates
    # peak.max_depth_m / .flooded_area_km2 / .n_buildings_affected.
    return peak


def _publish_peak_layer(
    raw_peak: SWMMDepthLayerURI, run_id: str
) -> SWMMDepthLayerURI:
    """Publish the PEAK depth COG through publish_layer (BREAK A render chokepoint).

    Routes the raw s3:// peak COG through ``publish_layer`` (the
    ``_resolve_titiler_style_params`` render seam) and returns a NEW
    ``SWMMDepthLayerURI`` carrying the published /tiles or WMS URL plus the
    narration scalars + echoed barriers. On publish failure (e.g. QGIS-on-AWS not
    yet landed - job-0308) the raw peak is returned UNCHANGED: the dispatch-level
    ``emit_layer_uri`` guardrail then drops the dead raw-s3:// raster from the map
    (honest - no broken layer row) while the typed metrics still narrate. The
    wrapper requires a ``SWMMDepthLayerURI`` return, so we never drop the layer
    object itself - only its renderability degrades.

    Mirrors the SFINCS ``model_flood_scenario`` Step-9 primary publish (a raster
    carrying a raw object-store URI takes the publish-or-honest-drop gate).
    """
    if raw_peak.layer_type != "raster" or not (
        raw_peak.uri.startswith("gs://") or raw_peak.uri.startswith("s3://")
    ):
        # Already a renderable URL (defensive) - return as-is.
        return raw_peak
    layer_id_for_pub = f"swmm-depth-peak-{run_id}"
    try:
        published_uri = publish_layer(
            layer_uri=raw_peak.uri,
            layer_id=layer_id_for_pub,
            style_preset=raw_peak.style_preset or FLOOD_DEPTH_STYLE_PRESET,
        )
    except PublishLayerError as exc:
        logger.warning(
            "model_urban_flood_swmm: publish_layer FAILED for the peak "
            "layer_id=%s error_code=%s (%s) - returning the unpublished peak. "
            "Its raw s3:// uri never renders, so the dispatch guardrail drops it "
            "from the map; the depth metrics still narrate honestly and the "
            "retry-on-failure loop (job-0177) can re-attempt publish.",
            layer_id_for_pub,
            exc.error_code,
            exc,
        )
        return raw_peak
    # Substitute the published URL into a fresh SWMMDepthLayerURI so the returned
    # layer renders directly while preserving the narration scalars + barriers.
    return SWMMDepthLayerURI(
        layer_id=layer_id_for_pub,
        name=raw_peak.name,
        layer_type=raw_peak.layer_type,
        uri=published_uri,
        style_preset=raw_peak.style_preset or FLOOD_DEPTH_STYLE_PRESET,
        role=raw_peak.role,
        units=raw_peak.units,
        bbox=raw_peak.bbox,
        max_depth_m=raw_peak.max_depth_m,
        flooded_area_km2=raw_peak.flooded_area_km2,
        n_buildings_affected=raw_peak.n_buildings_affected,
        barriers=raw_peak.barriers,
    )


async def _emit_frame_layers(
    emitter: Any, frame_layers: list[SWMMDepthLayerURI], run_id: str
) -> int:
    """Publish + emit per-frame depth COGs out-of-band so the web scrubber forms.

    Each frame COG is routed through ``publish_layer`` (BREAK A render chokepoint)
    so it carries a renderable /tiles or WMS URL before ``add_loaded_layer``;
    without this every frame is a raw s3:// COG the job-0254 guardrail drops, so
    the scrubber group never forms on the map. The "Flood depth step N" name token
    is preserved so the web ``detectSequentialGroups`` groups them. A frame that
    fails to publish is HONESTLY DROPPED (its raw uri never renders) - the
    remaining frames + the peak stay intact; if too many drop the group may fall
    below 2 members and simply not form (acceptable, never a fake row).

    Returns the number of frames emitted (0 when no emitter is bound - the
    direct/smoke/test path). Never raises - a frame publish/emit failure must not
    sink the peak layer (the postprocess_flood honesty stance carried into the
    composer).
    """
    if not frame_layers or emitter is None:
        if frame_layers:
            logger.info(
                "model_urban_flood_swmm: %d animation frames available but no "
                "emitter bound (direct/smoke/test) - frames not emitted.",
                len(frame_layers),
            )
        return 0
    emitted = 0
    for lyr in frame_layers:
        # Defensive: a frame that is already a renderable URL (not raw object
        # store) emits as-is; otherwise publish it through the render chokepoint.
        if not (lyr.uri.startswith("gs://") or lyr.uri.startswith("s3://")):
            emit_layer: LayerURI = lyr
        else:
            try:
                # BREAK B, post-solve: offload ONLY the publish_layer compute
                # (COG rasterize/reproject/upload + the publish-status time.sleep
                # polls - SYNCHRONOUS blocking work) off the loop. The
                # add_loaded_layer emit MUST stay on the loop (it is loop-bound),
                # so this thread-offloads the per-frame publish and then emits on
                # the loop below - NEVER the whole emit loop.
                frame_uri = await asyncio.to_thread(
                    publish_layer,
                    layer_uri=lyr.uri,
                    layer_id=lyr.layer_id,
                    style_preset=lyr.style_preset or FLOOD_DEPTH_STYLE_PRESET,
                )
            except PublishLayerError as exc:
                logger.warning(
                    "model_urban_flood_swmm: publish_layer FAILED for frame "
                    "layer_id=%s error_code=%s (%s) - dropping this frame from "
                    "the animation group (its raw s3:// uri never renders).",
                    lyr.layer_id,
                    exc.error_code,
                    exc,
                )
                continue
            # Keep the "Flood depth step N" name token so the web grouping forms.
            emit_layer = SWMMDepthLayerURI(
                layer_id=lyr.layer_id,
                name=lyr.name,
                layer_type=lyr.layer_type,
                uri=frame_uri,
                style_preset=lyr.style_preset or FLOOD_DEPTH_STYLE_PRESET,
                role=lyr.role,
                units=lyr.units,
                bbox=lyr.bbox,
                max_depth_m=lyr.max_depth_m,
                flooded_area_km2=lyr.flooded_area_km2,
                n_buildings_affected=lyr.n_buildings_affected,
                barriers=lyr.barriers,
            )
        try:
            await emitter.add_loaded_layer(emit_layer)
            emitted += 1
        except Exception as exc:  # noqa: BLE001 - never break the solve
            logger.warning(
                "model_urban_flood_swmm: frame add_loaded_layer failed for %s: %s",
                emit_layer.layer_id,
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


class _BatchSWMMRun:
    """A minimal ``swmm_mesh_builder.RunResult`` shim for the Batch lane.

    ``postprocess_swmm`` reads ONLY ``run.out_path`` (the local pyswmm ``.out``)
    plus ``run.continuity_error_pct`` for narration provenance; the S_i_j
    cell<->node map lives in ``staging.build`` (agent-side, unchanged). The Batch
    worker solved the deck remotely and uploaded the ``.out``/``.rpt`` to the
    runs bucket, so we hand postprocess a shim carrying the DOWNLOADED local
    ``out_path`` (+ the continuity read from the downloaded ``.rpt``). No change
    to ``postprocess_swmm`` is required (Change 3: do the download in the
    composer, keep postprocess minimal)."""

    def __init__(self, out_path: str, continuity_error_pct: float) -> None:
        self.out_path = out_path
        self.continuity_error_pct = continuity_error_pct


def _download_batch_swmm_outputs(run_result: Any, run_id: str) -> tuple[Any, str]:
    """Download the Batch ``.out`` (+ ``.rpt``) to a tmp dir for postprocess.

    The SWMM Batch worker (``services/workers/swmm/entrypoint.py``) uploads the
    ``mesh.out`` / ``mesh.rpt`` it produced under
    ``s3://<runs_bucket>/<run_id>/`` and records their full URIs in the
    completion.json ``output_uris``. We re-read completion.json (small, already
    on S3) to find the EXACT ``.out``/``.rpt`` keys (robust to the deck filename),
    download them via the SAME boto3 client the solver dispatch uses (no new
    client), read continuity from the ``.rpt`` (``swmm_mesh_builder``'s
    ``read_flow_routing_continuity``), and return a run-shim carrying the local
    ``out_path`` + a tmp-dir path for the caller to clean up.

    Args:
        run_result: the terminal ``RunResult`` from ``wait_for_completion``
            (``output_uri = s3://<runs_bucket>/<run_id>/``).
        run_id: the run id the outputs are keyed under.

    Returns:
        ``(_BatchSWMMRun, tmp_dir)`` — feed the shim to ``postprocess_swmm`` and
        pass ``tmp_dir`` to ``_cleanup_deck_dir`` afterward.

    Raises:
        SWMMWorkflowError("SWMM_BATCH_OUTPUT_MISSING"): the completed run did not
            produce a downloadable ``.out`` (a 'complete' solve with no output is
            a real failure - never a silent dead-end).
    """
    from ..tools.solver import (
        _get_runs_bucket,
        _get_s3_client,
        _split_object_uri,
        _try_get_completion_s3,
    )
    from .swmm_mesh_builder import read_flow_routing_continuity

    runs_bucket = _get_runs_bucket()
    s3 = _get_s3_client()

    # Resolve the exact .out/.rpt object keys from completion.json output_uris;
    # fall back to the conventional mesh.out / mesh.rpt under the runs prefix.
    out_keys: list[str] = []
    rpt_keys: list[str] = []
    manifest = _try_get_completion_s3(runs_bucket, run_id)
    if isinstance(manifest, dict):
        for raw in manifest.get("output_uris") or []:
            uri = str(raw)
            try:
                _scheme, _bucket, key = _split_object_uri(uri)
            except Exception:  # noqa: BLE001 — skip an unparseable entry
                continue
            if key.endswith(".out"):
                out_keys.append(key)
            elif key.endswith(".rpt"):
                rpt_keys.append(key)
    if not out_keys:
        out_keys = [f"{run_id}/mesh.out"]
    if not rpt_keys:
        rpt_keys = [f"{run_id}/mesh.rpt"]

    tmp_dir = tempfile.mkdtemp(prefix=f"swmm-batch-out-{run_id}-")

    def _download(key: str) -> str | None:
        dest = Path(tmp_dir) / Path(key).name
        try:
            resp = s3.get_object(Bucket=runs_bucket, Key=key)
            with dest.open("wb") as fh:
                shutil.copyfileobj(resp["Body"], fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SWMM Batch output download failed s3://%s/%s: %s",
                runs_bucket,
                key,
                exc,
            )
            return None
        return str(dest)

    local_out = next((p for p in (_download(k) for k in out_keys) if p), None)
    if local_out is None:
        _cleanup_deck_dir(tmp_dir)
        raise SWMMWorkflowError(
            "SWMM_BATCH_OUTPUT_MISSING",
            message=(
                f"SWMM Batch run {run_id} completed but produced no downloadable "
                f".out under s3://{runs_bucket}/{run_id}/ "
                f"(looked for {out_keys!r})"
            ),
            details={"run_id": run_id, "runs_bucket": runs_bucket},
        )

    local_rpt = next((p for p in (_download(k) for k in rpt_keys) if p), None)
    continuity = 0.0
    if local_rpt is not None:
        try:
            cont = read_flow_routing_continuity(local_rpt)
            if cont is not None:
                continuity = float(cont)
        except Exception as exc:  # noqa: BLE001 — provenance only; never fatal
            logger.warning(
                "SWMM Batch .rpt continuity read failed (%s): %s", local_rpt, exc
            )

    return _BatchSWMMRun(out_path=local_out, continuity_error_pct=continuity), tmp_dir
