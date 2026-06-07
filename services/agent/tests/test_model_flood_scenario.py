"""Unit + integration tests for model_flood_scenario workflow (job-0042, M5 capstone).

Coverage maps to the kickoff's 8-test minimum + the headline NLCD-validation
gate (OQ-4 §4 / Invariant 7 mitigation):

1. ``test_registry_registers_run_model_flood_scenario_wrapper`` — the LLM-facing
   wrapper appears in ``TOOL_REGISTRY`` as ``run_model_flood_scenario`` with
   the workflow_dispatch metadata.
2. ``test_nlcd_validation_gate_raises_on_unmapped_class`` — when the fetched
   landcover has a class integer the mapping CSV doesn't cover,
   ``build_sfincs_model`` raises ``SFINCSSetupError("LULC_MAPPING_MISMATCH")``
   with full details (the OQ-4 §4 headline gate).
3. ``test_nlcd_validation_gate_passes_when_subset_of_mapping`` — when the
   fetched classes are a subset of the mapping, the gate passes through
   silently (so HydroMT proceeds).
4. ``test_load_manning_mapping_returns_expected_classes`` — the version-pinned
   CSV loads cleanly with the documented NLCD 2021 class set.
5. ``test_workflow_happy_path_returns_flood_envelope`` — full mocked happy
   path: workflow returns ``AssessmentEnvelope`` with ``hazard_type="flood"``,
   populated ``FloodPayload``, ``layers`` list with the depth COG.
6. ``test_workflow_returns_failed_envelope_when_run_solver_fails`` — when
   ``wait_for_completion`` returns ``RunResult(status="failed",
   error_code="SOLVER_FAILED")`` the workflow returns a typed failed
   envelope carrying the error code in ``flood.metrics.solver_version``.
7. ``test_workflow_returns_failed_envelope_when_nlcd_gate_fires`` —
   end-to-end: the workflow's response to a vintage mismatch is a typed
   failed envelope (not an uncaught exception).
8. ``test_workflow_geocode_fallback_when_bbox_missing`` — ``bbox=None`` +
   ``location_query="Fort Myers, FL"`` routes through ``geocode_location``
   and uses the resolved bbox for the fetcher chain.
9. ``test_workflow_direct_bbox_path_skips_geocode`` — ``bbox`` supplied
   directly, no geocode call.
10. ``test_workflow_bbox_wins_when_both_supplied`` — precedence: direct bbox
    overrides location_query.
11. ``test_workflow_cancellation_propagates`` — ``asyncio.CancelledError``
    raised inside ``wait_for_completion`` propagates out of the workflow.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.workflows.model_flood_scenario import (
    model_flood_scenario,
    run_model_flood_scenario,
)
from grace2_agent.workflows.sfincs_builder import (
    BuildOptions,
    ForcingSpec,
    MANNING_MAPPING_PATH,
    MANNING_MAPPING_VERSION,
    SFINCSSetupError,
    build_sfincs_model,
    load_manning_mapping,
    validate_nlcd_vintage_against_mapping,
)
from grace2_contracts import new_ulid
from grace2_contracts.envelope import AssessmentEnvelope
from grace2_contracts.execution import ExecutionHandle, LayerURI, ModelSetup, RunResult


# --------------------------------------------------------------------------- #
# Test 1 — registration of the wrapper atomic tool
# --------------------------------------------------------------------------- #


def test_registry_registers_run_model_flood_scenario_wrapper() -> None:
    """The LLM-facing wrapper is registered with workflow_dispatch metadata."""
    assert "run_model_flood_scenario" in TOOL_REGISTRY, (
        f"workflow wrapper not in TOOL_REGISTRY; keys={sorted(TOOL_REGISTRY)}"
    )
    entry = TOOL_REGISTRY["run_model_flood_scenario"]
    assert entry.metadata.cacheable is False, "workflow wrapper must be uncacheable"
    assert entry.metadata.ttl_class == "live-no-cache"
    assert entry.metadata.source_class == "workflow_dispatch"
    assert entry.fn is run_model_flood_scenario


# --------------------------------------------------------------------------- #
# Test 2 — NLCD validation gate FAIL path (the OQ-4 §4 headline)
# --------------------------------------------------------------------------- #


def test_nlcd_validation_gate_raises_on_unmapped_class() -> None:
    """A fetched class not covered by the mapping fires LULC_MAPPING_MISMATCH."""
    # Mapping covers classes {11, 41, 81}; the fetched raster has class 99
    # (not in mapping) — gate must fire.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False
    ) as fh:
        fh.write("nlcd_class,manning_n,description\n")
        fh.write("11,0.025,Open Water\n")
        fh.write("41,0.150,Deciduous Forest\n")
        fh.write("81,0.035,Pasture/Hay\n")
        fixture_path = Path(fh.name)
    try:
        mapping = load_manning_mapping(fixture_path)
        assert set(mapping) == {11, 41, 81}
        with pytest.raises(SFINCSSetupError) as excinfo:
            validate_nlcd_vintage_against_mapping(
                fetched_classes={11, 41, 99},  # 99 is unmapped
                nlcd_vintage_year=2021,
                mapping=mapping,
                mapping_version="test-1.0",
                mapping_csv_path=str(fixture_path),
            )
        err = excinfo.value
        assert err.error_code == "LULC_MAPPING_MISMATCH"
        assert err.details["unmapped_classes"] == [99]
        assert err.details["nlcd_vintage_year"] == 2021
        assert err.details["mapping_version"] == "test-1.0"
    finally:
        fixture_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Test 3 — NLCD validation gate PASS path
# --------------------------------------------------------------------------- #


def test_nlcd_validation_gate_passes_when_subset_of_mapping() -> None:
    """When every fetched class is in the mapping, the gate is silent."""
    mapping = load_manning_mapping(MANNING_MAPPING_PATH)
    # NLCD 2021 Fort Myers area: water, dev open, pasture, woody wetlands.
    fetched = {11, 21, 82, 90}
    # Should not raise.
    validate_nlcd_vintage_against_mapping(
        fetched_classes=fetched,
        nlcd_vintage_year=2021,
        mapping=mapping,
    )
    # And: class 0 (nodata) is filtered out even if it appears.
    validate_nlcd_vintage_against_mapping(
        fetched_classes=fetched | {0},
        nlcd_vintage_year=2021,
        mapping=mapping,
    )


# --------------------------------------------------------------------------- #
# Test 4 — version-pinned CSV loads with expected NLCD 2021 classes
# --------------------------------------------------------------------------- #


def test_load_manning_mapping_returns_expected_classes() -> None:
    """Production manning_mapping.csv covers the NLCD 2021 L48 class set."""
    mapping = load_manning_mapping(MANNING_MAPPING_PATH)
    # NLCD 2021 publishes these class integers in the CONUS L48 product;
    # every one must be in our mapping (gate would fire otherwise).
    expected = {11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95}
    missing = expected - set(mapping.keys())
    assert not missing, f"manning_mapping.csv missing classes {missing}"
    # Sanity: every Manning's value is positive + plausible (<= 0.30).
    for cls, n in mapping.items():
        assert 0.0 < n <= 0.30, f"implausible manning_n={n} for nlcd_class={cls}"
    assert MANNING_MAPPING_VERSION == "1.0.0"


# --------------------------------------------------------------------------- #
# Fixtures for full-workflow tests — mocked atomic tools + GCS-aware shims
# --------------------------------------------------------------------------- #


def _make_handle(run_id: str | None = None) -> ExecutionHandle:
    """Construct a valid ExecutionHandle for tests."""
    return ExecutionHandle(
        handle_id=new_ulid(),
        run_id=run_id or new_ulid(),
        solver="sfincs",
        compute_class="standard",
        workflows_execution_id=(
            "projects/test/locations/us-central1/workflows/"
            "grace-2-sfincs-orchestrator/executions/test-exec"
        ),
        workflow_name="grace-2-sfincs-orchestrator",
        workflow_location="us-central1",
        submitted_at=datetime.now(timezone.utc),
    )


def _mock_layer_uri(prefix: str) -> LayerURI:
    return LayerURI(
        layer_id=f"{prefix}-test",
        name=f"{prefix} test layer",
        layer_type="raster",
        uri=f"gs://test-cache/cache/static-30d/{prefix}/test.tif",
        style_preset="continuous_dem",
        role="input",
        units="meters",
    )


# --------------------------------------------------------------------------- #
# Test 5 — happy path: full workflow returns Flood AssessmentEnvelope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_happy_path_returns_flood_envelope() -> None:
    """Mocked happy chain returns AssessmentEnvelope with Flood subtype + layers."""
    run_id = new_ulid()
    handle = _make_handle(run_id=run_id)

    landcover_layer = _mock_layer_uri("landcover")
    landcover_result = {
        "layer": landcover_layer,
        "nlcd_vintage_year": 2021,
        "dataset": "nlcd_2021",
        "source": "mrlc-wms",
    }
    precip_result = {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [26.6, -81.9],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9 Version 2",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }
    model_setup = ModelSetup(
        setup_id=new_ulid(),
        solver="sfincs",
        setup_uri="gs://test-cache/cache/static-30d/sfincs_setup/test/",
        grid_resolution_m=30.0,
        bbox=(-81.92, 26.55, -81.80, 26.68),
        parameters={"nlcd_vintage_year": 2021},
        created_at=datetime.now(timezone.utc),
    )
    run_result_ok = RunResult(
        run_id=run_id,
        handle_id=handle.handle_id,
        status="complete",
        output_uri=f"gs://grace-2-hazard-prod-runs/{run_id}/",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_seconds=120.0,
    )
    flood_layer = LayerURI(
        layer_id=f"flood-depth-peak-{run_id}",
        name="Flood Depth (peak)",
        layer_type="raster",
        uri=f"gs://grace-2-hazard-prod-runs/{run_id}/flood_depth_peak.tif",
        style_preset="continuous_flood_depth",
        role="primary",
        units="meters",
    )
    depth_metrics = {
        "max_depth_m": 2.4,
        "mean_depth_m": 0.6,
        "p95_depth_m": 1.9,
        "flooded_cell_count": 12_345,
        "crs": "EPSG:3857",
        "units": "meters",
    }

    async def _wfc(handle):  # noqa: ANN001 — mock
        return run_result_ok

    with (
        patch("grace2_agent.workflows.model_flood_scenario.fetch_dem", return_value=_mock_layer_uri("dem")),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_landcover", return_value=landcover_result),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_river_geometry", return_value=_mock_layer_uri("rivers")),
        patch("grace2_agent.workflows.model_flood_scenario.lookup_precip_return_period", return_value=precip_result),
        patch("grace2_agent.workflows.model_flood_scenario.build_sfincs_model", return_value=model_setup),
        patch("grace2_agent.workflows.model_flood_scenario.run_solver", return_value=handle),
        patch("grace2_agent.workflows.model_flood_scenario.wait_for_completion", side_effect=_wfc),
        patch(
            "grace2_agent.workflows.model_flood_scenario.postprocess_flood",
            return_value=([flood_layer], depth_metrics),
        ),
    ):
        envelope = await model_flood_scenario(
            bbox=(-81.92, 26.55, -81.80, 26.68),
            return_period_yr=100,
            duration_hr=24,
            compute_class="medium",
        )
    assert isinstance(envelope, AssessmentEnvelope)
    assert envelope.envelope_type == "modeled"
    assert envelope.hazard_type == "flood"
    assert envelope.workflow_name == "model_flood_scenario"
    assert envelope.flood is not None
    assert envelope.flood.metrics.max_depth_m == pytest.approx(2.4)
    assert envelope.flood.metrics.p95_depth_m == pytest.approx(1.9)
    assert envelope.flood.metrics.solver_version == "sfincs-v2.3.3"
    assert envelope.flood.metrics.grid_resolution_m == 30.0
    assert envelope.flood.metrics.simulation_duration_hours == 24
    assert len(envelope.layers) == 1
    assert envelope.layers[0].style_preset == "continuous_flood_depth"
    assert envelope.layers[0].role == "primary"
    assert envelope.forcing is not None
    assert envelope.forcing.forcing_type == "pluvial_synthetic"
    assert envelope.solver_run_ids == [run_id]


# --------------------------------------------------------------------------- #
# Test 6 — SOLVER_FAILED returns a typed failed envelope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_returns_failed_envelope_when_run_solver_fails() -> None:
    """RunResult(status='failed', error_code='SOLVER_FAILED') → failed envelope."""
    handle = _make_handle()
    landcover_result = {
        "layer": _mock_layer_uri("landcover"),
        "nlcd_vintage_year": 2021,
        "dataset": "nlcd_2021",
        "source": "mrlc-wms",
    }
    precip_result = {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [26.6, -81.9],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9 Version 2",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }
    model_setup = ModelSetup(
        setup_id=new_ulid(),
        solver="sfincs",
        setup_uri="gs://test-cache/cache/static-30d/sfincs_setup/test/",
        grid_resolution_m=30.0,
        bbox=(-81.92, 26.55, -81.80, 26.68),
        parameters={},
        created_at=datetime.now(timezone.utc),
    )
    run_result_failed = RunResult(
        run_id=handle.run_id,
        handle_id=handle.handle_id,
        status="failed",
        output_uri=None,
        error_code="SOLVER_FAILED",
        error_message="sfincs exited with non-zero code 2",
    )

    async def _wfc(handle):  # noqa: ANN001
        return run_result_failed

    with (
        patch("grace2_agent.workflows.model_flood_scenario.fetch_dem", return_value=_mock_layer_uri("dem")),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_landcover", return_value=landcover_result),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_river_geometry", return_value=_mock_layer_uri("rivers")),
        patch("grace2_agent.workflows.model_flood_scenario.lookup_precip_return_period", return_value=precip_result),
        patch("grace2_agent.workflows.model_flood_scenario.build_sfincs_model", return_value=model_setup),
        patch("grace2_agent.workflows.model_flood_scenario.run_solver", return_value=handle),
        patch("grace2_agent.workflows.model_flood_scenario.wait_for_completion", side_effect=_wfc),
    ):
        envelope = await model_flood_scenario(
            bbox=(-81.92, 26.55, -81.80, 26.68),
        )
    assert isinstance(envelope, AssessmentEnvelope)
    assert envelope.hazard_type == "flood"
    assert envelope.layers == []
    assert envelope.flood is not None
    assert envelope.flood.metrics.solver_version == "failed:SOLVER_FAILED"
    assert envelope.flood.metrics.max_depth_m == 0.0
    assert envelope.solver_run_ids == [handle.run_id]


# --------------------------------------------------------------------------- #
# Test 7 — NLCD gate firing surfaces as failed envelope end-to-end
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_returns_failed_envelope_when_nlcd_gate_fires() -> None:
    """build_sfincs_model raises LULC_MAPPING_MISMATCH → workflow returns failed envelope."""
    landcover_result = {
        "layer": _mock_layer_uri("landcover"),
        "nlcd_vintage_year": 2099,
        "dataset": "nlcd_2099",
        "source": "mrlc-wms",
    }
    precip_result = {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [26.6, -81.9],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9 Version 2",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }

    def _raising_build_sfincs_model(**kwargs: Any) -> ModelSetup:
        raise SFINCSSetupError(
            "LULC_MAPPING_MISMATCH",
            message="vintage 2099 introduced class 200 not in mapping",
            details={
                "nlcd_vintage_year": 2099,
                "mapping_version": "1.0.0",
                "unmapped_classes": [200],
            },
        )

    with (
        patch("grace2_agent.workflows.model_flood_scenario.fetch_dem", return_value=_mock_layer_uri("dem")),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_landcover", return_value=landcover_result),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_river_geometry", return_value=_mock_layer_uri("rivers")),
        patch("grace2_agent.workflows.model_flood_scenario.lookup_precip_return_period", return_value=precip_result),
        patch("grace2_agent.workflows.model_flood_scenario.build_sfincs_model", side_effect=_raising_build_sfincs_model),
    ):
        envelope = await model_flood_scenario(
            bbox=(-81.92, 26.55, -81.80, 26.68),
        )
    assert isinstance(envelope, AssessmentEnvelope)
    assert envelope.layers == []
    assert envelope.flood is not None
    assert envelope.flood.metrics.solver_version == "failed:LULC_MAPPING_MISMATCH"
    # No solver runs dispatched since build failed.
    assert envelope.solver_run_ids == []


# --------------------------------------------------------------------------- #
# Test 8 — geocode fallback (no bbox, only location_query)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_geocode_fallback_when_bbox_missing() -> None:
    """``bbox=None`` + ``location_query="Fort Myers, FL"`` routes through geocode."""
    geocode_result = {
        "name": "Fort Myers, Lee County, Florida, USA",
        "latitude": 26.6,
        "longitude": -81.9,
        "bbox": [-81.92, 26.55, -81.80, 26.68],
        "source": "nominatim",
        "query": "Fort Myers, FL",
        "osm_type": "relation",
        "osm_id": 12345,
        "place_id": 1,
    }
    landcover_result = {
        "layer": _mock_layer_uri("landcover"),
        "nlcd_vintage_year": 2021,
        "dataset": "nlcd_2021",
        "source": "mrlc-wms",
    }
    precip_result = {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [26.6, -81.9],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9 Version 2",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }

    def _raise_after_fetch(**_kwargs: Any) -> ModelSetup:
        # short-circuit so we don't try to run the solver in this test
        raise SFINCSSetupError("HYDROMT_UNAVAILABLE", message="test stub")

    with (
        patch(
            "grace2_agent.workflows.model_flood_scenario.geocode_location",
            return_value=geocode_result,
        ) as mock_geocode,
        patch("grace2_agent.workflows.model_flood_scenario.fetch_dem", return_value=_mock_layer_uri("dem")) as mock_dem,
        patch("grace2_agent.workflows.model_flood_scenario.fetch_landcover", return_value=landcover_result),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_river_geometry", return_value=_mock_layer_uri("rivers")),
        patch("grace2_agent.workflows.model_flood_scenario.lookup_precip_return_period", return_value=precip_result),
        patch("grace2_agent.workflows.model_flood_scenario.build_sfincs_model", side_effect=_raise_after_fetch),
    ):
        envelope = await model_flood_scenario(
            location_query="Fort Myers, FL",
            return_period_yr=100,
            duration_hr=24,
        )
    mock_geocode.assert_called_once_with("Fort Myers, FL")
    # fetch_dem was called with the geocoded bbox.
    args, kwargs = mock_dem.call_args
    used_bbox = args[0] if args else kwargs.get("bbox")
    assert tuple(used_bbox) == (-81.92, 26.55, -81.80, 26.68)
    # Failed envelope shape — Hydromt unavailable surfaced as the test stub.
    assert envelope.flood.metrics.solver_version == "failed:HYDROMT_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# Test 9 — direct bbox path: geocode is NOT called
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_direct_bbox_path_skips_geocode() -> None:
    """When ``bbox`` is supplied directly, ``geocode_location`` is not called."""
    landcover_result = {
        "layer": _mock_layer_uri("landcover"),
        "nlcd_vintage_year": 2021,
        "dataset": "nlcd_2021",
        "source": "mrlc-wms",
    }
    precip_result = {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [26.6, -81.9],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9 Version 2",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }

    def _raise(**_kwargs: Any) -> ModelSetup:
        raise SFINCSSetupError("HYDROMT_UNAVAILABLE", message="test stub")

    with (
        patch("grace2_agent.workflows.model_flood_scenario.geocode_location") as mock_geocode,
        patch("grace2_agent.workflows.model_flood_scenario.fetch_dem", return_value=_mock_layer_uri("dem")),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_landcover", return_value=landcover_result),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_river_geometry", return_value=_mock_layer_uri("rivers")),
        patch("grace2_agent.workflows.model_flood_scenario.lookup_precip_return_period", return_value=precip_result),
        patch("grace2_agent.workflows.model_flood_scenario.build_sfincs_model", side_effect=_raise),
    ):
        envelope = await model_flood_scenario(
            bbox=(-81.92, 26.55, -81.80, 26.68),
        )
    mock_geocode.assert_not_called()
    assert envelope.bbox == (-81.92, 26.55, -81.80, 26.68)


# --------------------------------------------------------------------------- #
# Test 10 — both supplied → bbox wins (Decision K precedence)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_bbox_wins_when_both_supplied() -> None:
    """``bbox`` + ``location_query`` → bbox takes precedence; geocode NOT called."""
    landcover_result = {
        "layer": _mock_layer_uri("landcover"),
        "nlcd_vintage_year": 2021,
        "dataset": "nlcd_2021",
        "source": "mrlc-wms",
    }
    precip_result = {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [26.6, -81.9],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9 Version 2",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }

    def _raise(**_kwargs: Any) -> ModelSetup:
        raise SFINCSSetupError("HYDROMT_UNAVAILABLE", message="test stub")

    with (
        patch("grace2_agent.workflows.model_flood_scenario.geocode_location") as mock_geocode,
        patch("grace2_agent.workflows.model_flood_scenario.fetch_dem", return_value=_mock_layer_uri("dem")) as mock_dem,
        patch("grace2_agent.workflows.model_flood_scenario.fetch_landcover", return_value=landcover_result),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_river_geometry", return_value=_mock_layer_uri("rivers")),
        patch("grace2_agent.workflows.model_flood_scenario.lookup_precip_return_period", return_value=precip_result),
        patch("grace2_agent.workflows.model_flood_scenario.build_sfincs_model", side_effect=_raise),
    ):
        await model_flood_scenario(
            bbox=(-95.0, 29.0, -94.5, 29.5),
            location_query="Fort Myers, FL",  # should be ignored
        )
    mock_geocode.assert_not_called()
    args, kwargs = mock_dem.call_args
    used_bbox = args[0] if args else kwargs.get("bbox")
    assert tuple(used_bbox) == (-95.0, 29.0, -94.5, 29.5)


# --------------------------------------------------------------------------- #
# Test 11 — asyncio.CancelledError propagates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_cancellation_propagates() -> None:
    """asyncio.CancelledError raised inside wait_for_completion propagates."""
    handle = _make_handle()
    landcover_result = {
        "layer": _mock_layer_uri("landcover"),
        "nlcd_vintage_year": 2021,
        "dataset": "nlcd_2021",
        "source": "mrlc-wms",
    }
    precip_result = {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [26.6, -81.9],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9 Version 2",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }
    model_setup = ModelSetup(
        setup_id=new_ulid(),
        solver="sfincs",
        setup_uri="gs://test-cache/cache/static-30d/sfincs_setup/test/",
        grid_resolution_m=30.0,
        bbox=(-81.92, 26.55, -81.80, 26.68),
        parameters={},
        created_at=datetime.now(timezone.utc),
    )

    async def _wfc_cancelled(handle):  # noqa: ANN001
        raise asyncio.CancelledError()

    with (
        patch("grace2_agent.workflows.model_flood_scenario.fetch_dem", return_value=_mock_layer_uri("dem")),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_landcover", return_value=landcover_result),
        patch("grace2_agent.workflows.model_flood_scenario.fetch_river_geometry", return_value=_mock_layer_uri("rivers")),
        patch("grace2_agent.workflows.model_flood_scenario.lookup_precip_return_period", return_value=precip_result),
        patch("grace2_agent.workflows.model_flood_scenario.build_sfincs_model", return_value=model_setup),
        patch("grace2_agent.workflows.model_flood_scenario.run_solver", return_value=handle),
        patch("grace2_agent.workflows.model_flood_scenario.wait_for_completion", side_effect=_wfc_cancelled),
    ):
        with pytest.raises(asyncio.CancelledError):
            await model_flood_scenario(
                bbox=(-81.92, 26.55, -81.80, 26.68),
            )


# --------------------------------------------------------------------------- #
# Test 12 — OQ-49 hotfix (job-0052): SfincsModel.build receives a parsed dict,
# NOT the raw YAML text blob. This is the regression guard for the
# ``'str' object has no attribute 'keys'`` failure surfaced by job-0049's
# M5 smoke run against hydromt-sfincs 1.2.2.
# --------------------------------------------------------------------------- #


def test_build_sfincs_model_passes_parsed_dict_to_hydromt_build(
    tmp_path: Path,
) -> None:
    """``model.build(opt=...)`` must receive a Dict[str, Dict], not a YAML string.

    hydromt-sfincs 1.2.x's ``SfincsModel.build`` parses ``opt`` by calling
    ``.keys()`` on every step value, so a raw YAML text blob raises
    ``'str' object has no attribute 'keys'`` deep inside ``_parse_steps``.
    The OQ-49 fix is ``yaml.safe_load(yaml_text)`` before passing — this test
    asserts the corrected path: ``model.build`` is called exactly once with
    ``opt`` shaped as a parsed mapping carrying the expected top-level step
    keys (``setup_config``, ``setup_grid_from_region``, ...).
    """
    # Subset Manning's CSV the gate will accept against fetched_classes={11, 41}.
    mapping_path = tmp_path / "manning.csv"
    mapping_path.write_text(
        "nlcd_class,manning_n,description\n"
        "11,0.025,Open Water\n"
        "41,0.150,Deciduous Forest\n",
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    class _FakeSfincsModel:
        def __init__(self, root: str, mode: str) -> None:  # noqa: D401
            captured["root"] = root
            captured["mode"] = mode

        def build(self, opt: Any) -> None:  # noqa: D401
            captured["opt"] = opt
            captured["opt_type"] = type(opt).__name__

        def write(self) -> None:  # noqa: D401
            captured["write_called"] = True

    fake_module = MagicMock()
    fake_module.SfincsModel = _FakeSfincsModel

    forcing = ForcingSpec(
        forcing_type="pluvial_synthetic",
        precip_inches=11.9,
        duration_hours=24.0,
        return_period_years=100,
        provenance={"source": "noaa-atlas14"},
    )

    with (
        patch.dict(
            "sys.modules",
            {"hydromt_sfincs": fake_module},
        ),
        patch(
            "grace2_agent.workflows.sfincs_builder._extract_unique_nlcd_classes",
            return_value={11, 41},
        ),
        # fsspec upload is best-effort — let it fail and fall back to file://.
        patch.dict(
            "sys.modules",
            {"fsspec": MagicMock(filesystem=MagicMock(side_effect=RuntimeError("no gcs in test")))},
            clear=False,
        ),
    ):
        setup = build_sfincs_model(
            dem_uri="gs://test/dem.tif",
            landcover_uri="gs://test/landcover.tif",
            river_geometry_uri=None,
            forcing=forcing,
            bbox=(-81.92, 26.55, -81.80, 26.68),
            options=BuildOptions(grid_resolution_m=30.0, simulation_hours=24.0),
            nlcd_vintage_year=2021,
            manning_mapping_csv=mapping_path,
        )

    # The fix: opt is a parsed dict (not the YAML string) — the type that
    # hydromt-sfincs 1.2.x ``_parse_steps`` actually accepts.
    assert "opt" in captured, "model.build was not called"
    assert captured["opt_type"] == "dict", (
        f"OQ-49 regression: model.build received {captured['opt_type']!r} "
        f"(expected 'dict'); raw YAML string would re-trigger "
        f"'str' object has no attribute 'keys' inside hydromt-sfincs 1.2.x."
    )
    opt = captured["opt"]
    assert isinstance(opt, dict)
    # The parsed step keys our YAML config emits; nested values are dicts too.
    assert len(opt) > 0, "parsed opt dict is empty — YAML config generation broke"
    for step_name, step_kwargs in opt.items():
        assert isinstance(step_name, str)
        # hydromt-sfincs calls .keys() on every step value — must be a mapping.
        assert hasattr(step_kwargs, "keys"), (
            f"step {step_name!r} value is not a mapping; this is exactly the "
            f"'str' object has no attribute 'keys' shape that OQ-49 hit."
        )
    assert captured.get("write_called") is True
    assert setup.solver == "sfincs"


# --------------------------------------------------------------------------- #
# Test 13 — OQ-49 hotfix: malformed YAML surfaces as typed HYDROMT_BUILD_FAILED
# (FR-FR-2 substrate-integrity routing). yaml.safe_load is the seam where a
# bad config raises; the broad except wraps it into a SFINCSSetupError carrying
# the underlying message — never an uncaught crash.
# --------------------------------------------------------------------------- #


def test_build_sfincs_model_malformed_yaml_surfaces_typed_error(
    tmp_path: Path,
) -> None:
    """Malformed YAML from the config generator → HYDROMT_BUILD_FAILED."""
    mapping_path = tmp_path / "manning.csv"
    mapping_path.write_text(
        "nlcd_class,manning_n,description\n"
        "11,0.025,Open Water\n"
        "41,0.150,Deciduous Forest\n",
        encoding="utf-8",
    )

    # Patch the YAML generator to emit a string yaml.safe_load cannot parse.
    # The fake hydromt_sfincs module should never be reached (the parse fails
    # before model.build is invoked).
    fake_module = MagicMock()
    fake_module.SfincsModel = MagicMock(
        side_effect=AssertionError("SfincsModel must NOT be constructed on parse failure")
    )

    forcing = ForcingSpec(
        forcing_type="pluvial_synthetic",
        precip_inches=11.9,
        duration_hours=24.0,
        return_period_years=100,
        provenance={"source": "noaa-atlas14"},
    )

    malformed_yaml = "this: is: not: valid: yaml: ::: ["

    with (
        patch.dict("sys.modules", {"hydromt_sfincs": fake_module}, clear=False),
        patch(
            "grace2_agent.workflows.sfincs_builder._extract_unique_nlcd_classes",
            return_value={11, 41},
        ),
        patch(
            "grace2_agent.workflows.sfincs_builder._generate_hydromt_yaml_config",
            return_value=malformed_yaml,
        ),
    ):
        with pytest.raises(SFINCSSetupError) as excinfo:
            build_sfincs_model(
                dem_uri="gs://test/dem.tif",
                landcover_uri="gs://test/landcover.tif",
                river_geometry_uri=None,
                forcing=forcing,
                bbox=(-81.92, 26.55, -81.80, 26.68),
                options=BuildOptions(grid_resolution_m=30.0, simulation_hours=24.0),
                nlcd_vintage_year=2021,
                manning_mapping_csv=mapping_path,
            )

    err = excinfo.value
    assert err.error_code == "HYDROMT_BUILD_FAILED", (
        f"malformed YAML must surface as HYDROMT_BUILD_FAILED for FR-FR-2 "
        f"substrate-integrity routing; got {err.error_code!r}"
    )
    # Provenance: the wrapped error carries the bbox + URIs so the failed
    # envelope's pipeline strip can render a meaningful failure.
    assert err.details["bbox"] == [-81.92, 26.55, -81.80, 26.68]
    assert err.details["dem_uri"] == "gs://test/dem.tif"
    assert err.details["landcover_uri"] == "gs://test/landcover.tif"
    assert "underlying" in err.details
