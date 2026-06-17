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
import os
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

from ..pipeline_emitter import current_emitter
from ..tools import register_tool
from ..tools.data_fetch import (
    fetch_dem,
    fetch_landcover,
    fetch_river_geometry,
    geocode_location,
    lookup_precip_return_period,
)
from ..tools.publish_layer import PublishLayerError, publish_layer
from ..tools.solver import run_solver, wait_for_completion
from .postprocess_flood import (
    FLOOD_DEPTH_STYLE_PRESET,
    PostprocessError,
    postprocess_flood,
)
from .sfincs_builder import (
    BuildOptions,
    ForcingSpec,
    SFINCSSetupError,
    _to_vsigs,
    build_sfincs_model,
)

__all__ = [
    "model_flood_scenario",
    "run_model_flood_scenario",
    "WorkflowError",
    "PrecipForcingError",
    "compute_precip_area_mean_mm_per_hr",
]

logger = logging.getLogger("grace2_agent.workflows.model_flood_scenario")


# Default project/session identifiers for ULID-bearing envelope fields. The
# agent runtime threads real IDs through when WS state is present; the
# workflow itself accepts None and falls back to fresh ULIDs so a direct call
# (smoke harness, integration test) still produces a valid envelope.
_FALLBACK_PROJECT_ID = None
_FALLBACK_SESSION_ID = None


# --- Pre-solver phase timeouts (terminal-pipeline-card hardening) -----------
# The fetcher chain (Steps 1-4) + ``build_sfincs_model`` (Step 5) run BEFORE
# ``wait_for_completion``, which is the only phase that previously emitted
# progress. If any pre-solver step hangs (a wedged data endpoint, a GDAL VSI
# read with no overall timeout, a py3dep stall) the card sat ``running`` with
# NO progress and NO timeout — indistinguishable from the spin-after-cancel
# bug and consistent with NATE's "120 min, never finished" symptom. Each phase
# is now wrapped in ``asyncio.wait_for`` (the sync calls go through
# ``asyncio.to_thread`` so the timeout is enforceable) and bounded by a
# GENEROUS budget — large enough that a healthy fetch/build never trips it, but
# finite so a true hang surfaces as a typed ``*_TIMEOUT`` failed envelope
# instead of an infinite await. Overridable via env for ops tuning.
_FETCHER_PHASE_TIMEOUT_S = float(
    os.environ.get("GRACE2_FLOOD_FETCHER_TIMEOUT_S", "900")  # 15 min
)
_BUILD_PHASE_TIMEOUT_S = float(
    os.environ.get("GRACE2_FLOOD_BUILD_TIMEOUT_S", "900")  # 15 min
)


async def _emit_presolver_progress(
    emitter: Any, progress_percent: int
) -> None:
    """Best-effort pre-solver progress bump on the current pipeline card.

    Keeps the card from sitting SILENTLY during the multi-second pre-solver
    chain. ``emitter`` is the ``current_emitter()`` handle (may be ``None``
    outside a WS dispatch — direct call / smoke / unit test); failure is
    swallowed because progress is a UX hint, never a correctness gate.
    """
    if emitter is None:
        return
    try:
        await emitter.update_current_progress(progress_percent)
    except Exception as exc:  # noqa: BLE001 — progress is non-fatal
        logger.debug(
            "model_flood_scenario: pre-solver progress emit failed (non-fatal): %s",
            exc,
        )


#: Cadence (seconds) for the LIVE solve-progress envelope during the long solve.
#: Independent of the solver poll cadence — this is a UX tick on the running
#: card; conservative so a 10-20-min solve emits a steady (not chatty) stream.
_LIVE_SOLVE_PROGRESS_INTERVAL_S = 10.0


def _extract_solve_autoscale(model_setup: Any) -> dict[str, Any]:
    """Pull the autoscale provenance (active cells / vCPU / est-solve) off the
    built ``ModelSetup`` for the live solve-progress envelope + telemetry.

    Mirrors ``_emit_flood_solve_telemetry``'s read of
    ``model_setup.parameters['autoscale']`` so the live card and the
    at-completion telemetry agree on cells/vCPU. Returns ``{}`` when absent.
    """
    params = getattr(model_setup, "parameters", {}) or {}
    autoscale = params.get("autoscale") if isinstance(params, dict) else None
    return autoscale if isinstance(autoscale, dict) else {}


async def _drive_live_solve_progress(
    *,
    emitter: Any,
    run_id: str,
    solver: str,
    grid_resolution_m: float | None,
    active_cell_count: int | None,
    vcpus: int | None,
    eta_seconds: float | None,
) -> None:
    """Background loop: emit the LIVE solve-progress envelope every N seconds.

    Runs alongside ``wait_for_completion`` so the running tool/pipeline card
    shows grid/cells/vCPU/elapsed/ETA ticking during the long solve (rather than
    a silent multi-minute spinner). ``elapsed_seconds`` is wall-clock from this
    coroutine's start (Invariant 1: never an LLM estimate); ``eta_seconds`` is
    the perf-model ``estimated_solve_seconds`` when available, else ``None``.

    Best-effort + cancellation-safe: the caller cancels this task when the solve
    returns; any emit failure is swallowed (live telemetry is a UX hint, never a
    correctness gate). No-op when ``emitter`` is ``None`` (direct/smoke/test
    call without a WS emitter)."""
    if emitter is None:
        return
    from ..telemetry import build_live_solve_progress

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        while True:
            elapsed = max(0.0, loop.time() - started)
            payload = build_live_solve_progress(
                run_id=run_id,
                solver=solver,
                grid_resolution_m=grid_resolution_m,
                active_cell_count=active_cell_count,
                vcpus=vcpus,
                elapsed_seconds=elapsed,
                eta_seconds=eta_seconds,
            )
            try:
                await emitter.emit_solve_progress(payload)
            except Exception as exc:  # noqa: BLE001 — UX hint, never fatal
                logger.debug(
                    "model_flood_scenario: live solve-progress emit failed "
                    "(non-fatal): %s",
                    exc,
                )
            await asyncio.sleep(_LIVE_SOLVE_PROGRESS_INTERVAL_S)
    except asyncio.CancelledError:
        # Normal teardown when the solve completes — re-raise so the task
        # finalizes cleanly.
        raise


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
    # job-0327 (HONESTY FLOOR, B2): promote the error code onto the depth-0
    # ``workflow_name`` string ("<name>:FAILED:<CODE>") so it survives the
    # adapter's ``_coerce_to_summary_value`` depth>=2 dict-collapse (the
    # ``flood.metrics.solver_version`` threading sits at depth 2 and is reduced
    # to bare key names before the LLM sees it). This gives the adapter's
    # failed-modeled-envelope classifier (summarize_tool_result, job-0327 B1) a
    # depth-0 corroborating signal AND keeps the code human-legible in the
    # function_response even if the classifier were ever bypassed. The
    # ``:FAILED:`` infix is the parse anchor (``workflow_name`` never otherwise
    # contains it). Guard against double-tagging when this envelope is re-built.
    failed_workflow_name = (
        workflow_name
        if ":FAILED:" in workflow_name
        else f"{workflow_name}:FAILED:{error_code}"
    )
    return AssessmentEnvelope(
        envelope_id=new_ulid(),
        project_id=project_id,
        session_id=session_id,
        envelope_type="modeled",
        hazard_type="flood",
        workflow_name=failed_workflow_name,
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


def _emit_flood_solve_telemetry(
    *,
    run_result: "RunResult",
    handle: Any,
    model_setup: Any,
    bbox: tuple[float, float, float, float],
    grid_resolution_m: float,
) -> dict | None:
    """Emit a solve-completion telemetry record (sprint-16 autoscale).

    Pulls the autoscale provenance (estimated active cells, chosen resolution,
    vCPU) off ``model_setup.parameters`` and the wall-clock from the
    ``RunResult`` (``duration_seconds``), and folds in the backend
    (``handle.workflow_name`` — ``local-docker`` / ``local-exec`` /
    ``grace-2-sfincs-orchestrator``) + aoi_km2. Best-effort; returns the record
    (or ``None`` on any failure) so the caller's try/except stays simple.
    """
    from ..telemetry import emit_solve_telemetry

    params = getattr(model_setup, "parameters", {}) or {}
    autoscale = params.get("autoscale") if isinstance(params, dict) else None
    autoscale = autoscale if isinstance(autoscale, dict) else {}

    active_cells = autoscale.get("estimated_active_cells")
    vcpus = autoscale.get("vcpus")
    est_solve_s = autoscale.get("estimated_solve_seconds")
    coarsened = autoscale.get("coarsened")
    # Prefer the actually-built resolution off the ModelSetup; fall back to the
    # workflow's resolution variable.
    built_res = getattr(model_setup, "grid_resolution_m", None) or grid_resolution_m

    return emit_solve_telemetry(
        run_id=run_result.run_id,
        backend=str(getattr(handle, "workflow_name", "") or "unknown"),
        active_cell_count=int(active_cells) if active_cells is not None else None,
        grid_resolution_m=float(built_res) if built_res is not None else None,
        vcpus=int(vcpus) if vcpus is not None else None,
        wall_clock_seconds=run_result.duration_seconds,
        aoi_km2=_bbox_area_km2(bbox),
        solver=getattr(handle, "solver", "sfincs") or "sfincs",
        estimated_solve_seconds=float(est_solve_s) if est_solve_s is not None else None,
        coarsened=bool(coarsened) if coarsened is not None else None,
    )


def _default_runs_prefix(run_id: str) -> str:
    """Scheme-aware fallback runs prefix when ``RunResult.output_uri`` is None.

    job-0291 (sprint-14-aws): under ``GRACE2_STORAGE_BACKEND=s3`` the prefix
    is ``s3://$GRACE2_RUNS_BUCKET/<run_id>/`` (no GCP-named default on AWS —
    when the env is unset we keep the legacy gs:// literal so the failure
    surfaces as the familiar RUN_OUTPUT_READ_FAILED rather than a silent
    write to a wrong bucket). The default (gcs) branch is byte-identical to
    the pre-job-0291 literal.
    """
    import os

    from ..tools.cache import storage_scheme

    if storage_scheme() == "s3":
        bucket = (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
        if bucket:
            return f"s3://{bucket}/{run_id}/"
    return f"gs://grace-2-hazard-prod-runs/{run_id}/"


# --------------------------------------------------------------------------- #
# job-0225 v2 — real-precip forcing branch (area-mean netamt)
# --------------------------------------------------------------------------- #


class PrecipForcingError(RuntimeError):
    """Raised when the observed-precip-raster forcing path cannot be computed.

    Carries an A.6 open-set ``error_code`` so the workflow surface lifts it
    into a failed AssessmentEnvelope (same pattern as ``SFINCSSetupError``).
    Codes:
    - ``PRECIP_RASTER_READ_FAILED`` — the raster bytes were unreadable.
    - ``PRECIP_RASTER_EMPTY`` — the raster had no valid (non-nodata) cells in
      the domain → no area-mean is computable.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def compute_precip_area_mean_mm_per_hr(
    forcing_raster_uri: str,
    bbox: tuple[float, float, float, float],
    accumulation_hours: float,
    *,
    raster_units: str = "mm",
) -> tuple[float, float]:
    """Compute the AREA-MEAN accumulated precip over the model domain → mm/hr.

    job-0225 v2 (OQ-6 netamt fallback). Reads the precipitation raster at
    ``forcing_raster_uri`` (an accumulated-precip COG — MRMS QPE, ERA5,
    gridMET, …), computes the mean over all valid cells, and converts that
    single domain-mean accumulated depth into a uniform SFINCS ``netamt``
    rate in **mm/hr** by dividing by the ``accumulation_hours`` window.

    This collapses the raster's spatial structure to one number — the v0.1
    netamt fallback locked by manifest OQ-6. The spw spatially-varying-precip
    upgrade path (ingest the raster as a 2D time grid) is documented in
    ``sfincs_builder._generate_hydromt_yaml_config`` + this job's report.md.

    Domain handling (v0.1): we average over EVERY valid cell in the raster.
    The fetchers that produce the precip raster (e.g. ``fetch_mrms_qpe``) clip
    to roughly the requested bbox already, so the raster footprint ≈ the model
    domain. A future refinement would window-read the raster to the exact bbox
    before averaging (captured as OQ-225-EXACT-DOMAIN-WINDOW); for v0.1 the
    whole-raster mean is the documented behavior.

    Args:
        forcing_raster_uri: ``gs://...`` (or local path / ``/vsigs/...``) URI
            of the accumulated-precip COG.
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` — the model domain.
            Carried for provenance + future exact-window cropping; v0.1 uses
            the whole-raster mean.
        accumulation_hours: the precip accumulation window in hours (e.g. 24
            for a 24h QPE product). The area-mean accumulated depth is divided
            by this to yield mm/hr. Must be positive.
        raster_units: declared units of the raster values. Default ``"mm"``
            (the MRMS/ERA5/gridMET convention used by our fetchers). If
            ``"inches"`` the mean is multiplied by 25.4 to reach mm before the
            per-hour conversion.

    Returns:
        ``(magnitude_mm_per_hr, area_mean_mm)`` — the uniform SFINCS netamt
        rate AND the area-mean accumulated depth in mm (echoed into forcing
        provenance for narration).

    Raises:
        PrecipForcingError("PRECIP_RASTER_READ_FAILED"): the read failed.
        PrecipForcingError("PRECIP_RASTER_EMPTY"): no valid cells.
        ValueError: ``accumulation_hours <= 0``.
    """
    if accumulation_hours <= 0:
        raise ValueError(
            f"accumulation_hours must be positive; got {accumulation_hours!r}"
        )
    try:
        import numpy as np  # type: ignore[import-not-found]
        import rasterio  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PrecipForcingError(
            "PRECIP_RASTER_READ_FAILED",
            f"rasterio/numpy not available for precip area-mean: {exc}",
        ) from exc

    # Scheme dispatch for the forcing-raster read:
    #   s3://  — boto3 stage-then-open (sprint-14-aws / job-0293c). GDAL's
    #            /vsis3/ credential chain does NOT resolve the EC2 instance role
    #            in this env (boto3 does) — observed live: "does not exist" on an
    #            existing object. Stage the bytes via the shared boto3 reader and
    #            open in-memory (MemoryFile frees with the dataset; no temp-file
    #            leak — mirrors extract_landcover_class._open_source). The MRMS
    #            COG is bbox-clipped/small, so a whole-file fetch is safe.
    #   gs:// / /vsigs/ / file:// / local — keep the GDAL /vsigs/ path (job-0170
    #            — keeps the fragile gcsfs path out of the read; local pass-through).
    try:
        if forcing_raster_uri.startswith("s3://"):
            from rasterio.io import MemoryFile  # type: ignore[import-not-found]

            from ..tools.cache import read_object_bytes_s3

            with MemoryFile(read_object_bytes_s3(forcing_raster_uri)) as mf:
                with mf.open() as src:
                    arr = src.read(1).astype("float64")
                    nodata = src.nodata
        else:
            read_path = _to_vsigs(forcing_raster_uri)
            with rasterio.open(read_path) as src:
                arr = src.read(1).astype("float64")
                nodata = src.nodata
    except Exception as exc:  # noqa: BLE001
        raise PrecipForcingError(
            "PRECIP_RASTER_READ_FAILED",
            f"rasterio.open({forcing_raster_uri}) failed: {exc}",
        ) from exc

    # Mask nodata + common sentinels + non-finite values. Negative precip is
    # physically invalid (some products use negatives as fill) — mask those
    # too so they don't drag the mean.
    mask = np.isfinite(arr)
    if nodata is not None:
        mask &= arr != nodata
    mask &= arr != -9999.0
    mask &= arr >= 0.0
    valid = arr[mask]
    if valid.size == 0:
        raise PrecipForcingError(
            "PRECIP_RASTER_EMPTY",
            f"precip raster {forcing_raster_uri} has no valid cells over the "
            f"domain {bbox} — no area-mean computable",
        )

    area_mean = float(valid.mean())
    if raster_units == "inches":
        area_mean_mm = area_mean * 25.4
    else:
        area_mean_mm = area_mean
    magnitude_mm_per_hr = area_mean_mm / accumulation_hours
    logger.info(
        "precip area-mean: raster=%s valid_cells=%d mean=%.4f %s "
        "(%.4f mm) / %.2f hr → %.6f mm/hr",
        forcing_raster_uri,
        int(valid.size),
        area_mean,
        raster_units,
        area_mean_mm,
        accumulation_hours,
        magnitude_mm_per_hr,
    )
    return magnitude_mm_per_hr, area_mean_mm


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
    forcing_raster_uri: str | None = None,
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
        forcing_raster_uri: optional ``gs://...`` (or local) URI of an
            OBSERVED accumulated-precip raster (job-0225 v2, Case 3). When
            set, the workflow SKIPS the ``lookup_precip_return_period`` Atlas
            14 design-storm lookup and instead computes the AREA-MEAN
            accumulated precip over the model domain, converting it to a
            uniform SFINCS ``netamt`` rate (mm/hr) — the OQ-6 area-mean
            fallback (spw spatial upgrade path documented in
            ``sfincs_builder``). ``duration_hr`` is reused as the precip
            accumulation window for the depth→rate conversion. When ``None``
            (the default) the Atlas 14 design-storm path runs unchanged —
            behavior is **identical** to the v1 workflow (regression-critical).
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
        "return_period_yr=%s duration_hr=%s compute_class=%s "
        "forcing_raster_uri=%r",
        bbox,
        location_query,
        event_id,
        return_period_yr,
        duration_hr,
        compute_class,
        forcing_raster_uri,
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

    # --- Zoom-on-area-first (job-0160): emit ``map-command(zoom-to)`` BEFORE
    # any compute starts. As soon as we have a bbox, the map zooms — the
    # user sees immediate response while the multi-minute SFINCS chain runs.
    # The emitter binding is set by ``PipelineEmitter.emit_tool_call`` via
    # the ``_CURRENT_EMITTER`` ContextVar; outside that scope (direct call,
    # smoke harness, unit test without an emitter) ``current_emitter()``
    # returns ``None`` and we skip silently — emitting a transient verb is
    # a UX nice-to-have, not a correctness gate.
    emitter = current_emitter()
    if emitter is not None:
        try:
            await emitter.emit_map_command(
                "zoom-to",
                {"bbox": list(resolved_bbox)},
            )
            logger.info(
                "model_flood_scenario: zoom-on-area-first emitted bbox=%s",
                resolved_bbox,
            )
        except Exception as exc:  # noqa: BLE001 — non-fatal UX hint
            logger.warning(
                "model_flood_scenario: zoom-on-area-first emit failed (non-fatal): %s",
                exc,
            )

    # --- Step 1-4: atomic-tool fetcher chain ---
    forcing_summary: ForcingSummary | None = None
    # job-0225 v2: ``precip_inches`` is the Atlas 14 design-storm depth (None
    # on the observed-raster path); ``precip_magnitude_mm_per_hr`` is the
    # pre-computed uniform netamt rate (None on the design-storm path).
    precip_inches: float | None = None
    precip_magnitude_mm_per_hr: float | None = None
    # Pre-solver progress (terminal-pipeline-card hardening): nudge the card so
    # it is never SILENT during the multi-second fetcher chain.
    await _emit_presolver_progress(emitter, 5)
    # The fetcher chain + ForcingSummary build is SYNCHRONOUS, blocking I/O
    # (HTTP fetches, GDAL VSI reads with no overall timeout). Run it off the
    # event loop in a worker thread and bound it with ``asyncio.wait_for`` so a
    # wedged endpoint surfaces as a typed PRESOLVER_TIMEOUT failed envelope
    # instead of an INFINITE silent await (NATE's "120 min, never finished").
    # The closure mutates ``data_sources`` / ``forcing_summary`` etc. via a
    # results container; single worker thread, sequential, no concurrent reader.
    _fetch_out: dict[str, Any] = {}

    def _fetcher_chain() -> None:
        nonlocal precip_inches, precip_magnitude_mm_per_hr, forcing_summary
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
        # job-0307: river geometry is BEST-EFFORT for the v0.1 pluvial deck.
        # ``build_sfincs_model`` does NOT emit ``setup_river_inflow`` for v0.1
        # pluvial (job-0055) — ``river_geometry_uri`` is accepted but unused, and
        # documented as ``may be None``. So a river-fetch failure must NOT kill an
        # otherwise-valid pluvial flood. Live Case 3 (2026-06-16): Victoria, TX
        # failed with "could not route bbox … to a HUC4 region" (the OQ-39 v0.1
        # HUC4 heuristic only covers a few demo areas), needlessly aborting a
        # flood that needs no river inflow. Degrade to None + narrate; re-enable
        # the hard dependency when v0.2 river-inflow (real ATCF surge) lands.
        river_layer: LayerURI | None
        try:
            river_layer = fetch_river_geometry(resolved_bbox, source="nhdplus_hr")
            data_sources.append(
                DataSource(
                    name="NHDPlus HR (USGS)",
                    uri=river_layer.uri,
                    accessed_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:  # noqa: BLE001 — river is optional for pluvial
            logger.warning(
                "model_flood_scenario: fetch_river_geometry failed for bbox=%s "
                "(%s) — proceeding WITHOUT river geometry (pluvial deck does not "
                "use river inflow; job-0055/job-0307).",
                resolved_bbox,
                exc,
            )
            river_layer = None
        if forcing_raster_uri is not None:
            # --- job-0225 v2: OBSERVED-precip forcing branch (Case 3) ---
            # Compute the AREA-MEAN accumulated precip over the model domain
            # and convert to a uniform SFINCS netamt rate (mm/hr). ``duration_hr``
            # is reused as the accumulation window. The Atlas 14 design-storm
            # lookup is SKIPPED entirely on this path.
            precip_magnitude_mm_per_hr, area_mean_mm = (
                compute_precip_area_mean_mm_per_hr(
                    forcing_raster_uri=forcing_raster_uri,
                    bbox=resolved_bbox,
                    accumulation_hours=float(duration_hr),
                )
            )
            data_sources.append(
                DataSource(
                    name="Observed precipitation raster (area-mean netamt)",
                    uri=forcing_raster_uri,
                    accessed_at=datetime.now(timezone.utc),
                )
            )
            # Envelope-side ``ForcingSummary.forcing_type`` is a contract-owned
            # Literal that does NOT (yet) include ``"pluvial_observed"`` — the
            # observed precip raster IS a pluvial-precip forcing on the same
            # SFINCS netamt path, so we summarise it as ``"pluvial_synthetic"``
            # and carry the observed/area-mean distinction in the free-form
            # ``parameters`` dict (``forcing_mode="area_mean_netamt"`` +
            # ``forcing_raster_uri``) + the human-readable ``source``. The
            # ENGINE-internal ``ForcingSpec.forcing_type`` (below) is
            # ``"pluvial_observed"`` — that drives the deck-builder branch and
            # is engine-owned. A future schema amendment could add a dedicated
            # ``"pluvial_observed"`` envelope literal (OQ-225-OBSERVED-FORCING-
            # LITERAL — propose to the schema specialist).
            forcing_summary = ForcingSummary(
                forcing_type="pluvial_synthetic",
                source=(
                    f"Observed precip raster {forcing_raster_uri} — "
                    f"area-mean {area_mean_mm:.2f} mm over {duration_hr}-hr "
                    "accumulation → uniform netamt (OQ-6 area-mean fallback)"
                ),
                parameters={
                    "forcing_raster_uri": forcing_raster_uri,
                    "area_mean_mm": area_mean_mm,
                    "precip_magnitude_mm_per_hr": precip_magnitude_mm_per_hr,
                    "accumulation_hours": float(duration_hr),
                    "forcing_mode": "area_mean_netamt",
                },
                inputs_uri=forcing_raster_uri,
            )
        else:
            # --- Atlas 14 design-storm path (v1 behavior, unchanged) ---
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
        # Hand the downstream-needed locals back to the async frame.
        _fetch_out["dem_layer"] = dem_layer
        _fetch_out["landcover_layer"] = landcover_layer
        _fetch_out["nlcd_vintage_year"] = nlcd_vintage_year
        _fetch_out["river_layer"] = river_layer

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_fetcher_chain),
            timeout=_FETCHER_PHASE_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        # Invariant 8: a true cancel propagates (mark_cancelled fires upstream).
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "model_flood_scenario: fetcher chain exceeded %.0fs budget for "
            "bbox=%s — returning PRESOLVER_TIMEOUT failed envelope (a hang is "
            "now bounded + visible, not an infinite silent await).",
            _FETCHER_PHASE_TIMEOUT_S,
            resolved_bbox,
        )
        return _build_failed_envelope(
            bbox=resolved_bbox,
            project_id=proj_id,
            session_id=sess_id,
            error_code="PRESOLVER_TIMEOUT",
            error_detail=(
                f"data-fetch phase exceeded {_FETCHER_PHASE_TIMEOUT_S:.0f}s "
                "(a data endpoint or terrain/landcover read stalled)"
            ),
            workflow_name=workflow_name,
            data_sources=data_sources,
            forcing=forcing_summary,
            solver_run_ids=solver_run_ids,
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
            grid_resolution_m=grid_resolution_m,
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

    dem_layer = _fetch_out["dem_layer"]
    landcover_layer = _fetch_out["landcover_layer"]
    nlcd_vintage_year = _fetch_out["nlcd_vintage_year"]
    river_layer = _fetch_out["river_layer"]
    await _emit_presolver_progress(emitter, 25)

    # --- Step 5: build_sfincs_model with NLCD validation gate ---
    try:
        if forcing_raster_uri is not None:
            # Observed-precip netamt path: carry the pre-computed magnitude.
            forcing_spec = ForcingSpec(
                forcing_type="pluvial_observed",
                duration_hours=float(duration_hr),
                precip_magnitude_mm_per_hr=precip_magnitude_mm_per_hr,
                provenance=dict(forcing_summary.parameters if forcing_summary else {}),
            )
        else:
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
            # sprint-16: feed the compute_class through so the adaptive-grid cap
            # is sized against the right instance vCPU (the cap derives from the
            # solve budget + vCPU via the perf model). build_sfincs_model snaps
            # grid_resolution_m UP if the estimated active-cell count overruns.
            compute_class=compute_class,
        )
        # ``build_sfincs_model`` is SYNCHRONOUS with no overall timeout
        # (sfincs_builder GDAL VSI cache/timeout is per-read only). Run it off
        # the loop + bound it so a wedged build surfaces as PRESOLVER_TIMEOUT
        # rather than an infinite silent await.
        model_setup = await asyncio.wait_for(
            asyncio.to_thread(
                build_sfincs_model,
                dem_uri=dem_layer.uri,
                landcover_uri=landcover_layer.uri,
                # job-0307: None when the best-effort river fetch failed (pluvial
                # deck ignores it; build_sfincs_model documents river_geometry_uri
                # as "may be None").
                river_geometry_uri=river_layer.uri if river_layer is not None else None,
                forcing=forcing_spec,
                bbox=resolved_bbox,
                options=options,
                nlcd_vintage_year=nlcd_vintage_year,
            ),
            timeout=_BUILD_PHASE_TIMEOUT_S,
        )
        # build_sfincs_model may snap grid_resolution_m UP (coarsen) if the
        # estimated active-cell count overruns the per-job cell cap. Refresh the
        # workflow-local resolution from the ACTUALLY-BUILT value so downstream
        # consumers — the solve-telemetry record (cells/resolution/vCPU/wall) and
        # any envelope metrics — report the resolution the solver really ran at,
        # not the pre-coarsen 30 m request.
        _built_res = getattr(model_setup, "grid_resolution_m", None)
        if _built_res:
            grid_resolution_m = float(_built_res)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "model_flood_scenario: build_sfincs_model exceeded %.0fs budget for "
            "bbox=%s — returning PRESOLVER_TIMEOUT failed envelope.",
            _BUILD_PHASE_TIMEOUT_S,
            resolved_bbox,
        )
        return _build_failed_envelope(
            bbox=resolved_bbox,
            project_id=proj_id,
            session_id=sess_id,
            error_code="PRESOLVER_TIMEOUT",
            error_detail=(
                f"SFINCS model build exceeded {_BUILD_PHASE_TIMEOUT_S:.0f}s"
            ),
            workflow_name=workflow_name,
            data_sources=data_sources,
            forcing=forcing_summary,
            solver_run_ids=solver_run_ids,
            return_period_years=return_period_yr,
            duration_hours=float(duration_hr),
            grid_resolution_m=grid_resolution_m,
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

    # Pre-solver phases done — the long solve takes over progress emission from
    # here (wait_for_completion drives the binding). Stamp the hand-off so the
    # card shows clear forward motion into Step 7.
    await _emit_presolver_progress(emitter, 40)

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
    # LIVE big-sim telemetry (NATE 2026-06-17): drive a solve-progress envelope
    # on the running card every few seconds for the duration of the solve so the
    # user sees grid/cells/vCPU/elapsed/ETA tick rather than a silent spinner.
    # The ETA comes from the perf model (autoscale estimated_solve_seconds) when
    # available, else None (no fabricated ETA). The driver is a side task that we
    # cancel as soon as the solve returns/raises — it never affects the outcome.
    _autoscale = _extract_solve_autoscale(model_setup)
    _live_active_cells = _autoscale.get("estimated_active_cells")
    _live_vcpus = _autoscale.get("vcpus")
    _live_eta = _autoscale.get("estimated_solve_seconds")
    _progress_task = asyncio.ensure_future(
        _drive_live_solve_progress(
            emitter=emitter,
            run_id=handle.run_id,
            solver=getattr(handle, "solver", "sfincs") or "sfincs",
            grid_resolution_m=grid_resolution_m,
            active_cell_count=(
                int(_live_active_cells) if _live_active_cells is not None else None
            ),
            vcpus=int(_live_vcpus) if _live_vcpus is not None else None,
            eta_seconds=float(_live_eta) if _live_eta is not None else None,
        )
    )
    try:
        run_result: RunResult = await wait_for_completion(handle)
    except asyncio.CancelledError:
        # Invariant 8: the cancel chain is owned by wait_for_completion;
        # propagate immediately so the WS handler emits pipeline-state(cancelled).
        logger.info("model_flood_scenario cancelled while awaiting solver")
        raise
    finally:
        # Tear down the live-progress driver (success, failure, OR cancel).
        _progress_task.cancel()
        try:
            await _progress_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    # --- Solve-time telemetry (sprint-16 SFINCS per-job autoscale) ---
    # Accumulate real (active_cells, vCPU, wall_clock) data so the adaptive-grid
    # cell cap can be re-tuned from logged measurements. Emitted on the CURRENT
    # path (every solve), for BOTH success and failure/timeout — a censored
    # timeout is itself a data point about a too-big AOI. Best-effort; never
    # breaks the solve loop.
    try:
        _emit_flood_solve_telemetry(
            run_result=run_result,
            handle=handle,
            model_setup=model_setup,
            bbox=resolved_bbox,
            grid_resolution_m=grid_resolution_m,
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the solve
        logger.warning("solve telemetry emission failed (non-fatal): %s", exc)

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
            run_result.output_uri or _default_runs_prefix(run_result.run_id),
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

    # --- Step 9: publish_layer (COG → QGIS Server WMS bridge, job-0062) ---
    # For the primary flood-depth layer, invoke the PyQGIS worker to add the COG
    # to the canonical .qgs project so QGIS Server can serve it as WMS.
    # The returned WMS URL replaces the gs:// uri in the LayerURI/ResultLayer so
    # the client gets a renderable URL directly (layer-emission-contract.md, 2026-06-07).
    #
    # Non-fatal: if publish_layer fails (e.g. OQ-62-WORKER-SA-RUNS-BUCKET-GRANT
    # is not yet landed), we DROP the primary raster layer from the emitted set
    # rather than fall back to the raw gs:// uri (job-0254 §1, Decision 11). A
    # gs:// uri never renders — MapLibre cannot fetch it; emitting it only paints
    # a dead, broken layer row in the LayerPanel. Dropping it keeps the map
    # honest while the rest of the envelope (metrics, provenance, narration)
    # stays intact, so the LLM narrates the publish failure truthfully and the
    # job-0177 retry-on-failure loop can act. The layer_uri_emit seam enforces
    # this same rule at the emission boundary as a belt-and-suspenders invariant.
    published_layers: list[LayerURI] = []
    for lyr in layers:
        # job-0291: s3:// COGs (AWS local-docker backend) take the same
        # publish-or-honest-drop gate as gs:// — a raw object-store URI never
        # renders in MapLibre (job-0254 §1), so it must never reach the map.
        # On AWS publish_layer fails until job-0290 lands QGIS-on-AWS; the
        # layer is dropped and the metrics/narration stay honest.
        if (
            lyr.role == "primary"
            and lyr.layer_type == "raster"
            and (lyr.uri.startswith("gs://") or lyr.uri.startswith("s3://"))
        ):
            layer_id_for_wms = f"flood-depth-peak-{run_result.run_id}"
            try:
                wms_url = publish_layer(
                    layer_uri=lyr.uri,
                    layer_id=layer_id_for_wms,
                    style_preset=lyr.style_preset or "continuous_flood_depth",
                )
                # Substitute the WMS URL into the LayerURI so the client renders
                # directly (OQ-62-LAYERURI-URI-FIELD: LayerURI.uri is documented
                # as gs:// but has no validator rejecting WMS URLs; we use it here
                # as the renderable URL per the kickoff direction. A follow-up
                # schema job should add a dedicated wms_url field.)
                published_layers.append(
                    LayerURI(
                        layer_id=layer_id_for_wms,
                        name=lyr.name,
                        layer_type=lyr.layer_type,
                        uri=wms_url,
                        # job (flood-duplicate-layer fix): the published layer
                        # is the ONE styled (white->blue->green) peak-depth
                        # layer the user sees. Carry the canonical preset
                        # unconditionally — never emit a styleless flood-depth
                        # raster (a styleless COG falls through to TiTiler's
                        # default matplotlib viridis, the redundant unstyled
                        # duplicate this workflow must never produce).
                        style_preset=lyr.style_preset or FLOOD_DEPTH_STYLE_PRESET,
                        temporal=lyr.temporal,
                        role=lyr.role,
                        units=lyr.units,
                        bbox=resolved_bbox,
                    )
                )
                logger.info(
                    "publish_layer succeeded layer_id=%s wms_url=%s",
                    layer_id_for_wms,
                    wms_url,
                )
            except PublishLayerError as exc:
                logger.warning(
                    "publish_layer failed for layer_id=%s error_code=%s (%s) — "
                    "DROPPING the primary flood-depth layer from the emitted set "
                    "(job-0254 §1): a raw gs:// uri never renders in MapLibre, so "
                    "we do NOT fall back to it. The envelope's metrics/provenance "
                    "remain intact and the failure is narrated honestly; the "
                    "retry-on-failure loop (job-0177) can re-attempt publish.",
                    layer_id_for_wms,
                    exc.error_code,
                    exc,
                )
                # Intentionally do NOT append `lyr` — the gs:// uri stays off the
                # map. (OQ-62-WORKER-SA-RUNS-BUCKET-GRANT resolution restores the
                # success path; until then the depth metrics still surface.)
        else:
            published_layers.append(lyr)

    # --- Step 10: build success envelope ---
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
        for lyr in published_layers
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
    forcing_raster_uri: str | None = None,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI | dict[str, Any]:
    """Run the full deterministic SFINCS flood-modeling workflow end-to-end.

    Nine-step composition chain (all deterministic Python, zero LLM calls):
    1. ``geocode_location(location_query)`` — optional; derives bbox from
       a free-text place name when ``bbox`` is not provided.
    2. ``fetch_dem(bbox)`` — downloads USGS 3DEP or CoastalDEM to a COG.
    3. ``fetch_landcover(bbox)`` — downloads NLCD landcover for Manning's
       roughness parameterization.
    4. ``fetch_river_geometry(bbox)`` — downloads NHD river geometry for
       channel routing.
    5. ``lookup_precip_return_period(bbox, return_period_years, duration_hours)``
       — looks up NOAA Atlas 14 design-storm precipitation depth.
    6. ``build_sfincs_model(dem_uri, landcover_uri, river_uri, forcing, bbox)``
       — assembles the HydroMT-SFINCS deck in GCS with NLCD validation gate.
    7. ``run_solver(model_setup)`` — submits the SFINCS Cloud Run Job.
    8. ``wait_for_completion(run_id)`` — polls until SUCCEEDED or FAILED;
       emits progress events per FR-WC-12.
    9. ``postprocess_flood(run_outputs_uri)`` → ``publish_layer(flood_depth_cog)``
       — extracts peak depth COG, uploads to the runs bucket, and publishes
       to QGIS Server WMS.

    When to use:
        - User asks to model a flood scenario, simulate flood inundation,
          compute peak flood depth, run a flood simulation, or estimate flood
          extent for a named location.
        - Any request mentioning "return period", "design storm", "ARI",
          "flood risk", "inundation depth", or "flood extent" for a named
          location or bounding box.

    When NOT to use:
        - Custom solver dispatch (use ``run_solver`` + ``wait_for_completion``
          directly).
        - Non-flood hazards (separate workflow milestones).
        - Cancelling a running flood scenario (use the WS ``cancel`` envelope;
          cancellation propagates through ``wait_for_completion``).

    Examples:
        - "model the flood from a 100-year storm in Fort Myers, FL"
          → location_query: Fort Myers, FL ; return_period_years: 100
        - "peak flood depth from a 25-year design storm in Houston"
          → location_query: Houston ; return_period_years: 25
        - "simulate flood inundation for Hurricane Ian near Fort Myers"
          → location_query: Fort Myers ; return_period_years: 100 (default)
        - "500-year flood for New Orleans, 48-hour duration"
          → location_query: New Orleans ; return_period_years: 500 ; duration_hours: 48

    Params:
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326. When
            ``None``, ``location_query`` is used to geocode. Direct bbox
            wins when both are supplied.
        location_query: free-text place name (geocoded via Nominatim).
        event_id: optional event ID for HEP-side provenance (v0.1: carried
            on the envelope's provenance hook; HEP integration M5.5+).
        return_period_years: design-storm ARI in years. Atlas 14 publishes
            {1, 2, 5, 10, 25, 50, 100, 200, 500, 1000}. Default 100.
            (Alias ``return_period_yr`` is accepted for backward compat.)
        duration_hours: design-storm duration in hours. Atlas 14 publishes
            durations 5-min through 60-day. Default 24.
            (Alias ``duration_hr`` is accepted for backward compat.)
        compute_class: FR-CE-3 compute class. Default ``"medium"``.
        forcing_raster_uri: optional ``gs://...`` URI of an OBSERVED
            accumulated-precipitation raster (e.g. an MRMS QPE COG from
            ``fetch_mrms_qpe``). When provided, the workflow forces SFINCS
            with the AREA-MEAN of this raster over the model domain (converted
            to a uniform rain rate) INSTEAD of the Atlas 14 design storm — this
            is the Case 3 real-data forcing path. ``duration_hours`` is reused
            as the accumulation window. Leave unset (``None``) for the standard
            return-period design-storm scenario.

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

    Cross-tool dependencies:
        Upstream (consumes) — the 9-step fetch + solve chain above:
        - ``geocode_location`` (optional) → ``fetch_dem`` → ``fetch_landcover``
          → ``fetch_river_geometry`` → ``lookup_precip_return_period``
          → ``build_sfincs_model`` → ``run_solver`` → ``wait_for_completion``
          → ``postprocess_flood`` → ``publish_layer``
        Downstream (feeds):
        - ``run_model_flood_habitat_scenario`` — calls this sub-workflow as
          step 3 to generate the flood layer for Case 1 habitat analysis.
        - ``run_pelicun_damage_assessment`` / ``run_pelicun_with_buildings`` —
          consume the returned flood-depth COG ``LayerURI.uri`` as
          ``hazard_raster_uri`` for building-damage assessment.
        - ``compute_zonal_statistics`` — flood-depth COG as ``value_raster_uri``
          for population-in-flood-zone or habitat-impact metrics.
    """
    envelope = await model_flood_scenario(
        bbox=bbox,
        location_query=location_query,
        event_id=event_id,
        return_period_yr=return_period_yr,
        duration_hr=duration_hr,
        compute_class=compute_class,
        forcing_raster_uri=forcing_raster_uri,
    )
    # --- Layer-emission contract pin (docs/decisions/layer-emission-contract.md, 2026-06-07) ---
    # Return the primary flood-depth COG as a LayerURI so PipelineEmitter's
    # isinstance(result, LayerURI) gate at pipeline_emitter.py:517 fires
    # add_loaded_layer → session-state.loaded_layers (declarative, A.7
    # replace-not-reconcile).  On failure the envelope has no layers; fall
    # back to the dict so the LLM can narrate the error honestly.
    #
    # job-0160 bbox fix: include ``envelope.bbox`` on the returned LayerURI so
    # ``PipelineEmitter.add_loaded_layer`` fires the post-publish
    # ``emit_map_command("zoom-to")`` (pipeline_emitter.py:443-447). Prior to
    # this fix the wrapper dropped bbox (``envelope.layers[0]`` is a
    # ``ResultLayer`` with no bbox field) → silent no-zoom after layer landed.
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
            bbox=envelope.bbox,
        )
    return envelope.model_dump(mode="json")
