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


# --------------------------------------------------------------------------- #
# Test 14 — OQ-52 hotfix (job-0053): the setup_manning_roughness step emits
# the hydromt-sfincs 1.2.x-accepted kwarg shape. The live signature is
# ``setup_manning_roughness(datasets_rgh, manning_land, manning_sea,
# rgh_lev_land)`` — there is NO top-level ``map_fn`` keyword. The LULC →
# Manning's reclass CSV is threaded INSIDE each ``datasets_rgh`` entry
# under the key ``reclass_table`` (per ``_parse_datasets_rgh``), and the
# CSV itself must have first column = LULC class (index_col=0) plus a
# column literally named ``N``. This test is the regression guard.
# --------------------------------------------------------------------------- #


def test_build_sfincs_model_emits_v1_2_x_manning_roughness_kwargs(
    tmp_path: Path,
) -> None:
    """``setup_manning_roughness`` kwargs must match hydromt-sfincs 1.2.x.

    Failure modes this guards against:
      * Re-emitting a top-level ``map_fn`` key (1.2.x rejects with
        ``TypeError: setup_manning_roughness() got an unexpected keyword
        argument 'map_fn'`` — the OQ-52 blocker observed by job-0052).
      * Forgetting ``reclass_table`` inside each ``datasets_rgh`` entry —
        without it, ``_parse_datasets_rgh`` raises ``IOError("Manning
        roughness 'reclass_table' csv file must be provided")``.
      * Writing the reclass CSV without an ``N`` column — HydroMT
        reclassifies via ``df_map[["N"]]``; the wrong column header is a
        silent-wrong-answer in waiting.
    """
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

        def build(self, opt: Any) -> None:  # noqa: D401
            captured["opt"] = opt

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
        patch.dict("sys.modules", {"hydromt_sfincs": fake_module}, clear=False),
        patch(
            "grace2_agent.workflows.sfincs_builder._extract_unique_nlcd_classes",
            return_value={11, 41},
        ),
    ):
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

    opt = captured.get("opt")
    assert isinstance(opt, dict), "model.build was not called with a parsed dict"
    assert "setup_manning_roughness" in opt, (
        f"setup_manning_roughness step missing from build opt; got keys {list(opt)}"
    )
    rgh_kwargs = opt["setup_manning_roughness"]
    assert isinstance(rgh_kwargs, dict)

    # The OQ-52 regression: ``map_fn`` MUST NOT appear as a top-level key.
    assert "map_fn" not in rgh_kwargs, (
        "OQ-52 regression: setup_manning_roughness emitted top-level 'map_fn' "
        "kwarg — hydromt-sfincs 1.2.x raises "
        "'TypeError: got an unexpected keyword argument map_fn'. The reclass "
        "table belongs INSIDE each datasets_rgh entry as 'reclass_table'."
    )

    # Verify the v1.2.x-accepted kwarg names are present (every key here is
    # a parameter of the live 1.2.2 SfincsModel.setup_manning_roughness
    # signature: datasets_rgh, manning_land, manning_sea, rgh_lev_land).
    assert "datasets_rgh" in rgh_kwargs
    valid_top_level_keys = {
        "datasets_rgh",
        "manning_land",
        "manning_sea",
        "rgh_lev_land",
    }
    extra = set(rgh_kwargs.keys()) - valid_top_level_keys
    assert not extra, (
        f"setup_manning_roughness has unexpected top-level kwargs {extra}; "
        f"hydromt-sfincs 1.2.x accepts only {valid_top_level_keys}."
    )

    # datasets_rgh is a list[dict]; each entry must carry lulc + reclass_table
    # (the only path through ``_parse_datasets_rgh`` that hits a
    # reclassification). Without reclass_table the parser raises IOError.
    datasets = rgh_kwargs["datasets_rgh"]
    assert isinstance(datasets, list) and len(datasets) == 1
    entry = datasets[0]
    assert "lulc" in entry, f"datasets_rgh entry missing 'lulc'; got {entry}"
    assert "reclass_table" in entry, (
        f"datasets_rgh entry missing 'reclass_table' (this was 'map_fn' "
        f"pre-fix); hydromt-sfincs 1.2.x ``_parse_datasets_rgh`` requires "
        f"this key alongside ``lulc``. Got: {entry}"
    )

    # The reclass_table the YAML points at must exist and carry the v1.2.x
    # column shape (first column = LULC class index; column named ``N``).
    reclass_csv_path = Path(entry["reclass_table"])
    assert reclass_csv_path.exists() or reclass_csv_path.name == "manning_reclass.csv", (
        f"reclass_table CSV path {reclass_csv_path} should be the temp file "
        f"written by _write_hydromt_reclass_table_csv"
    )

    # Independent unit check on the writer itself — round-trip the in-memory
    # mapping through the CSV format hydromt-sfincs 1.2.x reads. This is the
    # behavior of the helper that supplies the on-disk substrate the YAML
    # references.
    from grace2_agent.workflows.sfincs_builder import (
        _write_hydromt_reclass_table_csv,
    )

    out = _write_hydromt_reclass_table_csv(
        {11: 0.025, 41: 0.150}, tmp_path / "rt.csv"
    )
    text = out.read_text(encoding="utf-8")
    # First row is the header: first column = index, then ``N``.
    header_line = text.splitlines()[0]
    cols = [c.strip() for c in header_line.split(",")]
    assert cols[0] in {"nlcd_class", "lulc", "class"}, (
        f"reclass_table first column must be the LULC class index; got {cols[0]!r}"
    )
    assert "N" in cols, (
        f"reclass_table must have a column literally named 'N' — "
        f"hydromt-sfincs 1.2.x ``_parse_datasets_rgh`` indexes ``df_map[['N']]``. "
        f"Got header columns: {cols}"
    )


# --------------------------------------------------------------------------- #
# Test 15 — v0.1 scope guard (job-0055, OQ-54 routing recommendation b):
# ``setup_river_inflow`` must NOT appear in the YAML for ``pluvial_synthetic``
# mode. The v0.1 M5 demo is pluvial-only (Atlas 14 design storm); river inflow
# is M5+ / sprint-9+ scope. Additionally, hydromt-sfincs 1.2.2's
# ``set_forcing_1d`` (sfincs.py:1858) calls ``pd.RangeIndex.is_integer()``
# which was removed in pandas ≥ 2.0 (we run 3.0.3); this upstream bug is
# exercised by the river-inflow path. Dropping the step bypasses
# ``set_forcing_1d`` entirely.
#
# Historical note (job-0054, OQ-53): this was previously a guard that
# ``setup_river_inflow`` was present but WITHOUT ``hydrography: merit_hydro``.
# Job-0055 advances this to a complete step-omission guard for v0.1 pluvial.
# --------------------------------------------------------------------------- #


def _build_with_capture(
    *,
    tmp_path: Path,
    river_geometry_uri: str | None,
) -> dict[str, Any]:
    """Run ``build_sfincs_model`` against a fake hydromt-sfincs, return captured opt.

    Helper used by the migration-audit tests. Mocks the dep extraction, the
    SfincsModel constructor, and the build()/write() calls; returns the parsed
    ``opt`` dict that ``SfincsModel.build`` would receive.
    """
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

        def build(self, opt: Any) -> None:  # noqa: D401
            captured["opt"] = opt

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
        patch.dict("sys.modules", {"hydromt_sfincs": fake_module}, clear=False),
        patch(
            "grace2_agent.workflows.sfincs_builder._extract_unique_nlcd_classes",
            return_value={11, 41},
        ),
    ):
        build_sfincs_model(
            dem_uri="gs://test/dem.tif",
            landcover_uri="gs://test/landcover.tif",
            river_geometry_uri=river_geometry_uri,
            forcing=forcing,
            bbox=(-81.92, 26.55, -81.80, 26.68),
            options=BuildOptions(grid_resolution_m=30.0, simulation_hours=24.0),
            nlcd_vintage_year=2021,
            manning_mapping_csv=mapping_path,
        )
    return captured


def test_build_sfincs_model_river_inflow_not_emitted_in_pluvial_synthetic(
    tmp_path: Path,
) -> None:
    """v0.1 scope guard: ``setup_river_inflow`` MUST NOT appear in pluvial_synthetic YAML.

    Failure modes this guards against:
      * Re-introducing the ``setup_river_inflow`` block for v0.1 — the river-
        inflow path triggers hydromt-sfincs 1.2.2's ``set_forcing_1d``
        (sfincs.py:1858) which calls ``pd.RangeIndex.is_integer()``, removed
        in pandas ≥ 2.0 (we run 3.0.3). This upstream bug blocks the chain
        from reaching solver dispatch (job-0054 honest outcome disclosure).
      * Scope creep: the v0.1 M5 demo is pluvial-only (Atlas 14 design storm);
        river inflow is M5+ / sprint-9+ scope (real ATCF + storm surge).

    The ``river_geometry_uri`` is still passed to ``build_sfincs_model`` (the
    FGB is fetched and cached for future use); only the YAML step is omitted.

    Historical context: job-0054 (OQ-53) fixed ``setup_river_inflow`` to omit
    the ``hydrography: merit_hydro`` kwarg (CONUS bboxes raised
    ``NoDataException`` against the Italy-only artifact_data tile). Job-0055
    (OQ-54 routing recommendation b) completes the v0.1 remediation by
    dropping the entire block.
    """
    # Case 1: river_geometry_uri supplied (the FGB is available) — step still omitted.
    captured_with_river = _build_with_capture(
        tmp_path=tmp_path,
        river_geometry_uri="gs://test/river.fgb",
    )
    opt_with = captured_with_river.get("opt")
    assert isinstance(opt_with, dict)
    assert "setup_river_inflow" not in opt_with, (
        "job-0055 v0.1 scope violation: setup_river_inflow was re-introduced "
        "into the pluvial_synthetic YAML. This step triggers hydromt-sfincs "
        "1.2.2's set_forcing_1d which calls pd.RangeIndex.is_integer() — "
        "removed in pandas ≥ 2.0 (we run 3.0.3). The v0.1 M5 demo is "
        "pluvial-only; river inflow is M5+ scope. "
        f"Opt keys found: {list(opt_with)}"
    )

    # Case 2: river_geometry_uri=None — step also omitted (same code path).
    captured_no_river = _build_with_capture(
        tmp_path=tmp_path,
        river_geometry_uri=None,
    )
    opt_none = captured_no_river.get("opt")
    assert isinstance(opt_none, dict)
    assert "setup_river_inflow" not in opt_none, (
        "setup_river_inflow appeared when river_geometry_uri=None — "
        f"unexpected; opt keys: {list(opt_none)}"
    )


# --------------------------------------------------------------------------- #
# Test 16 — OQ-54 hotfix: setup_precip_forcing emits the v1.2.x-accepted
# kwarg shape. Live signature is ``setup_precip_forcing(timeseries=None,
# magnitude=None)`` — accepts EITHER a tabulated timeseries CSV path OR a
# constant rate in mm/hr. The previous YAML emitted ``precip`` +
# ``duration_hr`` (neither is a valid 1.2.x kwarg).
# --------------------------------------------------------------------------- #


def test_build_sfincs_model_precip_forcing_emits_magnitude_kwarg(
    tmp_path: Path,
) -> None:
    """``setup_precip_forcing`` kwargs must match hydromt-sfincs 1.2.x.

    Failure modes this guards against:
      * Emitting ``precip`` or ``duration_hr`` (the pre-OQ-54 shape) — 1.2.x
        raises ``TypeError: setup_precip_forcing() got an unexpected keyword
        argument 'precip'`` / ``'duration_hr'``.
      * Forgetting to convert Atlas 14's depth-over-duration to the mm/hr
        rate ``magnitude`` expects — the source builds a constant series
        at ``magnitude`` and SFINCS would receive the wrong forcing.
    """
    captured = _build_with_capture(
        tmp_path=tmp_path,
        river_geometry_uri=None,
    )

    opt = captured.get("opt")
    assert isinstance(opt, dict)
    assert "setup_precip_forcing" in opt, (
        f"setup_precip_forcing step missing from build opt; got keys {list(opt)}"
    )
    p_kwargs = opt["setup_precip_forcing"]
    assert isinstance(p_kwargs, dict)

    # The OQ-54 regression: ``precip`` + ``duration_hr`` MUST NOT appear.
    assert "precip" not in p_kwargs, (
        "OQ-54 regression: setup_precip_forcing emitted 'precip' kwarg — "
        "hydromt-sfincs 1.2.x raises TypeError. Use 'magnitude' (mm/hr)."
    )
    assert "duration_hr" not in p_kwargs, (
        "OQ-54 regression: setup_precip_forcing emitted 'duration_hr' "
        "kwarg — hydromt-sfincs 1.2.x raises TypeError."
    )

    # Only ``timeseries`` and ``magnitude`` are the v1.2.x-accepted kwargs.
    valid_keys = {"timeseries", "magnitude"}
    extra = set(p_kwargs.keys()) - valid_keys
    assert not extra, (
        f"setup_precip_forcing has unexpected kwargs {extra}; "
        f"hydromt-sfincs 1.2.x accepts only {valid_keys}."
    )

    # The conversion math: Atlas 14 (11.9 in over 24 hr) → mm/hr.
    # Expected: 11.9 * 25.4 / 24 = 12.5916666... mm/hr
    assert "magnitude" in p_kwargs, (
        f"setup_precip_forcing missing 'magnitude'; got {p_kwargs}"
    )
    expected_mm_per_hr = (11.9 * 25.4) / 24.0
    assert abs(p_kwargs["magnitude"] - expected_mm_per_hr) < 1e-6, (
        f"Atlas 14 conversion incorrect: got {p_kwargs['magnitude']}, "
        f"expected {expected_mm_per_hr} mm/hr"
    )


# --------------------------------------------------------------------------- #
# Test 17 — job-0054 comprehensive migration audit: every setup_* step our
# YAML emits has kwargs that are a subset of the live 1.2.2 signature
# parameter set. This is the ALL-STEPS regression guard against drift —
# if hydromt-sfincs adds/renames a kwarg in a future release, this test
# fires on the offending step.
# --------------------------------------------------------------------------- #


def test_build_sfincs_model_all_setup_steps_match_live_signatures(
    tmp_path: Path,
) -> None:
    """Every setup_* step's kwargs must match the live 1.2.2 SfincsModel signature.

    Iterates the parsed ``opt`` dict, looks up the matching ``SfincsModel``
    method, calls ``inspect.signature``, and asserts the emitted kwargs are
    a subset of the live parameter names. Skips ``setup_config`` (takes
    ``**cfdict``) and steps not present in the parsed opt.

    This is the comprehensive migration audit's residual guard — the
    individual ``map_fn``/``hydrography``/``magnitude`` tests cover the
    known mismatches; this catches anything we'd otherwise miss.
    """
    import inspect as _inspect

    try:
        import hydromt_sfincs as _hms  # type: ignore[import-not-found]
    except Exception:
        pytest.skip("hydromt_sfincs not installed; live-signature audit cannot run")

    captured = _build_with_capture(
        tmp_path=tmp_path,
        river_geometry_uri="gs://test/river.fgb",
    )

    opt = captured.get("opt")
    assert isinstance(opt, dict)

    # Steps whose live signature is ``**kwargs``-only (any key is accepted).
    permissive_steps = {"setup_config"}

    for step_name, step_kwargs in opt.items():
        if step_name in permissive_steps:
            continue
        method = getattr(_hms.SfincsModel, step_name, None)
        assert method is not None, (
            f"job-0054 audit: YAML emits unknown setup step {step_name!r} — "
            f"hydromt-sfincs 1.2.x SfincsModel has no method by that name."
        )
        live_sig = _inspect.signature(method)
        # Strip ``self`` and any ``**kwargs`` catch-all (which would accept
        # any extra kwarg, so we don't need to enforce subset there).
        live_params = {
            name
            for name, p in live_sig.parameters.items()
            if name != "self" and p.kind is not _inspect.Parameter.VAR_KEYWORD
        }
        has_var_kw = any(
            p.kind is _inspect.Parameter.VAR_KEYWORD
            for p in live_sig.parameters.values()
        )
        if has_var_kw:
            continue  # method accepts arbitrary kwargs; nothing to enforce.
        emitted = set(step_kwargs.keys()) if isinstance(step_kwargs, dict) else set()
        extra = emitted - live_params
        assert not extra, (
            f"job-0054 audit: YAML step {step_name!r} emits kwargs {extra} "
            f"not in live 1.2.2 signature {live_params}. Update "
            f"_generate_hydromt_yaml_config to match the v1.2.x API."
        )


# --------------------------------------------------------------------------- #
# Test 18 — job-0057: build_sfincs_model emits a manifest.json that conforms
# to the worker contract (services/workers/sfincs/entrypoint.py:9-23).
#
# Schema the worker reads:
#   {
#     "inputs": [{"gs_uri": "gs://...", "dest": "<filename>"}, ...],
#     "sfincs_args": [],
#     "outputs": ["sfincs_map.nc", "*.nc", "*.tif"]
#   }
#
# The worker calls ``blob.download_as_text()`` on the manifest URI then
# ``json.loads(text)`` — so the manifest MUST be a JSON FILE, not a
# directory. This was the exact bug that caused SOLVER_FAILED in job-0056.
# --------------------------------------------------------------------------- #


def test_build_sfincs_model_emits_manifest_json_with_input_list(
    tmp_path: Path,
) -> None:
    """``build_sfincs_model`` writes a manifest.json with the worker-contract shape.

    Asserts:
    - A ``manifest.json`` is emitted alongside the deck build.
    - Its ``inputs`` list contains at least one entry for every deck file
      produced by HydroMT (mocked to produce sfincs.inp + dep.tif).
    - Each input entry has both ``gs_uri`` and ``dest`` keys.
    - ``sfincs_args`` is a list (empty for v0.1).
    - ``outputs`` contains ``"sfincs_map.nc"`` (the headline output the
      postprocessing step reads).
    - The ``gs_uri`` values start with ``gs://`` and include the deck base
      prefix; ``dest`` values are bare filenames (no path separators).
    """
    import json as _json

    mapping_path = tmp_path / "manning.csv"
    mapping_path.write_text(
        "nlcd_class,manning_n,description\n"
        "11,0.025,Open Water\n"
        "41,0.150,Deciduous Forest\n",
        encoding="utf-8",
    )

    # We'll capture what files the fake HydroMT writes into the deck dir so
    # we can assert the manifest covers them all.
    captured_manifest: dict[str, Any] = {}

    class _FakeSfincsModel:
        def __init__(self, root: str, mode: str) -> None:  # noqa: D401
            self._root = root

        def build(self, opt: Any) -> None:  # noqa: D401
            # Simulate HydroMT writing deck files into the root directory.
            deck_dir = Path(self._root)
            deck_dir.mkdir(parents=True, exist_ok=True)
            (deck_dir / "sfincs.inp").write_text("[sfincs input]\n", encoding="utf-8")
            (deck_dir / "dep.tif").write_bytes(b"FAKE_GEOTIFF")

        def write(self) -> None:  # noqa: D401
            pass  # write() is already called inside build above in our stub

    fake_module = MagicMock()
    fake_module.SfincsModel = _FakeSfincsModel

    forcing = ForcingSpec(
        forcing_type="pluvial_synthetic",
        precip_inches=11.9,
        duration_hours=24.0,
        return_period_years=100,
        provenance={"source": "noaa-atlas14"},
    )

    # Use a fixed setup URI so we can assert the gs_uri prefix in the manifest.
    fixed_manifest_uri = (
        "gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs_setup/"
        "TESTID01/manifest.json"
    )

    # Mock fsspec upload to capture the local manifest.json content instead of
    # actually uploading to GCS. We intercept the upload call for the manifest
    # and read the file content immediately (while the TemporaryDirectory is
    # still alive — the upload happens inside the ``with tempfile.TemporaryDirectory``
    # block, so the file exists at that moment).
    uploaded_files: dict[str, Any] = {}

    class _FakeFS:
        def upload(self, local_path: str, remote_uri: str, recursive: bool = False) -> None:
            if remote_uri.endswith("manifest.json"):
                # Read content while the temp dir is still alive.
                uploaded_files["manifest_content"] = _json.loads(
                    Path(local_path).read_text(encoding="utf-8")
                )
                uploaded_files["manifest_uri"] = remote_uri
            # Don't raise — let the deck upload succeed silently.

    fake_fsspec = MagicMock()
    fake_fsspec.filesystem.return_value = _FakeFS()

    with (
        patch.dict("sys.modules", {"hydromt_sfincs": fake_module, "fsspec": fake_fsspec}, clear=False),
        patch(
            "grace2_agent.workflows.sfincs_builder._extract_unique_nlcd_classes",
            return_value={11, 41},
        ),
        patch(
            "grace2_agent.workflows.sfincs_builder._default_setup_uri",
            return_value=fixed_manifest_uri,
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

    # The manifest file should have been captured by our fake fsspec.
    assert "manifest_content" in uploaded_files, (
        "build_sfincs_model did not upload a manifest.json via fsspec; "
        "the worker would hit 404 on the manifest URI."
    )
    manifest = uploaded_files["manifest_content"]

    # Shape assertions — must match the worker contract schema.
    assert isinstance(manifest, dict), "manifest.json must be a JSON object"
    assert "inputs" in manifest, "manifest missing 'inputs' key"
    assert "sfincs_args" in manifest, "manifest missing 'sfincs_args' key"
    assert "outputs" in manifest, "manifest missing 'outputs' key"

    inputs = manifest["inputs"]
    assert isinstance(inputs, list), "'inputs' must be a list"
    assert len(inputs) >= 1, (
        "manifest 'inputs' list is empty — worker would download nothing "
        "and SFINCS would fail to find sfincs.inp"
    )

    # Every input entry must have both 'gs_uri' and 'dest'.
    for entry in inputs:
        assert "gs_uri" in entry, f"input entry missing 'gs_uri': {entry}"
        assert "dest" in entry, f"input entry missing 'dest': {entry}"
        assert entry["gs_uri"].startswith("gs://"), (
            f"input gs_uri must be a gs:// URI; got {entry['gs_uri']!r}"
        )
        # dest may be a relative path (e.g. "gis/dep.tif" for subdirectory
        # files); the worker does ``scratch / item["dest"]`` which handles
        # POSIX relative paths correctly.
        assert entry["dest"]  # non-empty
        assert not entry["dest"].startswith("/"), (
            f"input dest must be relative, not absolute; got {entry['dest']!r}"
        )

    # sfincs.inp must appear in inputs (SFINCS reads it from CWD).
    dest_names = {e["dest"] for e in inputs}
    assert "sfincs.inp" in dest_names, (
        f"manifest 'inputs' does not include 'sfincs.inp'; "
        f"SFINCS requires this file in CWD. Found: {sorted(dest_names)}"
    )

    # dep.tif must also appear (the DEM the model was built with).
    assert "dep.tif" in dest_names, (
        f"manifest 'inputs' does not include 'dep.tif'; found: {sorted(dest_names)}"
    )

    # gs_uri values must include the expected deck-base/deck/ prefix.
    # fsspec.upload(deck_dir, deck_base_uri, recursive=True) uploads the
    # "deck" directory as a child of deck_base_uri, so files land at
    # deck_base_uri/deck/<relative>.
    expected_prefix = (
        "gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs_setup/TESTID01/deck/"
    )
    for entry in inputs:
        assert entry["gs_uri"].startswith(expected_prefix), (
            f"input gs_uri {entry['gs_uri']!r} does not start with the "
            f"expected deck prefix {expected_prefix!r}. The worker "
            "downloads each input by its gs_uri; a mismatched prefix means "
            "the files are not where the manifest says they are."
        )

    # sfincs_args must be a list (empty for v0.1).
    assert isinstance(manifest["sfincs_args"], list), (
        "'sfincs_args' must be a list"
    )

    # outputs must include the headline flood-depth file.
    assert "sfincs_map.nc" in manifest["outputs"], (
        f"'sfincs_map.nc' missing from outputs; "
        f"postprocess_flood looks for this file. Got: {manifest['outputs']}"
    )

    # Regression: setup_uri must be the manifest file URI (not the directory).
    assert setup.setup_uri == fixed_manifest_uri, (
        f"ModelSetup.setup_uri should be the manifest file URI "
        f"{fixed_manifest_uri!r}; got {setup.setup_uri!r}. "
        "The worker passes this to _read_manifest → blob.download_as_text(); "
        "a directory URI hits 404."
    )


# --------------------------------------------------------------------------- #
# Test 19 — job-0057: ModelSetup.setup_uri returned by build_sfincs_model
# ends with ``/manifest.json`` — confirming the agent hands the worker a
# file URI, not a trailing-slash directory URI.
#
# This is the regression guard for the exact 404 observed in job-0056:
#   "ERROR google.api_core.exceptions.NotFound: 404 GET .../sfincs_setup/
#    01KTHQP54XVAAF2NPGKTAMP4PV/: No such object"
# --------------------------------------------------------------------------- #


def test_build_sfincs_model_setup_uri_points_at_manifest_file(
    tmp_path: Path,
) -> None:
    """``ModelSetup.setup_uri`` must end with ``/manifest.json``, never with ``/``.

    The worker contract (entrypoint.py:9-23) requires ``--manifest-uri`` to be
    a single JSON file URI.  The agent passes ``ModelSetup.setup_uri`` as that
    URI.  A trailing-slash directory URI causes:

        ``404 GET .../sfincs_setup/<id>/: No such object``

    because GCS has no object with that exact key.

    This test exercises the default path (no ``output_setup_uri`` override)
    and an override path where the caller supplies a directory URI, verifying
    that the normalisation logic appends ``manifest.json`` in both cases.
    """
    mapping_path = tmp_path / "manning.csv"
    mapping_path.write_text(
        "nlcd_class,manning_n,description\n"
        "11,0.025,Open Water\n"
        "41,0.150,Deciduous Forest\n",
        encoding="utf-8",
    )

    class _FakeSfincsModel:
        def __init__(self, root: str, mode: str) -> None:  # noqa: D401
            self._root = root

        def build(self, opt: Any) -> None:  # noqa: D401
            deck_dir = Path(self._root)
            deck_dir.mkdir(parents=True, exist_ok=True)
            (deck_dir / "sfincs.inp").write_text("[sfincs input]\n", encoding="utf-8")

        def write(self) -> None:  # noqa: D401
            pass

    fake_module = MagicMock()
    fake_module.SfincsModel = _FakeSfincsModel

    forcing = ForcingSpec(
        forcing_type="pluvial_synthetic",
        precip_inches=11.9,
        duration_hours=24.0,
        return_period_years=100,
        provenance={"source": "noaa-atlas14"},
    )

    fake_fsspec = MagicMock()
    fake_fsspec.filesystem.return_value = MagicMock(
        upload=MagicMock()  # swallow uploads silently
    )

    def _run_build(options: BuildOptions) -> "ModelSetup":
        with (
            patch.dict("sys.modules", {"hydromt_sfincs": fake_module, "fsspec": fake_fsspec}, clear=False),
            patch(
                "grace2_agent.workflows.sfincs_builder._extract_unique_nlcd_classes",
                return_value={11, 41},
            ),
        ):
            return build_sfincs_model(
                dem_uri="gs://test/dem.tif",
                landcover_uri="gs://test/landcover.tif",
                river_geometry_uri=None,
                forcing=forcing,
                bbox=(-81.92, 26.55, -81.80, 26.68),
                options=options,
                nlcd_vintage_year=2021,
                manning_mapping_csv=mapping_path,
            )

    # --- Case 1: default path (no output_setup_uri override) ---
    setup_default = _run_build(BuildOptions(grid_resolution_m=30.0, simulation_hours=24.0))
    assert setup_default.setup_uri.endswith("/manifest.json"), (
        f"Default path: ModelSetup.setup_uri must end with '/manifest.json'; "
        f"got {setup_default.setup_uri!r}. A directory URI (trailing '/') "
        "causes 404 in the worker's _read_manifest call."
    )
    assert not setup_default.setup_uri.endswith("//manifest.json"), (
        "Double-slash in URI: deck_base + manifest.json produced '//' — "
        f"check the URI composition logic. Got {setup_default.setup_uri!r}"
    )

    # --- Case 2: output_setup_uri override as a directory URI (trailing /) ---
    # Callers that previously passed a directory override must still work;
    # the normalisation logic should append 'manifest.json'.
    setup_dir_override = _run_build(
        BuildOptions(
            grid_resolution_m=30.0,
            simulation_hours=24.0,
            output_setup_uri="gs://grace-2-hazard-prod-cache/cache/custom-run/test-setup/",
        )
    )
    assert setup_dir_override.setup_uri.endswith("/manifest.json"), (
        f"Directory-override path: setup_uri must end with '/manifest.json'; "
        f"got {setup_dir_override.setup_uri!r}."
    )
    assert setup_dir_override.setup_uri == (
        "gs://grace-2-hazard-prod-cache/cache/custom-run/test-setup/manifest.json"
    ), (
        f"Directory override did not normalise correctly; "
        f"got {setup_dir_override.setup_uri!r}"
    )

    # --- Case 3: output_setup_uri already ends with /manifest.json ---
    setup_manifest_override = _run_build(
        BuildOptions(
            grid_resolution_m=30.0,
            simulation_hours=24.0,
            output_setup_uri=(
                "gs://grace-2-hazard-prod-cache/cache/custom-run/test-setup/manifest.json"
            ),
        )
    )
    assert setup_manifest_override.setup_uri == (
        "gs://grace-2-hazard-prod-cache/cache/custom-run/test-setup/manifest.json"
    ), (
        f"Manifest override was mutated unexpectedly; "
        f"got {setup_manifest_override.setup_uri!r}"
    )
