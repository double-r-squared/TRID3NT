"""model_flood_scenario workflow (M5 capstone — job-0042).

This module implements the **M5 capstone composition**:

    geocode_location (if location_query)
      → fetch_dem
      → fetch_landcover (with NLCD vintage_year sidecar per OQ-4 §4)
      → fetch_river_geometry
      → lookup_precip_return_period
      → build_sfincs_model        ← OQ-4 §4 NLCD validation gate fires here
      → run_solver(sfincs, model_setup_uri)
      → wait_for_completion(handle)
      → postprocess_flood
      → AssessmentEnvelope (Flood subtype, Appendix B.4)

Per Decision G + FR-TA-1, this workflow is **deterministic Python composition**
— there is no LLM in the chain. The workflow returns a typed
``AssessmentEnvelope`` whose ``flood: FloodPayload`` subtype carries the
narration metrics.

LLM exposure (workflow-as-atomic-tool-wrapper pattern):

    @register_tool(AtomicToolMetadata(name="run_model_flood_scenario",
                                       ttl_class="live-no-cache",
                                       source_class="workflow_dispatch",
                                       cacheable=False))
    def run_model_flood_scenario(bbox?, location_query?, ...) -> dict: ...

The wrapper forwards verbatim to ``model_flood_scenario`` and returns the
envelope's ``model_dump(mode="json")`` (a dict — the LLM tool surface doesn't
need the pydantic instance). The wrapper carries the FR-DC-6 ``cacheable=False``
flag because workflows are uncacheable (the whole point is the dispatch +
solver run + envelope build, never the cached return).

Partial-failure envelope shape (TENTATIVE per kickoff Open Questions):
    On any internal failure (fetcher exception, NLCD validation gate firing,
    SFINCS dispatch error, solver SOLVER_FAILED, postprocess error), the
    workflow still returns a typed ``AssessmentEnvelope`` — but with
    ``envelope_type="modeled"``, an empty layers list, and a
    ``FloodPayload`` carrying zero-valued metrics + the error code threaded
    into the ``solver_version`` field (a documented seam — see
    OQ-42-PARTIAL-FAILURE-ENVELOPE-SHAPE). The agent surface narrates the
    envelope honestly ("scenario could not be modeled because …") rather than
    fabricating depth values.

Cross-cutting principles in force:
- **Invariant 1 (Determinism boundary): preserves.** No LLM in the chain.
- **Invariant 2 (Deterministic workflows): preserves.** Straight-line
  composition; each step's failure surfaces as a typed exception caught at
  the workflow boundary.
- **Invariant 7 (no silent wrong answers): EXTENDS — the headline.** The
  ``build_sfincs_model`` NLCD validation gate is the load-bearing mitigation
  for OQ-4. ``LULC_MAPPING_MISMATCH`` is surfaced as a failed envelope, not a
  dispatched-broken-model SFINCS run.
- **Invariant 8 (Cancellation is first-class): preserves.** The workflow
  awaits ``wait_for_completion`` — any ``asyncio.CancelledError`` propagates
  through the workflow as-is, triggering the 850ms cancel chain from
  job-0041.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from grace2_contracts import new_ulid
from grace2_contracts.envelope import (
    AssessmentEnvelope,
    CriticalFacility,
    DataSource,
    FloodMetrics,
    FloodPayload,
    ForcingSummary,
    Provenance,
    ResultLayer,
)
from grace2_contracts.execution import LayerURI, RunResult
from grace2_contracts.tool_registry import AtomicToolMetadata

from ..tools import register_tool
from ..tools.data_fetch import (
    fetch_dem,
    fetch_landcover,
    fetch_river_geometry,
    geocode_location,
    lookup_precip_return_period,
)
from ..tools.solver import run_solver, wait_for_completion
from .postprocess_flood import PostprocessError, postprocess_flood
from .sfincs_builder import (
    BuildOptions,
    ForcingSpec,
    SFINCSSetupError,
    build_sfincs_model,
)

__all__ = [
    "model_flood_scenario",
    "run_model_flood_scenario",
    "WorkflowError",
]

logger = logging.getLogger("grace2_agent.workflows.model_flood_scenario")


# Default project/session identifiers for ULID-bearing envelope fields. The
# agent runtime threads real IDs through when WS state is present; the
# workflow itself accepts None and falls back to fresh ULIDs so a direct call
# (smoke harness, integration test) still produces a valid envelope.
_FALLBACK_PROJECT_ID = None
_FALLBACK_SESSION_ID = None


class WorkflowError(RuntimeError):
    """Raised by the workflow when composition fails fatally (rare).

    Most failure modes inside the workflow are surfaced as a typed
    AssessmentEnvelope with zero-valued metrics + the error code threaded
    through (per the partial-failure shape). ``WorkflowError`` is reserved
    for the case where even building a failed envelope isn't possible (e.g.
    geocoder returns no bbox AND no bbox was supplied).
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# --------------------------------------------------------------------------- #
# Helpers — bbox resolution + zero-metrics envelope builder
# --------------------------------------------------------------------------- #


def _resolve_bbox(
    *,
    bbox: tuple[float, float, float, float] | None,
    location_query: str | None,
) -> tuple[tuple[float, float, float, float], dict[str, Any] | None]:
    """Resolve the bbox via direct param or via ``geocode_location``.

    Precedence per the kickoff TENTATIVE: bbox-direct wins when both are
    given (matches the "intent + irreducible inputs" Decision K — bbox IS
    the irreducible input; geocode is a convenience).

    Returns:
        Tuple ``(bbox, geocode_result)``; ``geocode_result`` is the geocoder's
        return dict (carries canonical name + provenance) when geocoding was
        run, ``None`` when bbox was supplied directly.
    """
    if bbox is not None:
        if location_query is not None:
            logger.info(
                "model_flood_scenario: both bbox and location_query given; "
                "bbox-direct wins (decision K precedence)"
            )
        return bbox, None
    if location_query is None:
        raise WorkflowError(
            "BBOX_UNRESOLVABLE",
            "model_flood_scenario requires either bbox or location_query",
        )
    geo = geocode_location(location_query)
    bb = geo.get("bbox")
    if not bb or len(bb) != 4:
        raise WorkflowError(
            "GEOCODE_NO_BBOX",
            f"geocode_location({location_query!r}) returned no usable bbox: {geo!r}",
        )
    return (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])), geo


def _build_failed_envelope(
    *,
    bbox: tuple[float, float, float, float],
    project_id: str,
    session_id: str,
    error_code: str,
    error_detail: str,
    workflow_name: str,
    data_sources: list[DataSource],
    forcing: ForcingSummary | None,
    solver_run_ids: list[str],
    return_period_years: int,
    duration_hours: float,
    grid_resolution_m: float,
) -> AssessmentEnvelope:
    """Construct a typed failed-flood AssessmentEnvelope.

    Per OQ-42-PARTIAL-FAILURE-ENVELOPE-SHAPE (TENTATIVE): zero-valued
    FloodMetrics + error_code threaded into ``solver_version`` (a documented
    out-of-band seam — the schema-side ``solver_version`` is a string field
    so we can carry ``"failed:LULC_MAPPING_MISMATCH"`` etc. The agent surface
    parses this and emits a meaningful failure narration.)

    All required envelope fields are populated with safe defaults so the
    pydantic validator doesn't reject the failed envelope.
    """
    now = datetime.now(timezone.utc)
    return AssessmentEnvelope(
        envelope_id=new_ulid(),
        project_id=project_id,
        session_id=session_id,
        envelope_type="modeled",
        hazard_type="flood",
        workflow_name=workflow_name,
        bbox=bbox,
        crs="EPSG:4326",
        forcing=forcing,
        layers=[],
        provenance=Provenance(data_sources=data_sources),
        created_at=now,
        completed_at=now,
        solver_run_ids=solver_run_ids,
        flood=FloodPayload(
            metrics=FloodMetrics(
                flooded_area_km2=0.0,
                max_depth_m=0.0,
                mean_depth_m=0.0,
                p95_depth_m=0.0,
                solver_version=f"failed:{error_code}",
                grid_resolution_m=grid_resolution_m,
                simulation_duration_hours=int(duration_hours),
            )
        ),
    )


def _bbox_area_km2(bbox: tuple[float, float, float, float]) -> float:
    """Approximate WGS84 bbox area in km^2 (matches data_fetch helper)."""
    import math

    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = 0.5 * (min_lat + max_lat)
    dlat_km = (max_lat - min_lat) * 111.320
    dlon_km = (max_lon - min_lon) * 111.320 * math.cos(math.radians(mid_lat))
    return abs(dlat_km * dlon_km)


# --------------------------------------------------------------------------- #
# The workflow itself
# --------------------------------------------------------------------------- #


async def model_flood_scenario(
    bbox: tuple[float, float, float, float] | None = None,
    location_query: str | None = None,
    event_id: str | None = None,
    return_period_yr: int = 100,
    duration_hr: int = 24,
    compute_class: str = "medium",
    *,
    project_id: str | None = None,
    session_id: str | None = None,
) -> AssessmentEnvelope:
    """Compose the full M5 flood-modeling chain.

    Resolves the location (geocode if ``bbox`` not given), fetches DEM (3DEP)
    + landcover (NLCD) + river geometry (NHDPlus HR) + design-storm
    precipitation depth (NOAA Atlas 14), builds an SFINCS model via HydroMT
    (the OQ-4 §4 NLCD validation gate fires here — raises
    ``SFINCSSetupError("LULC_MAPPING_MISMATCH")`` on vintage mismatch),
    dispatches ``run_solver(sfincs, ...)``, awaits ``wait_for_completion``,
    postprocesses the run's NetCDF to a flood-depth COG, and returns a
    typed ``AssessmentEnvelope`` Flood subtype (Appendix B.4).

    On internal failure (fetch error, NLCD gate firing, SFINCS dispatch
    failure, SOLVER_FAILED, postprocess error), returns a typed
    AssessmentEnvelope with zero-valued ``FloodMetrics`` and the error code
    threaded into ``solver_version`` — never raises (caller-friendly).
    The agent surface narrates the failed envelope honestly.

    Args:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326. When
            ``None``, ``location_query`` is used to geocode.
        location_query: free-text place name (e.g. ``"Fort Myers, FL"``)
            geocoded via Nominatim. Ignored if ``bbox`` is supplied.
        event_id: optional event ID for provenance (HEP integration future
            hook; v0.1 carries it on the envelope's provenance dict).
        return_period_yr: design-storm ARI. Atlas 14 publishes
            ``{1, 2, 5, 10, 25, 50, 100, 200, 500, 1000}``. Default 100.
        duration_hr: design-storm duration in hours. Atlas 14 publishes a
            fixed row set; 24 hr is the v0.1 default.
        compute_class: FR-CE-3 compute class. Default ``"medium"``.
        project_id / session_id: ULID identifiers from the WS session. When
            ``None``, fresh ULIDs are minted (for direct-call / smoke).

    Returns:
        ``AssessmentEnvelope`` with ``envelope_type="modeled"``,
        ``hazard_type="flood"``, ``workflow_name="model_flood_scenario"``,
        and a populated ``flood: FloodPayload``. On success, ``layers``
        contains the flood-depth COG ``ResultLayer``; on failure the layer
        list is empty and ``FloodMetrics.solver_version`` carries the
        error code.
    """
    workflow_name = "model_flood_scenario"
    now = datetime.now(timezone.utc)
    proj_id = project_id or new_ulid()
    sess_id = session_id or new_ulid()
    data_sources: list[DataSource] = []
    solver_run_ids: list[str] = []
    grid_resolution_m = 30.0  # NFR-P-4 default; OQ-4 §4 immediate

    logger.info(
        "model_flood_scenario start bbox=%s location_query=%r event_id=%r "
        "return_period_yr=%s duration_hr=%s compute_class=%s",
        bbox,
        location_query,
        event_id,
        return_period_yr,
        duration_hr,
        compute_class,
    )

    # --- Step 0: bbox resolution (Decision K; bbox-direct wins precedence) ---
    try:
        resolved_bbox, geocode_result = _resolve_bbox(
            bbox=bbox, location_query=location_query
        )
    except WorkflowError as exc:
        # No bbox to anchor a failed envelope on; this is the rare fatal case.
        # Bubble up so the agent surface emits a top-level error frame.
        raise
    if geocode_result is not None:
        data_sources.append(
            DataSource(
                name="OpenStreetMap Nominatim",
                uri=f"nominatim:{geocode_result.get('osm_type','')}/{geocode_result.get('osm_id','')}",
                accessed_at=datetime.now(timezone.utc),
            )
        )

    # --- Step 1-4: atomic-tool fetcher chain ---
    forcing_summary: ForcingSummary | None = None
    try:
        dem_layer = fetch_dem(resolved_bbox, resolution_m=int(grid_resolution_m))
        data_sources.append(
            DataSource(name="USGS 3DEP", uri=dem_layer.uri, accessed_at=datetime.now(timezone.utc))
        )
        landcover_result = fetch_landcover(resolved_bbox, dataset="nlcd_2021")
        landcover_layer: LayerURI = landcover_result["layer"]
        nlcd_vintage_year = int(landcover_result.get("nlcd_vintage_year"))
        data_sources.append(
            DataSource(
                name=f"NLCD {nlcd_vintage_year} (MRLC WMS)",
                uri=landcover_layer.uri,
                accessed_at=datetime.now(timezone.utc),
            )
        )
        river_layer = fetch_river_geometry(resolved_bbox, source="nhdplus_hr")
        data_sources.append(
            DataSource(
                name="NHDPlus HR (USGS)",
                uri=river_layer.uri,
                accessed_at=datetime.now(timezone.utc),
            )
        )
        mid_lon = 0.5 * (resolved_bbox[0] + resolved_bbox[2])
        mid_lat = 0.5 * (resolved_bbox[1] + resolved_bbox[3])
        precip_result = lookup_precip_return_period(
            location=(mid_lat, mid_lon),
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
        )
        precip_inches = float(precip_result["precip_inches"])
        data_sources.append(
            DataSource(
                name=precip_result.get("vintage_volume", "NOAA Atlas 14"),
                uri="noaa-atlas14-pfds",
                accessed_at=datetime.now(timezone.utc),
            )
        )
        forcing_summary = ForcingSummary(
            forcing_type="pluvial_synthetic",
            source=(
                f"{precip_result.get('vintage_volume', 'NOAA Atlas 14')} — "
                f"{return_period_yr}-yr / {duration_hr}-hr design storm"
            ),
            parameters={
                "precip_inches": precip_inches,
                "duration_hours": float(duration_hr),
                "return_period_years": return_period_yr,
                "vintage_volume": precip_result.get("vintage_volume"),
                "project_area": precip_result.get("project_area"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("fetcher chain failed: %s", exc)
        return _build_failed_envelope(
            bbox=resolved_bbox,
            project_id=proj_id,
            session_id=sess_id,
            error_code=getattr(exc, "error_code", "FETCHER_FAILED"),
            error_detail=str(exc),
            workflow_name=workflow_name,
            data_sources=data_sources,
            forcing=forcing_summary,
            solver_run_ids=solver_run_ids,
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
            grid_resolution_m=grid_resolution_m,
        )

    # --- Step 5: build_sfincs_model with NLCD validation gate ---
    try:
        forcing_spec = ForcingSpec(
            forcing_type="pluvial_synthetic",
            precip_inches=precip_inches,
            duration_hours=float(duration_hr),
            return_period_years=return_period_yr,
            provenance=dict(forcing_summary.parameters if forcing_summary else {}),
        )
        options = BuildOptions(
            grid_resolution_m=grid_resolution_m,
            simulation_hours=float(duration_hr),
        )
        model_setup = build_sfincs_model(
            dem_uri=dem_layer.uri,
            landcover_uri=landcover_layer.uri,
            river_geometry_uri=river_layer.uri,
            forcing=forcing_spec,
            bbox=resolved_bbox,
            options=options,
            nlcd_vintage_year=nlcd_vintage_year,
        )
    except SFINCSSetupError as exc:
        # The headline failure path — LULC_MAPPING_MISMATCH and friends
        # surface here. Invariant 7: the failed envelope carries the error
        # code instead of a fabricated FloodPayload.
        logger.warning(
            "build_sfincs_model raised %s (details=%s) — returning failed envelope",
            exc.error_code,
            exc.details,
        )
        return _build_failed_envelope(
            bbox=resolved_bbox,
            project_id=proj_id,
            session_id=sess_id,
            error_code=exc.error_code,
            error_detail=str(exc),
            workflow_name=workflow_name,
            data_sources=data_sources,
            forcing=forcing_summary,
            solver_run_ids=solver_run_ids,
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
            grid_resolution_m=grid_resolution_m,
        )

    # --- Step 6: run_solver (Invariant 9 confirmation seam owned by agent) ---
    try:
        handle = run_solver(
            solver="sfincs",
            model_setup_uri=model_setup.setup_uri,
            compute_class=compute_class,
        )
        solver_run_ids.append(handle.run_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_solver dispatch failed: %s", exc)
        return _build_failed_envelope(
            bbox=resolved_bbox,
            project_id=proj_id,
            session_id=sess_id,
            error_code=getattr(exc, "error_code", "SOLVER_DISPATCH_FAILED"),
            error_detail=str(exc),
            workflow_name=workflow_name,
            data_sources=data_sources,
            forcing=forcing_summary,
            solver_run_ids=solver_run_ids,
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
            grid_resolution_m=grid_resolution_m,
        )

    # --- Step 7: wait_for_completion (Invariant 8 cancel chain propagates) ---
    try:
        run_result: RunResult = await wait_for_completion(handle)
    except asyncio.CancelledError:
        # Invariant 8: the cancel chain is owned by wait_for_completion;
        # propagate immediately so the WS handler emits pipeline-state(cancelled).
        logger.info("model_flood_scenario cancelled while awaiting solver")
        raise

    if run_result.status != "complete":
        # SOLVER_FAILED, SOLVER_TIMEOUT, cancelled — surface as failed envelope.
        return _build_failed_envelope(
            bbox=resolved_bbox,
            project_id=proj_id,
            session_id=sess_id,
            error_code=run_result.error_code or run_result.status.upper(),
            error_detail=run_result.error_message or run_result.cancellation_reason or "",
            workflow_name=workflow_name,
            data_sources=data_sources,
            forcing=forcing_summary,
            solver_run_ids=solver_run_ids,
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
            grid_resolution_m=grid_resolution_m,
        )

    # --- Step 8: postprocess_flood ---
    try:
        layers, depth_metrics = postprocess_flood(
            run_result.output_uri or f"gs://grace-2-hazard-prod-runs/{run_result.run_id}/",
            run_id=run_result.run_id,
        )
    except PostprocessError as exc:
        logger.warning("postprocess_flood failed: %s (%s)", exc.error_code, exc)
        return _build_failed_envelope(
            bbox=resolved_bbox,
            project_id=proj_id,
            session_id=sess_id,
            error_code=exc.error_code,
            error_detail=str(exc),
            workflow_name=workflow_name,
            data_sources=data_sources,
            forcing=forcing_summary,
            solver_run_ids=solver_run_ids,
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
            grid_resolution_m=grid_resolution_m,
        )

    # --- Step 9: build success envelope ---
    bbox_area_km2 = _bbox_area_km2(resolved_bbox)
    result_layers: list[ResultLayer] = [
        ResultLayer(
            layer_id=lyr.layer_id,
            name=lyr.name,
            layer_type=lyr.layer_type,
            uri=lyr.uri,
            style_preset=lyr.style_preset,
            temporal=lyr.temporal,
            role=lyr.role,
            units=lyr.units,
        )
        for lyr in layers
    ]
    metrics = FloodMetrics(
        flooded_area_km2=min(
            bbox_area_km2,
            float(depth_metrics.get("flooded_cell_count", 0))
            * (grid_resolution_m * grid_resolution_m / 1_000_000.0),
        ),
        max_depth_m=float(depth_metrics.get("max_depth_m", 0.0)),
        mean_depth_m=float(depth_metrics.get("mean_depth_m", 0.0)),
        p95_depth_m=float(depth_metrics.get("p95_depth_m", 0.0)),
        solver_version="sfincs-v2.3.3",
        grid_resolution_m=grid_resolution_m,
        simulation_duration_hours=int(duration_hr),
    )
    envelope = AssessmentEnvelope(
        envelope_id=new_ulid(),
        project_id=proj_id,
        session_id=sess_id,
        envelope_type="modeled",
        hazard_type="flood",
        workflow_name=workflow_name,
        bbox=resolved_bbox,
        crs="EPSG:4326",
        forcing=forcing_summary,
        layers=result_layers,
        provenance=Provenance(data_sources=data_sources),
        created_at=now,
        completed_at=datetime.now(timezone.utc),
        solver_run_ids=solver_run_ids,
        flood=FloodPayload(metrics=metrics),
    )
    logger.info(
        "model_flood_scenario complete envelope_id=%s run_ids=%s layers=%d",
        envelope.envelope_id,
        solver_run_ids,
        len(result_layers),
    )
    return envelope


# --------------------------------------------------------------------------- #
# LLM-exposed thin atomic-tool wrapper (workflow_dispatch source class)
# --------------------------------------------------------------------------- #


_RUN_MODEL_FLOOD_SCENARIO_METADATA = AtomicToolMetadata(
    name="run_model_flood_scenario",
    ttl_class="live-no-cache",
    source_class="workflow_dispatch",
    cacheable=False,
)


@register_tool(_RUN_MODEL_FLOOD_SCENARIO_METADATA)
async def run_model_flood_scenario(
    bbox: tuple[float, float, float, float] | None = None,
    location_query: str | None = None,
    event_id: str | None = None,
    return_period_yr: int = 100,
    duration_hr: int = 24,
    compute_class: str = "medium",
) -> LayerURI | dict[str, Any]:
    """Run the full deterministic flood-modeling workflow.

    Use this when: the agent has a flood-modeling intent grounded in a
    location (either a free-text place name or a bbox) and needs the typed
    AssessmentEnvelope (with a flood-depth COG layer and structured flood
    metrics) for narration + rendering. The workflow composes the M5
    fetcher chain → HydroMT SFINCS setup (with NLCD validation gate per OQ-4
    §4 — guards Invariant 7) → Cloud Run solver dispatch → postprocess.

    Do NOT use this for: running a custom solver dispatch (use ``run_solver``
    + ``wait_for_completion`` directly); composing a non-flood hazard
    (other hazard workflows land in their respective milestones); cancelling
    a running flood scenario (use the WS ``cancel`` envelope — the cancel
    chain propagates through ``wait_for_completion``).

    Params:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326. When
            ``None``, ``location_query`` is used to geocode. Direct bbox
            wins when both are supplied.
        location_query: free-text place name (e.g. ``"Fort Myers, FL"``).
        event_id: optional event ID for HEP-side provenance (v0.1: carried
            on the envelope's provenance hook; HEP integration M5.5+).
        return_period_yr: design-storm ARI. Default 100.
        duration_hr: design-storm duration (hours). Default 24.
        compute_class: FR-CE-3 compute class. Default ``"medium"``.

    Returns:
        On success: the primary flood-depth COG as a ``LayerURI`` — the
        ``PipelineEmitter.emit_tool_call`` gate at
        ``pipeline_emitter.py:517`` fires ``add_loaded_layer`` when it sees
        a ``LayerURI`` return, which appends to ``session-state.loaded_layers``
        and emits a fresh ``session-state`` envelope (A.7 replace-not-reconcile).
        See ``docs/decisions/layer-emission-contract.md`` (ADOPTED 2026-06-07).

        On failure (partial-failure envelope with empty layers): the
        AssessmentEnvelope serialized as a dict so the LLM can narrate the
        error. The dict carries the Appendix B.4 Flood subtype shape with the
        error code threaded into ``flood.metrics.solver_version`` as
        ``"failed:<ERROR_CODE>"``.

    FR-DC-6: This wrapper declares ``cacheable=False`` +
    ``ttl_class="live-no-cache"`` + ``source_class="workflow_dispatch"`` (a new
    FR-DC-6 source class for the workflow exposure surface — same shape as
    job-0041's ``solver_dispatch``).
    """
    envelope = await model_flood_scenario(
        bbox=bbox,
        location_query=location_query,
        event_id=event_id,
        return_period_yr=return_period_yr,
        duration_hr=duration_hr,
        compute_class=compute_class,
    )
    # --- Layer-emission contract pin (docs/decisions/layer-emission-contract.md, 2026-06-07) ---
    # Return the primary flood-depth COG as a LayerURI so PipelineEmitter's
    # isinstance(result, LayerURI) gate at pipeline_emitter.py:517 fires
    # add_loaded_layer → session-state.loaded_layers (declarative, A.7
    # replace-not-reconcile).  On failure the envelope has no layers; fall
    # back to the dict so the LLM can narrate the error honestly.
    if envelope.layers:
        primary = envelope.layers[0]
        return LayerURI(
            layer_id=primary.layer_id,
            name=primary.name,
            layer_type=primary.layer_type,
            uri=primary.uri,
            style_preset=primary.style_preset,
            temporal=primary.temporal,
            role=primary.role,
            units=primary.units,
        )
    return envelope.model_dump(mode="json")
