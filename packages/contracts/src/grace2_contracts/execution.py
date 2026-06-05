"""Solver-execution shapes (FR-TA-2): ModelSetup, RunResult, ExecutionHandle,
LayerURI.

These are the return types of the model-setup/execution tool chain:
- ``build_sfincs_model(...)  -> ModelSetup``
- ``run_solver(...)          -> ExecutionHandle``
- ``wait_for_completion(...) -> RunResult``
- ``postprocess_flood(...)   -> list[LayerURI]``

Invariants this module is responsible for:
- **8. Cancellation is first-class.** ``ExecutionHandle`` carries the Cloud
  Workflows execution identifier as a first-class field
  (``workflows_execution_id``) so ``agent`` calls Workflows ``terminate``
  without string-parsing. There is one handle type; no per-backend variants.
- **``LayerURI`` aligns field-for-field with ``map-command load-layer`` args**
  (``layer_id``, ``style_preset``, optional ``temporal``) and with
  ``ResultLayer`` so postprocess output flows to the map without translation.
  Output formats are fixed: rasters COG, vectors FlatGeobuf/GeoParquet.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import GraceModel, ULIDStr, UTCDatetime
from .envelope import TemporalConfig

__all__ = [
    "ComputeClass",
    "ModelSetup",
    "ExecutionHandle",
    "RunResult",
    "LayerURI",
]


# Open enum: compute classes a solver may request. Engine/infra extend as
# backends are added; the handle shape does not change per backend.
ComputeClass = Literal["small", "standard", "large", "gpu"]


class ModelSetup(GraceModel):
    """Returned by ``build_sfincs_model`` (HydroMT). A staged, ready-to-run model.

    The built model artifacts live in GCS; ``setup_uri`` points at them.
    ``parameters`` is solver-specific staging metadata (grid, forcing, options)
    validated at the engine layer.
    """

    schema_version: Literal["v1"] = "v1"

    setup_id: ULIDStr
    solver: str  # e.g., "sfincs"
    setup_uri: str  # gs://... staged model inputs
    grid_resolution_m: float = Field(gt=0.0)
    bbox: tuple[float, float, float, float]
    parameters: dict = Field(default_factory=dict)  # solver-specific staging
    created_at: UTCDatetime


class ExecutionHandle(GraceModel):
    """Returned by ``run_solver``. The cancellation contract (invariant 8).

    ``workflows_execution_id`` is the Cloud Workflows execution identifier —
    the pinned cancellation seam. ``agent`` calls Workflows ``terminate`` with
    it on cancel; ``infra`` provisions the workflow definitions it names. All
    three cite this same handle (orchestrator "Solver cancellation chain").
    """

    schema_version: Literal["v1"] = "v1"

    handle_id: ULIDStr
    run_id: ULIDStr  # the runs._id / solver_run_id this execution backs
    solver: str
    compute_class: ComputeClass

    # --- Cancellation seam (FR-CE-2/3, FR-AS-6) ---
    workflows_execution_id: str  # Cloud Workflows execution identifier
    workflow_name: str  # the Cloud Workflows definition name
    workflow_location: str  # GCP region of the workflow execution

    submitted_at: UTCDatetime


class RunResult(GraceModel):
    """Returned by ``wait_for_completion``. Terminal outcome of an execution.

    ``status`` mirrors the ``runs`` lifecycle; ``cancelled`` is distinct from
    ``failed`` (invariant 8). ``output_uri`` points at the raw solver output in
    GCS, which ``postprocess_flood`` consumes to produce ``LayerURI`` objects.
    """

    schema_version: Literal["v1"] = "v1"

    run_id: ULIDStr
    handle_id: ULIDStr
    status: Literal["complete", "failed", "cancelled"]
    output_uri: str | None = None  # gs://... raw solver output (None if not complete)
    started_at: UTCDatetime | None = None
    completed_at: UTCDatetime | None = None
    duration_seconds: float | None = None

    # Failure details (status == "failed")
    error_code: str | None = None
    error_message: str | None = None

    # Cancellation details (status == "cancelled")
    cancellation_reason: str | None = None


class LayerURI(GraceModel):
    """Returned by ``postprocess_flood`` (one per output layer).

    Aligned field-for-field with ``map-command load-layer`` args and with
    ``ResultLayer`` so postprocess output maps onto the visualization seam with
    no translation. ``uri`` is a COG (raster) or FlatGeobuf/GeoParquet (vector).
    """

    layer_id: str  # stable id; flows into map-command load-layer args
    name: str
    layer_type: Literal["raster", "vector"]
    uri: str  # gs://... COG / FlatGeobuf / GeoParquet
    style_preset: str  # references the QML preset library
    temporal: TemporalConfig | None = None  # present iff time-varying
    role: Literal["primary", "context", "input"] = "primary"
    units: str | None = None
