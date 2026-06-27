"""Atomic tool ``run_modflow_archetype_job``  -  MODFLOW Wave-1 GWF-only engines.

The shared LLM-facing exposure of the three sprint-18 Wave-1 MODFLOW archetypes
(``sustainable_yield`` / ``mine_dewatering`` / ``regional_water_budget``). All
three REUSE the live MODFLOW solver path (``workflows/run_modflow.py`` deck-build
-> submit/local-run, the ``modflow`` Batch job-def, the GWF-only branch of
``services/workers/modflow/gwt_adapter.build_modflow_deck``)  -  there is NO new
worker, container, or Batch job-def. They differ ONLY in the per-archetype
forcing they thread into ``MODFLOWRunArgs`` and the postprocess reader they pick:

  * ``sustainable_yield``     -> ``postprocess_drawdown``        -> DrawdownLayerURI
  * ``mine_dewatering``       -> ``postprocess_dewatering``      -> DewaterLayerURI
  * ``regional_water_budget`` -> ``postprocess_budget_partition``-> BudgetPartitionLayerURI

Chain (mirrors ``run_modflow_job`` with the archetype branch):

  1. Build + stage a GWF-only archetype deck (``build_and_stage_modflow_deck``
     threads ``run_args.archetype`` + the per-archetype fields into the adapter's
     GWF-only branch). The deck writes head (``gwf_model.hds``) + budget
     (``gwf_model.cbc``) and NO UCN concentration.
  2. Run mf6 (AWS Batch ``modflow`` job-def, or local ``mf6`` when
     ``GRACE2_MODFLOW_LOCAL=1``)  -  the SAME submit/wait/cancel seam as
     ``run_modflow_job``.
  3. Postprocess the head / cbc into the archetype's headline LayerURI.
  4. Return it so the emitter's ``add_loaded_layer`` gate loads it onto the map.

This tool is the engine surface the three composers
(``model_{sustainable_yield,mine_dewatering,regional_water_budget}_scenario``)
dispatch to. The USER-INPUT honesty gate (no fabricated well / pit) lives in the
COMPOSERS  -  by the time a request reaches this tool the contract args carry the
real user-supplied geometry. As a backstop, an absent required field raises a
typed ValueError in the adapter (surfaced here as a typed error envelope).

Determinism boundary (Invariant 1): every narrated number comes from the typed
LayerURI fields the postprocess computed  -  never free-generated.

FR-DC-6: ``cacheable=False`` + ``ttl_class="live-no-cache"`` +
``source_class="workflow_dispatch"``  -  the cache shim is NOT invoked.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from grace2_contracts.execution import LayerURI, RunResult
from grace2_contracts.modflow_contracts import MODFLOWRunArgs

from . import register_tool
from ..pipeline_emitter import current_emitter
from ..workflows.postprocess_modflow import (
    PostprocessMODFLOWError,
    postprocess_asr,
    postprocess_budget_partition,
    postprocess_dewatering,
    postprocess_drawdown,
    postprocess_mounding,
    postprocess_wetland_hydroperiod,
)
from ..workflows.run_modflow import (
    MODFLOWWorkflowError,
    build_and_stage_modflow_deck,
    is_local_mode,
    run_modflow_local,
    submit_modflow_run,
)
from ..workflows.solve_progress import drive_live_solve_progress

logger = logging.getLogger("grace2_agent.tools.run_modflow_archetype_tool")

__all__ = [
    "run_modflow_archetype_job",
    "RunMODFLOWArchetypeError",
    "ARCHETYPE_POSTPROCESS",
]


class RunMODFLOWArchetypeError(RuntimeError):
    """Raised when the archetype chain fails fatally before producing a layer."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


#: archetype -> (postprocess callable, headline-scalar attr the logger reads).
#: The callable signature is uniform (run_outputs_uri, *, run_id, model_crs,
#: deck_dir) so the dispatch below is one branchless lookup. The Wave-2 trio
#: (MAR / ASR / wetland_hydroperiod) extends it additively.
ARCHETYPE_POSTPROCESS: dict[str, Any] = {
    "sustainable_yield": (postprocess_drawdown, "max_drawdown_m"),
    "mine_dewatering": (postprocess_dewatering, "dewatering_rate_m3_day"),
    "regional_water_budget": (postprocess_budget_partition, "budget_partition_m3_day"),
    "MAR": (postprocess_mounding, "max_mounding_m"),
    "ASR": (postprocess_asr, "head_timeseries"),
    "wetland_hydroperiod": (
        postprocess_wetland_hydroperiod,
        "seasonal_head_range_m",
    ),
}

#: Archetypes whose headline deliverable is a SERIES / dict (truthy-when-present)
#: rather than a positive scalar. The empty-result honesty floor checks these for
#: presence (a non-empty list/dict) instead of ``float(headline) > 0``: the ASR
#: deliverable is the well-head sawtooth series (recovery_efficiency may legitimately
#: be None on a single cycle), and the budget partition is a dict.
_NON_SCALAR_HEADLINES: frozenset[str] = frozenset(
    {"regional_water_budget", "ASR"}
)


def _runs_prefix() -> str:
    """Default runs bucket name for composing a fallback output prefix."""
    return os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")


async def run_modflow_archetype_job(
    run_args: MODFLOWRunArgs,
    *,
    compute_class: str = "standard",
) -> LayerURI | dict[str, Any]:
    """Run one Wave-1 GWF-only MODFLOW archetype and postprocess its headline layer.

    Internal engine surface (the composers call this with a fully-assembled
    ``MODFLOWRunArgs``; it is NOT registered as a thin LLM tool because the three
    composer dispatch tools are the LLM-facing surface). Selects the postprocess
    by ``run_args.archetype`` and returns the archetype's typed headline LayerURI
    (``DrawdownLayerURI`` / ``DewaterLayerURI`` / ``BudgetPartitionLayerURI``).

    Args:
        run_args: the assembled MODFLOW run args with ``archetype`` set to one of
            ``sustainable_yield`` / ``mine_dewatering`` / ``regional_water_budget``
            and the per-archetype geometry fields populated.
        compute_class: FR-CE-3 compute class.

    Returns:
        On success: the archetype's headline LayerURI subtype (a ``LayerURI`` so
        the emitter loads it onto the map). On failure: a dict with
        ``status="error"`` + ``error_code`` + ``error_message`` so the caller
        narrates the failure honestly (no layer, never a fabricated success).
    """
    archetype = getattr(run_args, "archetype", None)
    if archetype not in ARCHETYPE_POSTPROCESS:
        return {
            "status": "error",
            "error_code": "MODFLOW_ARCHETYPE_UNKNOWN",
            "error_message": (
                f"run_modflow_archetype_job requires a known archetype "
                f"(one of {sorted(ARCHETYPE_POSTPROCESS)}); got {archetype!r}."
            ),
        }
    postprocess_fn, headline_attr = ARCHETYPE_POSTPROCESS[archetype]

    logger.info(
        "run_modflow_archetype_job archetype=%s aoi=%s compute=%s local=%s",
        archetype,
        run_args.spill_location_latlon,
        compute_class,
        is_local_mode(),
    )

    staging = None
    try:
        # --- Step 1: build + stage the GWF-only archetype deck (off-loop) ----
        staging = await asyncio.to_thread(build_and_stage_modflow_deck, run_args)

        # --- Step 2: run the solver (local or Batch) -------------------------
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
                logger.info("run_modflow_archetype_job cancelled while awaiting solver")
                raise
            if run_result.status != "complete":
                return {
                    "status": "error",
                    "error_code": run_result.error_code or run_result.status.upper(),
                    "error_message": (
                        run_result.error_message
                        or run_result.cancellation_reason
                        or "MODFLOW archetype solver did not complete"
                    ),
                }
            run_outputs_uri = (
                run_result.output_uri or f"gs://{_runs_prefix()}/{run_result.run_id}/"
            )

        # --- Step 3: postprocess the head / cbc -> archetype headline layer --
        layer: LayerURI = await asyncio.to_thread(
            lambda: postprocess_fn(
                run_outputs_uri,
                run_id=staging.run_id,
                model_crs=staging.model_crs,
                deck_dir=staging.local_deck_dir,
            )
        )

        # Honesty floor: a "modeled" archetype layer with an empty deliverable
        # must NOT read as a successful layer. The budget partition is empty when
        # the CBC had no non-trivial source/sink term; drawdown is zero when the
        # well drew nothing; dewatering is zero when the drains removed nothing.
        headline = getattr(layer, headline_attr, None)
        if archetype in _NON_SCALAR_HEADLINES:
            empty = not headline  # an empty partition dict / empty head series
        else:
            empty = not headline or float(headline) <= 0.0
        if empty:
            return {
                "status": "error",
                "error_code": "MODFLOW_ARCHETYPE_EMPTY_RESULT",
                "error_message": (
                    f"the {archetype} run produced no non-trivial result "
                    f"({headline_attr}={headline!r}); check the well / pit / "
                    "gradient forcing. No layer was loaded."
                ),
            }

        logger.info(
            "run_modflow_archetype_job complete archetype=%s run_id=%s %s=%s uri=%s",
            archetype,
            staging.run_id,
            headline_attr,
            headline,
            layer.uri,
        )
        return layer

    except asyncio.CancelledError:
        raise
    except (MODFLOWWorkflowError, PostprocessMODFLOWError) as exc:
        logger.warning(
            "run_modflow_archetype_job failed: %s (%s)", exc.error_code, exc
        )
        return {
            "status": "error",
            "error_code": exc.error_code,
            "error_message": str(exc),
        }
    except ValueError as exc:
        # The adapter raises a ValueError when a required per-archetype field is
        # missing (the engine-side backstop to the composer honesty gate).
        logger.warning("run_modflow_archetype_job input error: %s", exc)
        return {
            "status": "error",
            "error_code": "MODFLOW_ARCHETYPE_INPUT_INVALID",
            "error_message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001  -  defensive catch-all
        logger.exception("run_modflow_archetype_job unexpected failure")
        return {
            "status": "error",
            "error_code": "MODFLOW_ARCHETYPE_INTERNAL_ERROR",
            "error_message": str(exc),
        }
    finally:
        if staging is not None:
            try:
                deck_base = Path(staging.local_deck_dir).parent
                if deck_base.name.startswith("modflow-"):
                    shutil.rmtree(deck_base, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
