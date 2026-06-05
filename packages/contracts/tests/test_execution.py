"""Round-trip + invariant tests for solver-execution shapes (FR-TA-2)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from grace2_contracts.common import new_ulid
from grace2_contracts.envelope import TemporalConfig
from grace2_contracts.execution import (
    ExecutionHandle,
    LayerURI,
    ModelSetup,
    RunResult,
)
from grace2_contracts.ws import LoadLayerArgs, MapTemporal


def test_model_setup_roundtrip() -> None:
    ms = ModelSetup(
        setup_id=new_ulid(),
        solver="sfincs",
        setup_uri="gs://grace-2/setups/01HX/",
        grid_resolution_m=10.0,
        bbox=(-82.5, 26.4, -81.7, 26.9),
        parameters={"manning": 0.04},
        created_at="2026-06-05T12:00:00Z",
    )
    a = ms.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = ModelSetup.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)


def test_execution_handle_pins_workflows_execution_id_invariant_8() -> None:
    """Invariant 8: the handle carries the Cloud Workflows execution identifier as
    a first-class field. agent calls Workflows `terminate` with it on cancel."""
    handle = ExecutionHandle(
        handle_id=new_ulid(),
        run_id=new_ulid(),
        solver="sfincs",
        compute_class="standard",
        workflows_execution_id="projects/grace-2/locations/us-central1/workflows/sfincs-run/executions/01HX",
        workflow_name="sfincs-run",
        workflow_location="us-central1",
        submitted_at="2026-06-05T12:00:00Z",
    )
    dumped = handle.model_dump(mode="json")
    assert "workflows_execution_id" in dumped
    assert dumped["workflows_execution_id"].startswith("projects/")
    # The handle must not silently accept a workflows_execution_id rename
    with pytest.raises(ValidationError):
        ExecutionHandle.model_validate({**dumped, "wf_id": dumped["workflows_execution_id"]})


def test_run_result_status_supports_cancelled() -> None:
    """Invariant 8: cancelled is distinct from failed."""
    rr = RunResult(
        run_id=new_ulid(),
        handle_id=new_ulid(),
        status="cancelled",
        cancellation_reason="user-requested",
        started_at="2026-06-05T12:00:00Z",
        completed_at="2026-06-05T12:01:00Z",
    )
    a = rr.model_dump(mode="json")
    again = RunResult.model_validate(a).model_dump(mode="json")
    assert a == again


def test_layer_uri_maps_field_for_field_onto_load_layer_args() -> None:
    """The visualization seam: LayerURI -> map-command load-layer with no
    translation beyond plumbing the WMS URL."""
    layer = LayerURI(
        layer_id="run-01HX-flood-depth",
        name="Flood depth (m)",
        layer_type="raster",
        uri="gs://grace-2/runs/01HX/depth.cog.tif",
        style_preset="flood_depth_blue",
        temporal=TemporalConfig(
            start="2022-09-28T00:00:00Z",
            end="2022-09-30T00:00:00Z",
            step_seconds=3600,
        ),
        role="primary",
        units="meters",
    )
    args = LoadLayerArgs(
        layer_id=layer.layer_id,
        wms_url="https://qgis.example.com/wms?MAP=01HX.qgs",
        style_preset=layer.style_preset,
        temporal=MapTemporal(
            start=layer.temporal.start,
            end=layer.temporal.end,
            step_seconds=layer.temporal.step_seconds,
        ),
    )
    assert args.layer_id == layer.layer_id
    assert args.style_preset == layer.style_preset
    assert args.temporal is not None
    assert args.temporal.step_seconds == layer.temporal.step_seconds
