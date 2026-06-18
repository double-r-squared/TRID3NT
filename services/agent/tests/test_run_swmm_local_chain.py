"""End-to-end LOCAL-lane proof for the PySWMM quasi-2D urban-flood engine
(sprint-16 P4, Path A).

Exercises the full LOCAL chain on a SMALL SYNTHETIC AOI (a tilted-plane DEM with
a central pit + two building footprints + a tagged RED-wall / GREEN-flap-gate
barrier FeatureCollection + a synthetic nested hyetograph via the design-storm
depth) WITHOUT any live network fetch:

    build_and_stage_swmm_deck (build_swmm_mesh, P2)
      -> run_swmm_local (pyswmm IN-PROCESS, the dev primary path, P4)
      -> postprocess_swmm (rasterize node depths -> peak + frames, P3)
      -> model_urban_flood_swmm composer (peak returned + frames emitted
         out-of-band via a fake emitter)
      -> run_swmm_urban_flood tool (SWMMRunArgs coercion + typed-error surface)

This is the P4 acceptance: a REAL solved ``.out`` produces a peak primary
``SWMMDepthLayerURI`` + a contiguous "Flood depth step N" animation frame group,
end to end. Solver registration + the SWMM ``LocalSolverSpec`` are pinned too.

pyswmm + swmm-api + rasterio are required; the heavy E2E tests skip if absent.
The lightweight registration tests need none of them.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

# --- Lightweight registration tests (no SWMM dep) ------------------------- #
from grace2_agent.workflows.run_swmm import (
    SWMM_SOLVER_NAME,
    is_local_mode,
    register_swmm_solver,
    swmm_local_spec,
)

_WEB_STEP_TOKEN_RE = re.compile(r"\b(?:step|frame|idx|index)\s*\+?(\d{1,4})\b", re.I)


def test_swmm_registered_in_solver_workflow_registry():
    """'swmm' is a first-class entry in SOLVER_WORKFLOW_REGISTRY (mirrors sfincs)."""
    from grace2_agent.tools.solver import (
        LOCAL_EXEC_WORKFLOW_NAME,
        SOLVER_WORKFLOW_REGISTRY,
    )

    register_swmm_solver()  # idempotent
    assert SWMM_SOLVER_NAME in SOLVER_WORKFLOW_REGISTRY
    assert SOLVER_WORKFLOW_REGISTRY[SWMM_SOLVER_NAME] == LOCAL_EXEC_WORKFLOW_NAME


def test_swmm_local_spec_is_exec_kind():
    """The SWMM LocalSolverSpec mirrors the MODFLOW exec-kind local spec."""
    spec = swmm_local_spec()
    assert spec.solver == "swmm"
    assert spec.exec_kind == "exec"  # pyswmm is a pip dep, no public image
    assert spec.args_key == "swmm_args"
    assert spec.stdout_uri_field == "swmm_stdout_uri"
    assert spec.stderr_uri_field == "swmm_stderr_uri"
    assert spec.classify_exit is not None  # the continuity (mass-balance) guard


def test_run_swmm_urban_flood_registered_and_typed_error():
    """The LLM-facing tool is registered + returns a typed error dict (never
    raises) on a missing/invalid bbox."""
    import asyncio

    import grace2_agent.tools as T
    from grace2_agent.tools.run_swmm_tool import run_swmm_urban_flood

    assert "run_swmm_urban_flood" in T.TOOL_REGISTRY

    # No bbox -> typed error dict, not a raise.
    out = asyncio.run(run_swmm_urban_flood(bbox=None))
    assert out["status"] == "error"
    assert out["error_code"] == "SWMM_PARAMS_INCOMPLETE"

    # A non-bbox string -> typed invalid-params error.
    out2 = asyncio.run(run_swmm_urban_flood(bbox="not-a-bbox"))
    assert out2["status"] == "error"
    assert out2["error_code"] == "SWMM_PARAMS_INVALID"


def test_is_local_mode_default_true():
    """The urban engine runs in-process by default (pyswmm is headless)."""
    assert is_local_mode() is True


# --- Heavy end-to-end chain (needs pyswmm + swmm-api + rasterio) ---------- #
swmm_api = pytest.importorskip("swmm_api")
pyswmm = pytest.importorskip("pyswmm")
rasterio = pytest.importorskip("rasterio")

from grace2_contracts.swmm_contracts import SWMMDepthLayerURI, SWMMRunArgs  # noqa: E402
from grace2_agent.workflows.model_urban_flood_swmm import (  # noqa: E402
    model_urban_flood_swmm,
)
from grace2_agent.workflows.run_swmm import (  # noqa: E402
    build_and_stage_swmm_deck,
    run_swmm_local,
)

_N = 16  # small grid -> fast solve
_CELL = 10.0
_EPSG = 32616  # UTM 16N (valid projected metres)
_OX, _OY = 500000.0, 4000000.0


def _write_dem_geotiff(path: Path) -> None:
    """Tilted plane draining to the low corner + a central pit (P0-spike shape)."""
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    ii, jj = np.meshgrid(np.arange(_N), np.arange(_N), indexing="ij")
    plane = 30.0 - 0.02 * _CELL * (ii + jj)
    ci = cj = (_N - 1) / 2.0
    pit = 2.0 * np.exp(-((ii - ci) ** 2 + (jj - cj) ** 2) / (2.0 * 3.0**2))
    dem = (plane - pit).astype("float32")
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        "height": _N,
        "width": _N,
        "crs": CRS.from_epsg(_EPSG),
        "transform": from_origin(_OX, _OY, _CELL, _CELL),
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(dem, 1)


def _cell_lonlat(i: int, j: int) -> tuple[float, float]:
    """Centroid (lon, lat) of grid cell (i, j) in EPSG:4326."""
    from rasterio.transform import from_origin, xy
    from rasterio.warp import transform as warp_transform

    t = from_origin(_OX, _OY, _CELL, _CELL)
    x, y = xy(t, i, j)
    lons, lats = warp_transform(f"EPSG:{_EPSG}", "EPSG:4326", [x], [y])
    return lons[0], lats[0]


def _footprint_over_cell(i: int, j: int) -> dict:
    """A small WGS84 building polygon centered on cell (i, j)."""
    lon, lat = _cell_lonlat(i, j)
    d = 0.00004
    ring = [
        [lon - d, lat - d], [lon + d, lat - d],
        [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d],
    ]
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _tagged_barriers() -> dict:
    """A RED wall + GREEN flap-gate barrier FeatureCollection along cell edges."""
    # Wall along the edge between cells (5,5)-(5,6); flap gate (10,5)-(10,6).
    def _edge_line(a: tuple[int, int], b: tuple[int, int]) -> list[list[float]]:
        la = _cell_lonlat(*a)
        lb = _cell_lonlat(*b)
        return [list(la), list(lb)]

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"barrier_type": "wall"},
                "geometry": {"type": "LineString", "coordinates": _edge_line((5, 5), (5, 6))},
            },
            {
                "type": "Feature",
                "properties": {"barrier_type": "flap_gate"},
                "geometry": {"type": "LineString", "coordinates": _edge_line((10, 5), (10, 6))},
            },
        ],
    }


@pytest.fixture()
def synthetic_inputs(tmp_path: Path):
    dem_path = tmp_path / "dem.tif"
    _write_dem_geotiff(dem_path)
    footprints = {
        "type": "FeatureCollection",
        "features": [_footprint_over_cell(7, 7), _footprint_over_cell(8, 8)],
    }
    barriers = _tagged_barriers()
    return str(dem_path), footprints, barriers


def _fake_upload(local_cog, run_id, runs_bucket=None, *, dest_filename="swmm_depth_peak.tif"):  # noqa: ANN001
    return f"gs://test-runs/{run_id}/{dest_filename}"


class _FakeEmitter:
    """Captures the out-of-band frame emissions + the zoom-to map command."""

    def __init__(self) -> None:
        self.loaded_layers: list = []
        self.map_commands: list = []

    async def add_loaded_layer(self, layer) -> None:  # noqa: ANN001
        self.loaded_layers.append(layer)

    async def emit_map_command(self, kind, payload) -> None:  # noqa: ANN001
        self.map_commands.append((kind, payload))


def test_build_and_run_local_lane_produces_solved_out(synthetic_inputs):
    """build_and_stage_swmm_deck -> run_swmm_local solves a REAL deck headless
    in-process and the .out exists with the barriers + buildings applied."""
    dem_path, footprints, barriers = synthetic_inputs
    run_args = SWMMRunArgs(
        bbox=(-88.0, 36.0, -87.99, 36.01),  # bbox is provenance-only here
        total_rain_depth_mm=120.0,
        storm_duration_hr=1.0,  # short storm keeps the solve fast
        rain_interval_min=5,
        target_resolution_m=10.0,
        building_representation="drop",
        barriers=barriers,
        mass_balance_tolerance_pct=100.0,  # tiny deck: only need a real .out
    )
    staging = build_and_stage_swmm_deck(
        run_args, dem_path=dem_path, building_footprints=footprints
    )
    # buildings dropped + at least one wall + at least one flap gate snapped.
    assert staging.build.n_buildings_dropped >= 1
    assert staging.build.n_walls >= 1
    assert staging.build.n_flap_gates >= 1

    run = run_swmm_local(staging)
    assert Path(run.out_path).exists()
    assert run.n_steps > 1  # a multi-step solve (frames can form)
    assert run.continuity_error_pct is not None


def test_full_local_chain_emits_peak_plus_frames(synthetic_inputs, monkeypatch):
    """The composer runs the FULL local chain end to end (synthetic DEM ->
    deck -> in-process solve -> postprocess), returns the PEAK primary
    SWMMDepthLayerURI, and emits a contiguous 'Flood depth step N' frame group
    out-of-band via the emitter. Upload is stubbed; no live fetch."""
    import asyncio

    dem_path, footprints, barriers = synthetic_inputs

    # Stub the COG upload (no object store in-test).
    monkeypatch.setattr(
        "grace2_agent.workflows.postprocess_swmm._upload_cog_to_runs_bucket",
        _fake_upload,
    )
    # Bind a fake emitter so the out-of-band frame emission is captured (mirrors
    # the WS dispatch ContextVar binding).
    from grace2_agent import pipeline_emitter as pe

    fake = _FakeEmitter()
    token = pe._CURRENT_EMITTER.set(fake)
    try:
        run_args = SWMMRunArgs(
            bbox=(-88.0, 36.0, -87.99, 36.01),
            total_rain_depth_mm=120.0,
            storm_duration_hr=1.0,
            rain_interval_min=5,
            target_resolution_m=10.0,
            building_representation="drop",
            barriers=barriers,
            mass_balance_tolerance_pct=100.0,
        )
        peak = asyncio.run(
            model_urban_flood_swmm(
                run_args,
                dem_path=dem_path,
                building_footprints=footprints,
                run_id="run-urban",
                cleanup_deck=True,
            )
        )
    finally:
        pe._CURRENT_EMITTER.reset(token)

    # --- peak primary: the run_modflow-style single returned LayerURI ---------
    assert isinstance(peak, SWMMDepthLayerURI)
    assert peak.role == "primary"
    assert peak.name == "Peak flood depth"
    assert peak.layer_id == "swmm-depth-peak-run-urban"
    assert peak.style_preset == "continuous_flood_depth"
    assert peak.max_depth_m >= 0.0
    assert peak.flooded_area_km2 >= 0.0
    assert peak.n_buildings_affected >= 0
    # barriers echoed back for rendering (RED walls / GREEN flap gates).
    assert peak.barriers is not None
    assert peak.barriers["type"] == "FeatureCollection"

    # --- frames emitted OUT-OF-BAND as a contiguous "Flood depth step N" group ---
    frames = fake.loaded_layers
    assert len(frames) >= 2, f"expected a multi-frame animation group; got {len(frames)}"
    assert all(isinstance(f, SWMMDepthLayerURI) for f in frames)
    assert all(f.role == "context" for f in frames)
    names = [f.name for f in frames]
    assert names == [f"Flood depth step {i}" for i in range(1, len(frames) + 1)]
    for name in names:
        assert _WEB_STEP_TOKEN_RE.search(name) is not None, name
    # DISTINCT uris (distinct runs-bucket keys -> no dedup collapse).
    uris = [f.uri for f in frames]
    assert len(set(uris)) == len(uris)
    assert peak.uri not in uris
    # the peak is NOT in the emitted frame set (it is the returned layer).
    assert all(f.layer_id != peak.layer_id for f in frames)

    # --- zoom-on-area-first emitted before the solve ---
    assert any(k == "zoom-to" for k, _ in fake.map_commands)


def test_tool_wrapper_drives_full_chain(synthetic_inputs, monkeypatch):
    """The LLM-facing run_swmm_urban_flood tool drives the same chain and returns
    a SWMMDepthLayerURI (the add_loaded_layer gate target) — DEM fetch stubbed to
    the synthetic file so no network is touched."""
    import asyncio

    dem_path, footprints, barriers = synthetic_inputs

    monkeypatch.setattr(
        "grace2_agent.workflows.postprocess_swmm._upload_cog_to_runs_bucket",
        _fake_upload,
    )
    # Stub the composer's DEM + buildings acquisition to the synthetic inputs so
    # the tool path needs no live fetch.
    monkeypatch.setattr(
        "grace2_agent.workflows.model_urban_flood_swmm._fetch_dem_for_urban",
        lambda bbox: (dem_path, "synthetic"),
    )
    monkeypatch.setattr(
        "grace2_agent.workflows.model_urban_flood_swmm._fetch_buildings_for_urban",
        lambda bbox: footprints,
    )
    monkeypatch.setattr(
        "grace2_agent.workflows.model_urban_flood_swmm._atlas14_total_depth_mm",
        lambda bbox, rp, dur: 120.0,
    )

    from grace2_agent.tools.run_swmm_tool import run_swmm_urban_flood

    out = asyncio.run(
        run_swmm_urban_flood(
            bbox=[-88.0, 36.0, -87.99, 36.01],
            storm_duration_hr=1.0,
            rain_interval_min=5,
            target_resolution_m=10.0,
            building_representation="drop",
            barriers=barriers,
            mass_balance_tolerance_pct=100.0,
        )
    )
    assert isinstance(out, SWMMDepthLayerURI), out
    assert out.role == "primary"
    assert out.layer_id.startswith("swmm-depth-peak-")
    assert out.max_depth_m >= 0.0
