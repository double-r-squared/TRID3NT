"""Unit tests for the COASTAL surge-with-waves AUTO-WIRE in model_flood_scenario.

The fix: a coastal run (``is_coastal`` True) with NO explicit ``surge_forcing``
used to silently degrade to a pure-RAINFALL deck  -  ``coastal=True`` only swapped
``fetch_dem`` -> ``fetch_topobathy`` (a deeper bed) and added ZERO sea water, and
waves were gated on ``quadtree`` which defaulted OFF. Now:

1. A coastal call with ``surge_forcing=None`` AUTO-WIRES a time-varying sea-surge
   water-level boundary, so the resolved ``ForcingSpec.waterlevel`` is NON-None
   (the deck emits a ``bzs`` boundary -> water rises from the sea and marches
   inland across the frames).
2. ``quadtree`` is FORCED True for any coastal run, firing the cht_sfincs
   quadtree + SnapWave deck (so ``run_sfincs_quadtree`` is submitted, not the
   regular ``run_solver``) -> the wave-height field exists.
3. A NON-coastal (inland / pluvial) call is UNCHANGED: ``ForcingSpec.waterlevel``
   stays None (precip-only), the auto-wire helper is NEVER called, and
   ``quadtree`` stays as passed (the regular-grid ``run_solver`` path)  -  the v0.1
   regression contract.

The parametric LAST-RESORT surge path is exercised deterministically by mocking
both fetchers (CO-OPS + GTSM) to raise, so the test needs NO network / CDS key.
The surge scaling with return period is unit-tested directly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from grace2_agent.tools.fetch_topobathy import TopobathyResult
from grace2_agent.workflows import model_flood_scenario as mfs
from grace2_agent.workflows.model_flood_scenario import (
    _autowire_coastal_surge_forcing,
    _parametric_surge_peak_m,
    _synthesize_parametric_surge_forcing,
    model_flood_scenario,
)
from grace2_contracts import new_ulid
from grace2_contracts.execution import (
    ExecutionHandle,
    LayerURI,
    ModelSetup,
    RunResult,
)

# Coastal AOI  -  Florida panhandle / Mexico Beach (the SFINCS North Star demo).
_COASTAL_BBOX = (-85.75, 29.55, -85.25, 30.20)
# Inland AOI  -  Idaho (no coast, pure pluvial).
_INLAND_BBOX = (-116.30, 43.55, -116.10, 43.70)


# --------------------------------------------------------------------------- #
# Mock builders
# --------------------------------------------------------------------------- #


def _topobathy_result() -> TopobathyResult:
    return TopobathyResult(
        layer_id="topobathy-test",
        name="Merged topo-bathymetry (3DEP + CUDEM)",
        layer_type="raster",
        uri="s3://test-cache/cache/static-30d/topobathy/coastal-test.tif",
        style_preset="continuous_dem",
        role="input",
        units="meters",
        bathymetry_present=True,
        cudem_tile_count=3,
        fallback_warning=None,
    )


def _mock_layer_uri(prefix: str) -> LayerURI:
    return LayerURI(
        layer_id=f"{prefix}-test",
        name=f"{prefix} test layer",
        layer_type="raster",
        uri=f"s3://test-cache/cache/static-30d/{prefix}/test.tif",
        style_preset="continuous_dem",
        role="input",
        units="meters",
    )


def _landcover_result() -> dict:
    return {
        "layer": _mock_layer_uri("landcover"),
        "nlcd_vintage_year": 2021,
        "dataset": "nlcd_2021",
        "source": "mrlc-wms",
    }


def _precip_result() -> dict:
    return {
        "precip_inches": 12.1,
        "units": "inches",
        "location": [29.95, -85.41],
        "return_period_years": 100,
        "duration_hours": 24.0,
        "vintage_volume": "NOAA Atlas 14 Volume 9",
        "project_area": "Southeastern States",
        "source": "noaa-atlas14-pfds",
    }


def _model_setup(bbox) -> ModelSetup:
    return ModelSetup(
        setup_id=new_ulid(),
        solver="sfincs",
        setup_uri="s3://test-cache/cache/static-30d/sfincs_setup/test/manifest.json",
        grid_resolution_m=30.0,
        bbox=bbox,
        parameters={"nlcd_vintage_year": 2021},
        created_at=datetime.now(timezone.utc),
    )


def _run_result_ok(run_id: str) -> RunResult:
    return RunResult(
        run_id=run_id,
        handle_id=new_ulid(),
        status="complete",
        output_uri=f"s3://grace-2-hazard-prod-runs/{run_id}/",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_seconds=120.0,
    )


def _make_handle(run_id: str) -> ExecutionHandle:
    return ExecutionHandle(
        handle_id=new_ulid(),
        run_id=run_id,
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


def _depth_layers(run_id: str) -> list[LayerURI]:
    return [
        LayerURI(
            layer_id=f"flood-depth-peak-{run_id}",
            name="Peak flood depth",
            layer_type="raster",
            uri=f"s3://grace-2-hazard-prod-runs/{run_id}/flood_depth_peak.tif",
            style_preset="continuous_flood_depth",
            role="primary",
            units="meters",
        ),
    ]


_DEPTH_METRICS = {
    "max_depth_m": 1.8,
    "mean_depth_m": 0.4,
    "p95_depth_m": 1.2,
    "flooded_cell_count": 8_000,
    "crs": "EPSG:32616",
    "units": "meters",
}


class _FakeEmitter:
    """No-op emitter so the workflow's substep/emit seams are inert in test."""

    def __init__(self) -> None:
        self.loaded: list[LayerURI] = []

    async def add_loaded_layer(self, layer) -> None:  # noqa: ANN001
        self.loaded.append(layer)

    async def update_current_progress(self, *_a, **_k) -> None:  # noqa: ANN002
        return None

    async def emit_solve_progress(self, *_a, **_k) -> None:  # noqa: ANN002
        return None

    async def emit_map_command(self, *_a, **_k) -> None:  # noqa: ANN002
        return None

    def begin_substeps(self, *_a, **_k) -> None:  # noqa: ANN002
        return None

    @asynccontextmanager
    async def substep(self, *_a, **_k):  # noqa: ANN002
        yield None


def _common_patches(*, bbox, run_id, emitter, forcing_capture):
    """Patch the full fetch+build+solve chain. Captures the ForcingSpec handed to
    ``build_sfincs_model`` so the test can assert ``.waterlevel`` directly.

    Both surge fetchers (CO-OPS + GTSM) raise so the auto-wire deterministically
    exercises the PARAMETRIC last-resort path (no network, no CDS key).
    """

    def _capture_build(**kw):  # noqa: ANN003
        forcing_capture["forcing"] = kw.get("forcing")
        return _model_setup(bbox)

    async def _run_quadtree(*_a, **_k):  # noqa: ANN002
        return _run_result_ok(run_id)

    async def _run_solver(*_a, **_k):  # noqa: ANN002
        return _make_handle(run_id)

    async def _wait(*_a, **_k):  # noqa: ANN002
        return _run_result_ok(run_id)

    return (
        patch.object(mfs, "fetch_topobathy", return_value=_topobathy_result()),
        patch.object(mfs, "fetch_dem", return_value=_mock_layer_uri("dem")),
        patch.object(mfs, "fetch_landcover", return_value=_landcover_result()),
        patch.object(
            mfs, "fetch_river_geometry", return_value=_mock_layer_uri("rivers")
        ),
        patch.object(
            mfs, "lookup_precip_return_period", return_value=_precip_result()
        ),
        # Both surge fetchers raise -> parametric last-resort fires.
        patch(
            "grace2_agent.tools.fetch_noaa_coops_tides.fetch_noaa_coops_tides",
            side_effect=RuntimeError("no CO-OPS station (test)"),
        ),
        patch(
            "grace2_agent.tools.fetch_gtsm_tide_surge.fetch_gtsm_tide_surge",
            side_effect=RuntimeError("no CDS key (test)"),
        ),
        patch.object(mfs, "build_sfincs_model", side_effect=_capture_build),
        patch.object(mfs, "_resolve_building_obstacle_uri", return_value=None),
        patch.object(mfs, "_resolve_quadtree_rivers_uri", return_value=None),
        patch.object(
            mfs,
            "_compose_and_upload_deckbuild_spec",
            return_value="s3://test-cache/cache/static-30d/sfincs_deck/x/spec.json",
        ),
        patch.object(mfs, "make_sfincs_mesh_layer_uri", return_value=None),
        patch.object(
            mfs, "postprocess_flood", return_value=(_depth_layers(run_id), _DEPTH_METRICS)
        ),
        patch.object(
            mfs, "postprocess_waves", MagicMock(return_value=([], _DEPTH_METRICS))
        ),
        patch.object(
            mfs,
            "publish_layer",
            side_effect=lambda **kw: f"https://cf.example.net/tiles/{kw['layer_id']}",
        ),
        patch.object(mfs, "current_emitter", return_value=emitter),
    )


# --------------------------------------------------------------------------- #
# 1. parametric surge scaling  -  pure unit test (no I/O)
# --------------------------------------------------------------------------- #


def test_parametric_surge_peak_scales_monotone_with_return_period() -> None:
    p2 = _parametric_surge_peak_m(2)
    p10 = _parametric_surge_peak_m(10)
    p100 = _parametric_surge_peak_m(100)
    p500 = _parametric_surge_peak_m(500)
    # Monotone increasing with ARI.
    assert p2 < p10 < p100 < p500
    # The 100-yr anchor is a real, visually-meaningful multi-metre surge.
    assert p100 >= 3.0
    # Clamped to a sane window (never negative / runaway).
    assert _parametric_surge_peak_m(1) >= 0.6
    assert _parametric_surge_peak_m(1_000_000) <= 7.5
    # None / 0 defaults to the 100-yr anchor (no crash).
    assert _parametric_surge_peak_m(None) == pytest.approx(p100)


def test_synthesize_parametric_surge_yields_materialised_waterlevel() -> None:
    out = _synthesize_parametric_surge_forcing(
        _COASTAL_BBOX, duration_hr=24, return_period_yr=100
    )
    # The materialised dict carries timeseries_uri -> a NON-None WaterlevelForcing.
    assert out.get("timeseries_uri")
    assert out.get("locations_uri")
    wl, _dq, _wind, _press = mfs._build_surge_forcing_members({"waterlevel": out})
    assert wl is not None
    assert wl.timeseries_uri == out["timeseries_uri"]


def test_autowire_falls_back_to_parametric_when_fetchers_fail() -> None:
    with patch(
        "grace2_agent.tools.fetch_noaa_coops_tides.fetch_noaa_coops_tides",
        side_effect=RuntimeError("no station"),
    ), patch(
        "grace2_agent.tools.fetch_gtsm_tide_surge.fetch_gtsm_tide_surge",
        side_effect=RuntimeError("no key"),
    ):
        sf = _autowire_coastal_surge_forcing(
            _COASTAL_BBOX, duration_hr=24, return_period_yr=100
        )
    assert isinstance(sf, dict)
    wl, *_ = mfs._build_surge_forcing_members(sf)
    assert wl is not None
    assert wl.timeseries_uri  # the parametric bzs CSV


# --------------------------------------------------------------------------- #
# 2. coastal call -> auto-wired non-None waterlevel + quadtree forced True
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_coastal_call_autowires_waterlevel_and_forces_quadtree() -> None:
    run_id = new_ulid()
    emitter = _FakeEmitter()
    forcing_capture: dict = {}
    quadtree_mock = MagicMock(return_value=_run_result_ok(run_id))

    async def _run_quadtree(*_a, **_k):  # noqa: ANN002
        quadtree_mock()
        return _run_result_ok(run_id)

    def _run_solver(*_a, **_k):  # noqa: ANN002  (sync dispatch)
        raise AssertionError("regular run_solver must NOT run on the coastal path")

    patches = _common_patches(
        bbox=_COASTAL_BBOX, run_id=run_id, emitter=emitter, forcing_capture=forcing_capture
    )
    extra = (
        patch.object(mfs, "run_sfincs_quadtree", side_effect=_run_quadtree),
        patch.object(mfs, "run_solver", side_effect=_run_solver),
    )
    for p in patches + extra:
        p.start()
    try:
        await model_flood_scenario(
            bbox=_COASTAL_BBOX,
            coastal=True,
            quadtree=False,  # NOT passed by the LLM  -  coastal must force it on
            surge_forcing=None,  # NOT supplied  -  must be auto-wired
            return_period_yr=100,
            duration_hr=24,
        )
    finally:
        for p in reversed(patches + extra):
            p.stop()

    # The auto-wired surge produced a NON-None waterlevel boundary on the
    # ForcingSpec handed to build_sfincs_model.
    spec = forcing_capture.get("forcing")
    assert spec is not None, "build_sfincs_model never received a ForcingSpec"
    assert spec.waterlevel is not None, (
        "coastal run must auto-wire a NON-None waterlevel surge boundary"
    )
    assert spec.waterlevel.timeseries_uri
    # quadtree was forced True for the coastal AOI -> the combined SnapWave job ran.
    assert quadtree_mock.called, "coastal run must force the quadtree+SnapWave deck"


# --------------------------------------------------------------------------- #
# 3. inland / pluvial call UNCHANGED (regression contract)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_inland_call_unchanged_no_surge_no_quadtree() -> None:
    run_id = new_ulid()
    emitter = _FakeEmitter()
    forcing_capture: dict = {}

    autowire_mock = MagicMock(wraps=_autowire_coastal_surge_forcing)

    # run_solver is a SYNC dispatch (returns an ExecutionHandle, not awaited).
    def _run_solver(*_a, **_k):  # noqa: ANN002
        return _make_handle(run_id)

    async def _wait(*_a, **_k):  # noqa: ANN002
        return _run_result_ok(run_id)

    async def _run_quadtree(*_a, **_k):  # noqa: ANN002
        raise AssertionError("quadtree must NOT run on the inland/pluvial path")

    patches = _common_patches(
        bbox=_INLAND_BBOX, run_id=run_id, emitter=emitter, forcing_capture=forcing_capture
    )
    extra = (
        patch.object(mfs, "run_solver", side_effect=_run_solver),
        patch.object(mfs, "wait_for_completion", side_effect=_wait),
        patch.object(mfs, "run_sfincs_quadtree", side_effect=_run_quadtree),
        patch.object(mfs, "_autowire_coastal_surge_forcing", autowire_mock),
    )
    for p in patches + extra:
        p.start()
    try:
        await model_flood_scenario(
            bbox=_INLAND_BBOX,
            coastal=False,
            quadtree=False,
            surge_forcing=None,
            return_period_yr=100,
            duration_hr=24,
        )
    finally:
        for p in reversed(patches + extra):
            p.stop()

    # The auto-wire helper was NEVER called on the inland path.
    assert not autowire_mock.called, "auto-wire must NOT fire for a non-coastal AOI"
    # The ForcingSpec carries NO surge boundary (pure pluvial  -  byte-identical v0.1).
    spec = forcing_capture.get("forcing")
    assert spec is not None
    assert spec.waterlevel is None, "inland/pluvial run must NOT carry a surge boundary"
    assert spec.discharge is None
    # forcing_type stays the design-storm pluvial path.
    assert spec.forcing_type == "pluvial_synthetic"
    assert spec.precip_inches == pytest.approx(12.1)
