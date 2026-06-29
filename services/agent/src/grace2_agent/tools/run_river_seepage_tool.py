"""Atomic tool ``run_river_seepage_job`` -- MODFLOW river-seepage engine (J9).

The LLM-facing exposure of the sprint-17 MODFLOW 6 RIVER-SEEPAGE extension: a
RIV head-dependent river<->aquifer flux boundary draped onto the structured GWF
grid, plus an along-river SRC solute source, on top of the existing
``run_modflow_job`` GWF+GWT engine. It REUSES the live MODFLOW solver path
(``workflows/run_modflow.py`` deck-build -> submit/local-run, the ``modflow``
Batch job-def, ``services/workers/modflow/gwt_adapter.py``) -- there is NO new
worker, container, or Batch job-def.

Chain (mirrors ``run_modflow_job`` with the river extension):

  1. Build + stage a GWF(+RIV)+GWT(+SRC) deck. The deck adapter
     (``gwt_adapter.build_modflow_deck``) drapes the ``river_geometry_uri``
     flowline onto the grid as RIV cells (per-cell stage/rbot/conductance from
     the DEM or demo defaults) and -- when ``along_river_source`` is True --
     distributes the SRC contaminant load along the reach (the seepage source
     enters where the river leaks into the aquifer).
  2. Run mf6 (AWS Batch ``modflow`` job-def, or local ``mf6`` when
     ``GRACE2_MODFLOW_LOCAL=1``) -- the SAME submit/wait/cancel seam as
     ``run_modflow_job``.
  3. Postprocess TWO layers:
       * ``postprocess_river_seepage`` reads the GWF ``gwf_model.cbc`` RIV
         leakage budget into a DIVERGING gaining/losing-stream COG (the
         river-seepage North Star layer) -> a ``SeepageLayerURI`` carrying the
         leakage narration scalars (total / gaining / losing / cell-count).
       * ``postprocess_modflow`` reads the GWT UCN into the contaminant plume
         COG -> a ``PlumeLayerURI`` (the solute that entered with the seepage).
  4. Return both as LayerURIs so the emitter's ``add_loaded_layer`` gate loads
     both onto the map (the seepage layer is ``role="primary"``, the plume
     ``role="context"``).

Determinism boundary (Invariant 1): every narrated number comes from the typed
``SeepageLayerURI`` / ``PlumeLayerURI`` fields the postprocess computed -- never
free-generated.

FR-DC-6: ``cacheable=False`` + ``ttl_class="live-no-cache"`` +
``source_class="workflow_dispatch"`` -- the cache shim is NOT invoked.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from grace2_contracts.execution import ComputeClass, RunResult
from grace2_contracts.modflow_contracts import (
    MODFLOWRunArgs,
    PlumeLayerURI,
    SeepageLayerURI,
)
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from ..pipeline_emitter import current_emitter
from ..tool_arg_normalizer import LatLonCoercionError, coerce_latlon
from ..workflows.postprocess_modflow import (
    PostprocessMODFLOWError,
    postprocess_modflow,
    postprocess_river_seepage,
)
from ..workflows.run_modflow import (
    MODFLOWWorkflowError,
    build_and_stage_modflow_deck,
    is_local_mode,
    run_modflow_local,
    submit_modflow_run,
)
from ..workflows.solve_progress import drive_live_solve_progress

logger = logging.getLogger("grace2_agent.tools.run_river_seepage_tool")

__all__ = ["run_river_seepage_job", "RunRiverSeepageError"]


class RunRiverSeepageError(RuntimeError):
    """Raised when the river-seepage chain fails fatally before a layer."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


_RUN_RIVER_SEEPAGE_METADATA = AtomicToolMetadata(
    name="run_river_seepage_job",
    ttl_class="live-no-cache",
    source_class="workflow_dispatch",
    cacheable=False,
)


@register_tool(
    _RUN_RIVER_SEEPAGE_METADATA,
    # readOnlyHint=False (submits a solver run), openWorldHint=False (intra-AWS
    # Batch / local mf6), destructiveHint=False (writes a new runs/ prefix),
    # idempotentHint=False (each call mints a new run + Batch job).
    read_only_hint=False,
    open_world_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
)
async def run_river_seepage_job(
    spill_location_latlon: tuple[float, float] | list[float] | str | None = None,
    contaminant: str | None = None,
    release_rate_kg_s: float | None = None,
    duration_days: float | None = None,
    river_geometry_uri: str | None = None,
    river_stage_m: float | None = None,
    river_stage_depth_m: float | None = None,
    streambed_conductance_m2_day: float | None = None,
    along_river_source: bool = True,
    aquifer_k_ms: float | None = None,
    porosity: float | None = None,
    compute_class: ComputeClass = "standard",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> SeepageLayerURI | dict[str, Any]:
    """Run raw MODFLOW 6 river<->aquifer seepage + solute -- river geometry already held (raw job, no geocoding/fetch).

    Use this when:
        - You ALREADY have the river geometry URI + coordinates and want
          gaining vs losing reaches / baseflow / streambed seepage.
        - A contaminant enters the groundwater ALONG a river / stream you already
          resolved, and you want the river-coupled plume.

    Do NOT use this for:
        - A PLACE name where you still need geocoding + a river-geometry fetch --
          use run_model_river_seepage_scenario (the composer does both for you).
        - A point spill with NO river -- use run_modflow_job.
        - Surface-water / inundation flooding -- run_model_flood_scenario (SFINCS).

    Never fabricates inputs; missing river_geometry_uri -> typed error (a plain
    spill belongs on run_modflow_job).

    Returns a SeepageLayerURI that auto-renders (carries total_leakage_m3_day +
    gaining_m3_day + losing_m3_day + river_cell_count the agent narrates,
    Invariant 1); the contaminant plume loads alongside via the emitter -- do not
    call publish_layer. On failure returns a dict with status="error" +
    error_code + error_message.

    Params (condensed):
        spill_location_latlon: ``(lat, lon)`` scenario centre / spill, EPSG:4326
            (lat-first); the grid centres here and the river is draped onto it.
        contaminant: contaminant name (e.g. "TCE"); conservative tracer.
        release_rate_kg_s: mass-release rate, kg/s (> 0).
        duration_days: release + transport duration, days (> 0).
        river_geometry_uri: FlatGeobuf / GeoJSON URI of the river flowline (from
            fetch_river_geometry / NLDI). REQUIRED -- without it use run_modflow_job.
        river_stage_m / river_stage_depth_m / streambed_conductance_m2_day:
            optional RIV overrides; demo defaults applied when None.
        along_river_source: True (default) places the SRC along the reach (the
            seepage source); False keeps it at the spill point.
        aquifer_k_ms / porosity: optional demo-aquifer overrides.
        compute_class: FR-CE-3 compute class (default "standard").

    Execution: cloud Batch mf6 by default (SAME submit/wait/cancel seam as
    run_modflow_job), or local mf6 when GRACE2_MODFLOW_LOCAL=1. FR-DC-6:
    cacheable=False / live-no-cache / workflow_dispatch -- cache shim not invoked.
    """
    # --- Validate required params ------------------------------------------
    if (
        spill_location_latlon is None
        or contaminant is None
        or release_rate_kg_s is None
        or duration_days is None
    ):
        return {
            "status": "error",
            "error_code": "RIVER_SEEPAGE_PARAMS_INCOMPLETE",
            "error_message": (
                "run_river_seepage_job requires spill_location_latlon, "
                "contaminant, release_rate_kg_s, and duration_days."
            ),
        }
    if not river_geometry_uri:
        return {
            "status": "error",
            "error_code": "RIVER_SEEPAGE_NO_RIVER",
            "error_message": (
                "run_river_seepage_job requires river_geometry_uri (the river "
                "flowline to drape onto the grid). For a point spill with no "
                "river coupling, use run_modflow_job instead."
            ),
        }
    try:
        loc = tuple(coerce_latlon(spill_location_latlon))  # -> (lat, lon)
    except LatLonCoercionError as exc:
        return {
            "status": "error",
            "error_code": "RIVER_SEEPAGE_PARAMS_INVALID",
            "error_message": f"invalid spill_location_latlon (expected lat,lon): {exc}",
        }
    try:
        kwargs: dict[str, Any] = dict(
            spill_location_latlon=loc,  # type: ignore[arg-type]
            contaminant=contaminant,
            release_rate_kg_s=float(release_rate_kg_s),
            duration_days=float(duration_days),
            river_geometry_uri=river_geometry_uri,
            along_river_source=bool(along_river_source),
        )
        if river_stage_m is not None:
            kwargs["river_stage_m"] = float(river_stage_m)
        if river_stage_depth_m is not None:
            kwargs["river_stage_depth_m"] = float(river_stage_depth_m)
        if streambed_conductance_m2_day is not None:
            kwargs["streambed_conductance_m2_day"] = float(streambed_conductance_m2_day)
        if aquifer_k_ms is not None:
            kwargs["aquifer_k_ms"] = float(aquifer_k_ms)
        if porosity is not None:
            kwargs["porosity"] = float(porosity)
        run_args = MODFLOWRunArgs(**kwargs)
    except Exception as exc:  # noqa: BLE001 -- pydantic ValidationError or coercion
        return {
            "status": "error",
            "error_code": "RIVER_SEEPAGE_PARAMS_INVALID",
            "error_message": f"invalid river-seepage run arguments: {exc}",
        }

    logger.info(
        "run_river_seepage_job spill=%s contaminant=%r rate=%s kg/s duration=%s d "
        "river=%s along_src=%s local=%s",
        run_args.spill_location_latlon,
        run_args.contaminant,
        run_args.release_rate_kg_s,
        run_args.duration_days,
        river_geometry_uri,
        run_args.along_river_source,
        is_local_mode(),
    )

    staging = None
    try:
        # --- Step 1: build + stage the river-coupled deck (off-loop) --------
        staging = await asyncio.to_thread(build_and_stage_modflow_deck, run_args)
        if not staging.river_coupled:
            return {
                "status": "error",
                "error_code": "RIVER_SEEPAGE_NO_RIV_CELLS",
                "error_message": (
                    "the river flowline did not intersect the model grid, so no "
                    "RIV reach cells were written. Check the river geometry "
                    "overlaps the spill location."
                ),
            }

        # --- Step 2: run the solver (local or Batch) -----------------------
        if is_local_mode():
            _progress_task = asyncio.ensure_future(
                drive_live_solve_progress(
                    emitter=current_emitter(),
                    run_id=staging.run_id,
                    solver="modflow",
                    grid_resolution_m=None,
                    active_cell_count=None,
                    vcpus=None,
                    eta_seconds=None,
                )
            )
            try:
                run_outputs_uri = await asyncio.to_thread(run_modflow_local, staging)
            finally:
                _progress_task.cancel()
                try:
                    await _progress_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        else:
            handle = await asyncio.to_thread(
                submit_modflow_run, staging, compute_class=compute_class
            )
            from .solver import wait_for_completion

            try:
                run_result: RunResult = await wait_for_completion(handle)
            except asyncio.CancelledError:
                logger.info("run_river_seepage_job cancelled while awaiting solver")
                raise
            if run_result.status != "complete":
                return {
                    "status": "error",
                    "error_code": run_result.error_code or run_result.status.upper(),
                    "error_message": (
                        run_result.error_message
                        or run_result.cancellation_reason
                        or "MODFLOW river-seepage solver did not complete"
                    ),
                }
            run_outputs_uri = (
                run_result.output_uri or f"gs://{_runs_prefix()}/{run_result.run_id}/"
            )

        # --- Step 3a: postprocess the GWF cbc RIV budget -> seepage layer ---
        seepage: SeepageLayerURI = await asyncio.to_thread(
            lambda: postprocess_river_seepage(
                run_outputs_uri,
                run_id=staging.run_id,
                model_crs=staging.model_crs,
                deck_dir=staging.local_deck_dir,
            )
        )

        # --- Step 3b: postprocess the GWT UCN -> plume layer (best-effort) --
        # The plume is the contaminant that entered with the seepage. A plume
        # postprocess failure is NON-FATAL: the seepage layer is the headline.
        plume: PlumeLayerURI | None = None
        try:
            plume = await asyncio.to_thread(
                lambda: postprocess_modflow(
                    run_outputs_uri,
                    run_id=staging.run_id,
                    model_crs=staging.model_crs,
                    deck_dir=staging.local_deck_dir,
                )
            )
        except (PostprocessMODFLOWError, Exception) as exc:  # noqa: BLE001
            logger.warning("river-seepage plume postprocess failed (non-fatal): %s", exc)

        # Load the plume as a context layer alongside the primary seepage layer.
        if plume is not None:
            emitter = current_emitter()
            if emitter is not None:
                try:
                    plume_ctx = plume.model_copy(update={"role": "context"})
                    await emitter.add_loaded_layer(plume_ctx)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("could not add plume context layer: %s", exc)

        logger.info(
            "run_river_seepage_job complete run_id=%s total_leakage_m3_day=%.6g "
            "gaining=%.6g losing=%.6g cells=%d uri=%s",
            staging.run_id,
            seepage.total_leakage_m3_day,
            seepage.gaining_m3_day,
            seepage.losing_m3_day,
            seepage.river_cell_count,
            seepage.uri,
        )
        return seepage

    except asyncio.CancelledError:
        raise
    except (MODFLOWWorkflowError, PostprocessMODFLOWError) as exc:
        logger.warning("run_river_seepage_job failed: %s (%s)", exc.error_code, exc)
        return {
            "status": "error",
            "error_code": exc.error_code,
            "error_message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 -- defensive catch-all
        logger.exception("run_river_seepage_job unexpected failure")
        return {
            "status": "error",
            "error_code": "RIVER_SEEPAGE_INTERNAL_ERROR",
            "error_message": str(exc),
        }
    finally:
        # Best-effort cleanup of the local deck dir (the COGs were uploaded).
        if staging is not None:
            try:
                deck_base = Path(staging.local_deck_dir).parent
                if deck_base.name.startswith("modflow-"):
                    shutil.rmtree(deck_base, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass


def _runs_prefix() -> str:
    """Default runs bucket name for composing a fallback output prefix."""
    import os

    return os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")
