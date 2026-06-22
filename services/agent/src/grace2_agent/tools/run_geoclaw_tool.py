"""Atomic tool ``run_geoclaw_inundation`` — GeoClaw (Clawpack) shallow-water
inundation engine (sprint-17).

The LLM-facing exposure of the GeoClaw shallow-water engine (tsunami run-up /
dam-break / surge run-up - a hazard family SFINCS/SWMM do not cover).
``run_geoclaw_inundation(...)`` takes the ``GeoClawRunArgs`` scenario/forcing
fields, runs the deterministic fetch -> stage -> Batch-solve -> postprocess chain
(``workflows/model_dambreak_geoclaw_scenario.py``), and returns a
``GeoClawDepthLayerURI`` the emitter loads onto the map (it subclasses
``LayerURI`` so the ``emit_tool_call`` ``add_loaded_layer`` gate fires).

This is the GeoClaw analogue of ``run_swmm_urban_flood`` (SWMM) /
``run_modflow_job`` (MODFLOW) / ``run_model_flood_scenario`` (SFINCS). Like those
wrappers it declares ``cacheable=False`` + ``ttl_class="live-no-cache"`` +
``source_class="workflow_dispatch"`` (FR-DC-6 - workflow exposure surface; never
touches the cache shim). Confirmation before consequence (Invariant 9 - a solver
run) is enforced by the server confirmation hook around this tool.

GeoClaw is BATCH-ONLY (the Clawpack Fortran lives in the worker container image,
never in the agent venv), so unlike SWMM this always dispatches to AWS Batch.

Determinism boundary (Invariant 1): every depth number the agent narrates comes
from the typed ``GeoClawDepthLayerURI.max_depth_m`` / ``.flooded_area_km2`` /
``.max_inundation_m`` fields the postprocess computed - never free-generated.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grace2_contracts.geoclaw_contracts import (
    GeoClawDepthLayerURI,
    GeoClawRunArgs,
)
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from ..tool_arg_normalizer import coerce_bbox_value
from ..workflows.model_dambreak_geoclaw_scenario import (
    GeoClawComposerError,
    model_dambreak_geoclaw_scenario,
)
from ..workflows.postprocess_geoclaw import PostprocessGeoClawError
from ..workflows.run_geoclaw import GeoClawWorkflowError

logger = logging.getLogger("grace2_agent.tools.run_geoclaw_tool")

__all__ = ["run_geoclaw_inundation", "RunGeoClawError"]


class RunGeoClawError(RuntimeError):
    """Raised when the GeoClaw chain fails fatally before producing a layer."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


_RUN_GEOCLAW_METADATA = AtomicToolMetadata(
    name="run_geoclaw_inundation",
    ttl_class="live-no-cache",
    source_class="workflow_dispatch",
    cacheable=False,
)


@register_tool(
    _RUN_GEOCLAW_METADATA,
    # readOnlyHint=False (runs a solver writing output COG artifacts),
    # openWorldHint=False (Batch worker + intra-cloud object store),
    # destructiveHint=False (writes go to a new runs/ prefix),
    # idempotentHint=False (each call mints a new run_id + COG keys).
    read_only_hint=False,
    open_world_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
)
async def run_geoclaw_inundation(
    bbox: tuple[float, float, float, float] | list[float] | str | None = None,
    scenario: str = "dam_break",
    sim_duration_s: float = 3600.0,
    dam_break_depth_m: float = 10.0,
    source_lonlat: tuple[float, float] | list[float] | None = None,
    source_magnitude: float = 8.0,
    tsunami_dtopo_uri: str | None = None,
    surge_forcing_uri: str | None = None,
    output_frames: int = 24,
    amr_levels: int = 2,
    manning_n: float = 0.025,
    sea_level_m: float = 0.0,
    compute_class: str = "standard",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> GeoClawDepthLayerURI | dict[str, Any]:
    """Run a GeoClaw (Clawpack) shallow-water inundation simulation over an AOI.

    Solves the 2D nonlinear shallow-water equations over real topography with
    adaptive mesh refinement, producing a peak overland-depth COG + a per-timestep
    depth-frame animation group. Covers a hazard family the other flood engines do
    not: TSUNAMI run-up, DAM-BREAK / embankment-failure overland flow, and
    shallow-water SURGE run-up.

    Use this when:
        - The user asks to model a TSUNAMI, a DAM BREAK / embankment or levee
          FAILURE / reservoir release, or shallow-water storm-SURGE RUN-UP over an
          AOI, and wants the resulting inundation depth + animation.

    Do NOT use this for:
        - Rain-driven riverine / coastal compound flooding (use
          ``run_model_flood_scenario`` - that is SFINCS).
        - Urban / pluvial / drainage / stormwater flooding (use
          ``run_swmm_urban_flood`` - that is SWMM).
        - Groundwater contamination plumes (use ``run_modflow_job``).

    Params:
        bbox: the computational-domain AOI as ``(min_lon, min_lat, max_lon,
            max_lat)`` in EPSG:4326 (lon-first).
        scenario: the driver family, EXACTLY one of {"dam_break", "tsunami",
            "surge"}. ``"dam_break"`` (DEFAULT) releases a raised water column over
            dry topo; ``"tsunami"`` drives a seafloor-displacement source;
            ``"surge"`` applies a raised sea surface. Synonyms (e.g. "wave" ->
            tsunami, "breach" -> dam_break) are normalized.
        sim_duration_s: simulated physical time, seconds (> 0). Default 3600.
        dam_break_depth_m: for ``scenario="dam_break"`` ONLY - the height (m) of
            the raised water column released at t=0. Default 10.
        source_lonlat: OPTIONAL ``(lon, lat)`` of the driver source (dam centroid
            / tsunami epicentre). When unset the AOI centroid is used.
        source_magnitude: for ``scenario="tsunami"`` synthetic-source mode - the
            moment magnitude (Mw) scaling the Okada slip. Default 8.0.
        tsunami_dtopo_uri: for ``scenario="tsunami"`` - OPTIONAL ``s3://`` URI of a
            prescribed dtopo file (else a synthetic Okada source is built).
        surge_forcing_uri: for ``scenario="surge"`` - OPTIONAL ``s3://`` URI of a
            sea-surface hydrograph CSV.
        output_frames: number of evenly-spaced fort.q output frames (>= 1).
            Drives the animation frame count. Default 24.
        amr_levels: AMR refinement levels (>= 1; 1 = uniform grid). Default 2.
        manning_n: shallow-water friction Manning n (> 0). Default 0.025.
        sea_level_m: still-water datum (m). Default 0.0.
        compute_class: FR-CE-3 compute class. Default ``"standard"``.

    Returns:
        On success: a ``GeoClawDepthLayerURI`` (a ``LayerURI`` subtype) - the
        emitter appends it to ``session-state.loaded_layers`` and the map renders
        the peak depth COG. It carries ``max_depth_m`` + ``flooded_area_km2`` +
        ``max_inundation_m`` (Invariant 1 - the agent narrates these typed
        numbers, never invents them). The per-timestep depth frames are emitted
        out-of-band as a temporal scrubber group.

        On failure: a dict with ``status="error"`` + ``error_code`` +
        ``error_message`` so the LLM narrates the failure honestly (no layer).

    FR-DC-6: ``cacheable=False`` + ``ttl_class="live-no-cache"`` +
    ``source_class="workflow_dispatch"`` - the cache shim is NOT invoked.
    """
    # --- Validate + coerce into the GeoClawRunArgs contract -----------------
    if bbox is None:
        return {
            "status": "error",
            "error_code": "GEOCLAW_PARAMS_INCOMPLETE",
            "error_message": (
                "run_geoclaw_inundation requires a bbox "
                "(min_lon, min_lat, max_lon, max_lat) in EPSG:4326."
            ),
        }
    coerced = coerce_bbox_value(bbox)
    if coerced is None:
        return {
            "status": "error",
            "error_code": "GEOCLAW_PARAMS_INVALID",
            "error_message": (
                f"invalid bbox (expected 4 numbers min_lon,min_lat,max_lon,max_lat): "
                f"{bbox!r}"
            ),
        }
    try:
        kwargs: dict[str, Any] = dict(
            bbox=tuple(coerced),  # type: ignore[arg-type]
            scenario=scenario,
            sim_duration_s=float(sim_duration_s),
            dam_break_depth_m=float(dam_break_depth_m),
            source_magnitude=float(source_magnitude),
            output_frames=int(output_frames),
            amr_levels=int(amr_levels),
            manning_n=float(manning_n),
            sea_level_m=float(sea_level_m),
        )
        if source_lonlat is not None:
            sl = list(source_lonlat)
            if len(sl) == 2:
                kwargs["source_lonlat"] = (float(sl[0]), float(sl[1]))
        if tsunami_dtopo_uri:
            kwargs["tsunami_dtopo_uri"] = str(tsunami_dtopo_uri)
        if surge_forcing_uri:
            kwargs["surge_forcing_uri"] = str(surge_forcing_uri)
        run_args = GeoClawRunArgs(**kwargs)
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError or coercion
        return {
            "status": "error",
            "error_code": "GEOCLAW_PARAMS_INVALID",
            "error_message": f"invalid GeoClaw run arguments: {exc}",
        }

    logger.info(
        "run_geoclaw_inundation bbox=%s scenario=%s duration=%.0fs frames=%d "
        "amr_levels=%d",
        run_args.bbox,
        run_args.scenario,
        run_args.sim_duration_s,
        run_args.output_frames,
        run_args.amr_levels,
    )

    try:
        peak = await model_dambreak_geoclaw_scenario(
            run_args,
            compute_class=compute_class,
        )
        logger.info(
            "run_geoclaw_inundation complete layer_id=%s scenario=%s "
            "max_depth_m=%.4g flooded_area_km2=%.6g max_inundation_m=%.4g uri=%s",
            peak.layer_id,
            peak.scenario,
            peak.max_depth_m,
            peak.flooded_area_km2,
            peak.max_inundation_m,
            peak.uri,
        )
        return peak
    except asyncio.CancelledError:
        raise
    except (
        GeoClawWorkflowError,
        PostprocessGeoClawError,
        GeoClawComposerError,
    ) as exc:
        logger.warning(
            "run_geoclaw_inundation failed: %s (%s)", exc.error_code, exc
        )
        return {
            "status": "error",
            "error_code": exc.error_code,
            "error_message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 — defensive catch-all
        logger.exception("run_geoclaw_inundation unexpected failure")
        return {
            "status": "error",
            "error_code": "GEOCLAW_INTERNAL_ERROR",
            "error_message": str(exc),
        }
