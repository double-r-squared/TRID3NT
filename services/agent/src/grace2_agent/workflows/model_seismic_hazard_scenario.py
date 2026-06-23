"""OpenQuake probabilistic-seismic-hazard (PSHA) composer (sprint-17).

The OpenQuake analogue of ``model_urban_flood_swmm`` (SWMM) /
``model_groundwater_contamination_scenario`` (MODFLOW) / ``model_flood_scenario``
(SFINCS). A deterministic orchestrator-style workflow (Invariant 2 - no LLM in
the chain) that composes the seismic-hazard engine end-to-end:

    assemble build_spec from OpenQuakeRunArgs (job.ini params + GMPE + G-R source)
      -> stage build_spec.json to S3 (the cache bucket)
      -> run_solver(solver='openquake', model_setup_uri=build_spec) -> Batch
      -> wait_for_completion (poll completion.json over the existing WS)
      -> download the exported hazard-MAP CSV from the Batch output
      -> postprocess_openquake (rasterize site values -> hazard COG + publish)

Unlike SWMM (in-process pyswmm) OpenQuake is CLOUD-ONLY: the engine is RAM-hungry
(~2 GB/thread) and ships as a containerized CLI, so there is NO in-process lane —
the composer always dispatches to the OpenQuake AWS Batch worker
(``services/workers/openquake/entrypoint.py``) through the SAME generic
run_solver / wait_for_completion seam SFINCS/SWMM use, routed to the openquake
job-def via the per-solver ``GRACE2_AWS_BATCH_JOB_DEF_OPENQUAKE`` env knob.

Returns the ``SeismicHazardLayerURI`` directly (a ``LayerURI`` subtype) so the
``emit_tool_call`` ``add_loaded_layer`` gate fires on it - exactly like
``run_modflow_job`` returns a ``PlumeLayerURI``. The hazard map pairs DIRECTLY
with the existing Pelicun impact path: its ground-motion intensity is Pelicun's
fragility input.

Determinism boundary (Invariant 1): every hazard number the agent narrates comes
from the typed ``SeismicHazardLayerURI`` fields the postprocess computed with
plain arithmetic - never free-generated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from grace2_contracts import new_ulid
from grace2_contracts.openquake_contracts import (
    OpenQuakeRunArgs,
    SeismicHazardLayerURI,
)

from ..pipeline_emitter import begin_substeps, current_emitter, substep
from .postprocess_openquake import (
    PostprocessOpenQuakeError,
    postprocess_openquake,
)

logger = logging.getLogger("grace2_agent.workflows.model_seismic_hazard_scenario")

__all__ = [
    "model_seismic_hazard_scenario",
    "OpenQuakeWorkflowError",
    "OPENQUAKE_SOLVER_NAME",
    "assemble_build_spec",
    "stage_openquake_build_spec",
]

#: The registry key + handle ``solver`` tag for the seismic-hazard engine.
OPENQUAKE_SOLVER_NAME: str = "openquake"


class OpenQuakeWorkflowError(RuntimeError):
    """Raised on any build-spec staging / dispatch / postprocess failure.

    Carries an open-set A.6 ``error_code`` so the agent emitter renders a typed
    error frame. Codes:

    - ``OQ_PARAMS_INVALID`` — the run args could not be coerced.
    - ``OQ_STAGING_FAILED`` — the build_spec could not be staged to S3.
    - ``OQ_SOLVE_FAILED`` — the Batch solve did not complete.
    - ``OQ_BATCH_OUTPUT_MISSING`` — a completed run produced no hazard-map CSV.
    """

    error_code: str = "OPENQUAKE_WORKFLOW_FAILED"

    def __init__(
        self,
        error_code: str,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.details: dict[str, Any] = dict(details or {})


# --------------------------------------------------------------------------- #
# build_spec assembly (PURE — unit-tested in isolation).
# --------------------------------------------------------------------------- #
def assemble_build_spec(run_args: OpenQuakeRunArgs) -> dict[str, Any]:
    """Map ``OpenQuakeRunArgs`` -> the build_spec dict the worker reads.

    Pure (no I/O) so the composer arg-assembly unit-tests in isolation. The
    build_spec is exactly the shape ``job_ini.render_openquake_deck`` consumes
    (bbox + IMT + poe + grid spacing + max distance + GMPE + the G-R source
    params) plus the output globs for the worker's upload step.

    levers STEP 3: the validated ``advanced_physics`` (truncation_level /
    rupture_mesh_spacing_km / width_of_mfd_bin / area_source_discretization_km)
    is MERGED into the build_spec, and ``uniform_hazard_spectra`` is flipped on
    (the classical run already exports hazard curves; UHS needs the flag). None
    => no keys merged => byte-identical job.ini. Invalid keys raise a typed
    ``OpenQuakeWorkflowError("OQ_PHYSICS_INVALID")``.
    """
    from .physics_registry import (
        PhysicsRegistryError,
        validate_and_resolve_physics,
    )

    try:
        resolved = validate_and_resolve_physics(
            "openquake", getattr(run_args, "advanced_physics", None)
        )
    except PhysicsRegistryError as exc:
        raise OpenQuakeWorkflowError(
            "OQ_PHYSICS_INVALID",
            message=f"invalid advanced_physics: {exc}",
            details={"engine": "openquake", "key": getattr(exc, "key", None)},
        ) from exc

    spec = {
        "bbox": list(run_args.bbox),
        "imt": run_args.imt,
        "poe": float(run_args.poe),
        "investigation_time_years": float(run_args.investigation_time_years),
        "site_grid_spacing_km": float(run_args.site_grid_spacing_km),
        "max_distance_km": float(run_args.max_distance_km),
        "gmpe": run_args.gmpe,
        "a_value": float(run_args.a_value),
        "b_value": float(run_args.b_value),
        "min_magnitude": float(run_args.min_magnitude),
        "max_magnitude": float(run_args.max_magnitude),
        # The OpenQuake CSV exports land under output/; capture them + the
        # rendered deck for provenance.
        "outputs": ["output/*.csv", "*.csv"],
    }
    # Merge validated physics overrides (the worker render_job_ini reads them).
    spec.update(resolved)
    # levers STEP 3: request UHS export when the registry-quantities flag is on
    # (default OFF -> byte-identical classical job.ini). The agent reads the
    # exported UHS + hazard-curve CSVs into ScalarField metrics in
    # publish_openquake_quantities.
    if os.environ.get("GRACE2_OPENQUAKE_REGISTRY_QUANTITIES", "").lower() in (
        "1", "true", "on", "yes"
    ):
        spec["uniform_hazard_spectra"] = True
    return spec


# --------------------------------------------------------------------------- #
# build_spec staging (S3) — mirror of stage_swmm_manifest.
# --------------------------------------------------------------------------- #
def stage_openquake_build_spec(
    run_args: OpenQuakeRunArgs, run_id: str
) -> str:
    """Upload the build_spec JSON to S3; return its ``s3://`` URI.

    Mirrors ``run_swmm.stage_swmm_manifest`` EXACTLY (no new client): uses the
    same ``cache.storage_scheme()`` scheme + the same ``solver._get_s3_client()``
    boto3 client + the same ``GRACE2_CACHE_BUCKET`` staging bucket. Feed the
    returned URI STRAIGHT to ``run_solver(solver='openquake',
    model_setup_uri=<this>, ...)``.

    Raises:
        OpenQuakeWorkflowError("OQ_STAGING_FAILED"): the upload could not complete.
    """
    from ..tools.cache import storage_scheme
    from ..tools.solver import _get_s3_client

    scheme = storage_scheme()  # "s3" on AWS
    cache_bucket = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
    prefix = f"cache/static-30d/openquake_setup/{run_id}/"
    spec_key = f"{prefix}build_spec.json"
    spec_uri = f"{scheme}://{cache_bucket}/{spec_key}"

    build_spec = assemble_build_spec(run_args)
    try:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=cache_bucket,
            Key=spec_key,
            Body=json.dumps(build_spec, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        raise OpenQuakeWorkflowError(
            "OQ_STAGING_FAILED",
            message=f"failed to stage OpenQuake build_spec to {spec_uri}: {exc}",
            details={"run_id": run_id, "build_spec_uri": spec_uri},
        ) from exc

    logger.info("stage_openquake_build_spec run_id=%s -> %s", run_id, spec_uri)
    return spec_uri


# --------------------------------------------------------------------------- #
# Batch hazard-map download — mirror of _download_batch_swmm_outputs.
# --------------------------------------------------------------------------- #
def _pick_hazard_map_uri(output_uris: list[str]) -> str | None:
    """Pick the hazard-MAP CSV from the uploaded output URIs (agent-side mirror
    of the worker's ``resolve_hazard_map_csv``, so the agent never imports the
    worker package). Prefer a ``hazard_map`` CSV, fall back to any ``hazard``
    CSV, else None."""
    csvs = [u for u in output_uris if u.lower().endswith(".csv")]
    for u in csvs:
        base = u.rsplit("/", 1)[-1].lower()
        if "hazard_map" in base or "hazard-map" in base:
            return u
    for u in csvs:
        if "hazard" in u.rsplit("/", 1)[-1].lower():
            return u
    return None


def _download_batch_hazard_csv(run_result: Any, run_id: str) -> str:
    """Download the exported hazard-MAP CSV produced by the Batch worker.

    The OpenQuake Batch worker uploads the engine's CSV exports under
    ``s3://<runs_bucket>/<run_id>/output/`` and records the hazard-map URI in
    completion.json (``hazard_map_uri``, with the full ``output_uris`` list as a
    fallback). We re-read completion.json (small, already on S3) to find the
    hazard-map key, download it via the SAME boto3 client the solver dispatch
    uses, and return the local CSV TEXT.

    Raises:
        OpenQuakeWorkflowError("OQ_BATCH_OUTPUT_MISSING"): the completed run did
            not produce a downloadable hazard-map CSV.
    """
    from ..tools.solver import (
        _get_runs_bucket,
        _get_s3_client,
        _split_object_uri,
        _try_get_completion_s3,
    )

    runs_bucket = _get_runs_bucket()
    s3 = _get_s3_client()

    manifest = _try_get_completion_s3(runs_bucket, run_id)
    hazard_uri: str | None = None
    if isinstance(manifest, dict):
        hazard_uri = manifest.get("hazard_map_uri") or _pick_hazard_map_uri(
            [str(u) for u in (manifest.get("output_uris") or [])]
        )

    if not hazard_uri:
        raise OpenQuakeWorkflowError(
            "OQ_BATCH_OUTPUT_MISSING",
            message=(
                "OpenQuake Batch solve completed but produced no hazard-map CSV "
                f"(runs_bucket={runs_bucket} run_id={run_id})"
            ),
            details={"run_id": run_id, "output_uri": getattr(run_result, "output_uri", None)},
        )

    try:
        _scheme, _bucket, key = _split_object_uri(hazard_uri)
    except Exception as exc:  # noqa: BLE001
        raise OpenQuakeWorkflowError(
            "OQ_BATCH_OUTPUT_MISSING",
            message=f"hazard_map_uri unparseable: {hazard_uri!r}: {exc}",
            details={"run_id": run_id},
        ) from exc

    try:
        resp = s3.get_object(Bucket=runs_bucket, Key=key)
        return resp["Body"].read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise OpenQuakeWorkflowError(
            "OQ_BATCH_OUTPUT_MISSING",
            message=f"hazard-map CSV download failed s3://{runs_bucket}/{key}: {exc}",
            details={"run_id": run_id},
        ) from exc


# --------------------------------------------------------------------------- #
# Composer.
# --------------------------------------------------------------------------- #
async def model_seismic_hazard_scenario(
    run_args: OpenQuakeRunArgs,
    *,
    compute_class: str = "standard",
) -> SeismicHazardLayerURI:
    """Run a classical-PSHA OpenQuake hazard calculation end-to-end on AWS Batch.

    Stages a build_spec, dispatches the OpenQuake Batch worker through the
    generic run_solver / wait_for_completion seam, downloads the exported
    hazard-map CSV, and postprocesses it into a published ``SeismicHazardLayerURI``.

    Args:
        run_args: the validated ``OpenQuakeRunArgs``.
        compute_class: FR-CE-3 compute class for the Batch sizing bucket.
            Default ``"standard"`` (OpenQuake is RAM-hungry, so it should size up
            for a larger site grid).

    Returns:
        ``SeismicHazardLayerURI`` (a ``LayerURI`` subtype) — the emitter appends
        it to ``session-state.loaded_layers`` and the map renders the hazard COG.

    Raises:
        OpenQuakeWorkflowError: any staging / dispatch / postprocess step failed.
    """
    from ..tools.solver import run_solver, wait_for_completion

    run_id = new_ulid()
    logger.info(
        "model_seismic_hazard_scenario run_id=%s bbox=%s imt=%s poe=%.4g "
        "inv_time=%.0fyr grid=%.1fkm gmpe=%s compute_class=%s",
        run_id,
        run_args.bbox,
        run_args.imt,
        run_args.poe,
        run_args.investigation_time_years,
        run_args.site_grid_spacing_km,
        run_args.gmpe,
        compute_class,
    )

    # Declare the planned child count up front so the parent card's live
    # breadcrumb can render "k/4" (build_spec -> solve -> download -> publish).
    # No-op when no emitter is bound (verify/CI direct-call path).
    begin_substeps(current_emitter(), 4)

    # 1) Stage the build_spec (sync boto3 off the loop).
    async with substep(current_emitter(), "stage_openquake_build_spec"):
        build_spec_uri = await asyncio.to_thread(
            stage_openquake_build_spec, run_args, run_id
        )

    # 2) Dispatch through the generic run_solver / wait_for_completion seam.
    #    Surface the dispatch + Batch wait as a single "Solved (Batch ...)" child
    #    row; the live Batch readout stays owned by the two-card Sim observability
    #    inside run_solver / wait_for_completion (mint_dispatch_and_sim_cards).
    async with substep(current_emitter(), "run_solver"):
        handle = run_solver(
            solver=OPENQUAKE_SOLVER_NAME,
            model_setup_uri=build_spec_uri,
            compute_class=compute_class,
        )
        run_result = await wait_for_completion(handle)

        # Honesty floor: a non-complete Batch result raises INSIDE the substep so
        # the solve child reads red (failed), not a silent green. The raise re-
        # raises through the substep wrapper unchanged (caller control flow).
        if run_result.status != "complete":
            raise OpenQuakeWorkflowError(
                "OQ_SOLVE_FAILED",
                message=(
                    "OpenQuake Batch solve did not complete "
                    f"(status={run_result.status}, error_code={run_result.error_code}): "
                    f"{run_result.error_message or run_result.cancellation_reason or ''}"
                ),
                details={
                    "run_id": run_id,
                    "output_uri": run_result.output_uri,
                },
            )

    # 3) Download the hazard-map CSV from the worker's run_id prefix (the Batch
    #    dispatch mints a fresh run_id; the worker writes under run_result.run_id,
    #    NOT the composer's run_id — mirror the SWMM/SFINCS Batch lesson).
    batch_run_id = getattr(run_result, "run_id", None) or run_id
    async with substep(current_emitter(), "_download_batch_hazard_csv"):
        hazard_csv_text = await asyncio.to_thread(
            _download_batch_hazard_csv, run_result, batch_run_id
        )

    # 4) Postprocess: rasterize site values -> hazard COG + publish.
    try:
        async with substep(current_emitter(), "postprocess_openquake"):
            layer = await asyncio.to_thread(
                postprocess_openquake,
                hazard_csv_text,
                run_id=batch_run_id,
                imt=run_args.imt,
                poe=float(run_args.poe),
                investigation_time_years=float(run_args.investigation_time_years),
            )
    except PostprocessOpenQuakeError as exc:
        raise OpenQuakeWorkflowError(
            exc.error_code,
            message=str(exc),
            details={"run_id": batch_run_id, **getattr(exc, "details", {})},
        ) from exc

    logger.info(
        "model_seismic_hazard_scenario complete run_id=%s layer_id=%s "
        "max_hazard=%.6g hazard_area_km2=%.6g n_sites=%d uri=%s",
        batch_run_id,
        layer.layer_id,
        layer.max_hazard_value,
        layer.hazard_area_km2,
        layer.n_sites,
        layer.uri,
    )
    return layer
