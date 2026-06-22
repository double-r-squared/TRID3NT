"""End-to-end MODULE tests for the SWAN (Simulating WAves Nearshore) spectral
wave engine (Phase 1), exercised in ISOLATION with run_solver / boto3 / network
MOCKED.

SWAN is NEW + BATCH-only + the ADDITIVE comparison engine (standalone wave field
vs SFINCS+SnapWave). These tests pin the agent-side MODULES the lane owns:

  1. **Contract round-trip + mode alias normalization** -- ``SwanRunArgs`` /
     ``WaveFieldLayerURI`` (no SWAN dep).
  2. **build_spec assembly** -- ``build_swan_build_spec`` maps run args onto the
     worker's deck_builder field dict (incl. the synthesized demo boundary).
  3. **Solver registration** -- ``'swan'`` is a first-class entry in
     ``SOLVER_WORKFLOW_REGISTRY`` and the bridge tool is registered with typed
     errors.
  4. **postprocess on a SYNTHETIC swan_out.mat** -- a hand-built SWAN Matlab BLOCK
     output reads + rasterizes + writes a VALID EPSG:4326 Hs COG (upload stubbed),
     yielding the EXACT postprocess_waves (layers, metrics) shape, AND the honesty
     floor (all-calm -> SWAN_OUTPUT_EMPTY).
  5. **Composer arg-assembly with run_solver MOCKED** -- the composer stages a
     manifest, dispatches via a mocked run_solver/wait_for_completion, downloads a
     mocked Batch output, postprocesses, and returns the peak WaveFieldLayerURI +
     emits frames out-of-band -- all without touching AWS or SWAN.

scipy + rasterio + numpy are required for (4)+(5); they are in the agent venv.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from grace2_contracts.swan_contracts import (
    SWAN_WAVE_HEIGHT_STYLE_PRESET,
    SwanRunArgs,
    SwanWaveBoundary,
    WaveFieldLayerURI,
)

_AOI = (-85.75, 29.55, -85.25, 30.20)

# The web parseFrameToken regex -- the frame NAMES must match it or the sequential
# group never forms (same guard as test_run_geoclaw_chain).
_WEB_STEP_TOKEN_RE = re.compile(r"\b(?:step|frame|idx|index)\s*\+?(\d{1,4})\b", re.I)


# ===========================================================================
# (1) Contract round-trip + mode alias normalization.
# ===========================================================================
def test_run_args_round_trip_and_mode_aliases():
    a = SwanRunArgs(bbox=_AOI, mode="peak")
    assert a.mode == "stationary"  # alias
    b = SwanRunArgs(bbox=_AOI, mode="transient")
    assert b.mode == "nonstationary"  # alias
    c = SwanRunArgs(bbox=_AOI, mode="time_series")
    assert c.mode == "nonstationary"  # alias
    # round-trip.
    a2 = SwanRunArgs(**a.model_dump())
    assert a2 == a


def test_run_args_rejects_bad_mode_and_freq_band():
    with pytest.raises(Exception):
        SwanRunArgs(bbox=_AOI, mode="not_a_mode")
    with pytest.raises(Exception):
        SwanRunArgs(bbox=_AOI, freq_low_hz=1.0, freq_high_hz=0.5)
    with pytest.raises(Exception):
        SwanRunArgs(bbox=_AOI, n_dir=4)  # < 12


def test_wave_field_layer_uri_round_trip():
    lyr = WaveFieldLayerURI(
        layer_id="swan-wave-height-peak-x",
        name="Peak wave height",
        layer_type="raster",
        uri="s3://b/k.tif",
        style_preset=SWAN_WAVE_HEIGHT_STYLE_PRESET,
        role="primary",
        units="meters",
        bbox=_AOI,
        max_hs_m=3.4,
        mean_tp_s=8.7,
        mean_dir_deg=182.0,
        wave_area_km2=12.3,
        mode="stationary",
    )
    assert WaveFieldLayerURI(**lyr.model_dump()) == lyr
    # reuses the shared SnapWave wave-height preset (no new style key).
    assert lyr.style_preset == "continuous_wave_height"


# ===========================================================================
# (2) build_spec assembly.
# ===========================================================================
def test_build_spec_synthesizes_demo_boundary():
    from grace2_agent.workflows.run_swan import build_swan_build_spec

    args = SwanRunArgs(bbox=_AOI, mode="stationary")  # no explicit boundary
    spec = build_swan_build_spec(args, mesh_cells=(60, 80))
    assert spec["mode"] == "stationary"
    assert spec["bbox"] == list(_AOI)
    assert spec["bottom_file"] == "bottom.bot"
    assert spec["mx"] == 60 and spec["my"] == 80
    # demo boundary synthesized from the AOI geometry: this AOI is taller (N-S)
    # than wide (E-W), so the offshore-facing side is East (the height>=width
    # heuristic in synthesize_demo_wave_boundary).
    assert spec["boundary"]["side"] == "E"
    assert spec["boundary"]["hs_m"] > 0.0
    # no wind file unless wind_uri was set.
    assert "wind_file" not in spec
    assert spec["output_quantities"] == ["HSIGN", "RTP", "DIR"]


def test_build_spec_respects_explicit_boundary_and_wind():
    from grace2_agent.workflows.run_swan import build_swan_build_spec

    args = SwanRunArgs(
        bbox=_AOI,
        mode="nonstationary",
        boundary=SwanWaveBoundary(hs_m=5.0, tp_s=12.0, dir_deg=200.0, side="E"),
        wind_uri="s3://cache/wind.dat",
    )
    spec = build_swan_build_spec(args, wind_dest="wind.dat")
    assert spec["mode"] == "nonstationary"
    assert spec["boundary"]["hs_m"] == 5.0
    assert spec["boundary"]["side"] == "E"
    assert spec["wind_file"] == "wind.dat"


# ===========================================================================
# (3) Solver registration + bridge tool registered.
# ===========================================================================
def test_swan_registered_in_solver_workflow_registry():
    from grace2_agent.tools.solver import SOLVER_WORKFLOW_REGISTRY
    from grace2_agent.workflows.run_swan import (
        SWAN_SOLVER_NAME,
        register_swan_solver,
    )

    register_swan_solver()  # idempotent
    assert SWAN_SOLVER_NAME in SOLVER_WORKFLOW_REGISTRY


def test_run_swan_waves_registered_in_tool_registry():
    import grace2_agent.tools  # noqa: F401 -- fire eager imports
    from grace2_agent.tools import TOOL_REGISTRY

    assert "run_swan_waves" in TOOL_REGISTRY


def test_run_swan_waves_typed_error_on_missing_bbox():
    import asyncio

    from grace2_agent.tools.run_swan_tool import run_swan_waves

    out = asyncio.run(run_swan_waves(bbox=None))
    assert isinstance(out, dict)
    assert out["status"] == "error"
    assert out["error_code"] == "SWAN_PARAMS_INCOMPLETE"

    out2 = asyncio.run(run_swan_waves(bbox="garbage"))
    assert out2["status"] == "error"
    assert out2["error_code"] == "SWAN_PARAMS_INVALID"


# ===========================================================================
# (4) postprocess on a SYNTHETIC swan_out.mat (upload stubbed).
# ===========================================================================
def _synthetic_swan_mat(
    path: Path, mx: int, my: int, hs_fn, *, frames: int = 1, with_tp_dir: bool = True
) -> None:
    """Write a SWAN-style swan_out.mat with Hsig / RTp / Dir arrays.

    SWAN writes one variable per quantity (stationary) or per frame
    (nonstationary, with a frame suffix). ``hs_fn(i, j, frame) -> hs`` supplies Hs;
    Tp/Dir are constants when ``with_tp_dir``. Row 0 = south (SWAN idla=1).
    """
    from scipy.io import savemat

    mat: dict = {}
    for f in range(frames):
        hs = np.zeros((my, mx), dtype="float64")
        for j in range(my):
            for i in range(mx):
                hs[j, i] = float(hs_fn(i, j, f))
        suffix = "" if frames == 1 else f"_{f + 1:02d}"
        mat[f"Hsig{suffix}"] = hs
        if with_tp_dir:
            mat[f"RTp{suffix}"] = np.where(hs > 0.0, 9.0, -999.0)
            mat[f"Dir{suffix}"] = np.where(hs > 0.0, 180.0, -999.0)
    savemat(str(path), mat)


def test_read_swan_mat_fields_reads_hs_tp_dir(tmp_path: Path):
    from grace2_agent.workflows.postprocess_swan import read_swan_mat_fields

    mat = tmp_path / "swan_out.mat"
    _synthetic_swan_mat(mat, 6, 4, lambda i, j, f: 2.0 if j >= 2 else 0.0)
    fields = read_swan_mat_fields(mat)
    assert len(fields["hs"]) == 1
    assert len(fields["tp"]) == 1
    assert len(fields["dir"]) == 1
    hs = fields["hs"][0]
    assert hs.shape == (4, 6)
    assert np.nanmax(hs) == pytest.approx(2.0)


def test_compute_swan_wave_metrics_on_synthetic_grid():
    from grace2_agent.workflows.postprocess_swan import compute_swan_wave_metrics

    hs = np.full((8, 8), 0.0)
    hs[4:, :] = 2.5  # half wave-bearing
    tp = np.where(hs > 0, 10.0, np.nan)
    dr = np.where(hs > 0, 190.0, np.nan)
    m = compute_swan_wave_metrics(hs, bbox=_AOI, tp_grid=tp, dir_grid=dr)
    assert m["max_hs_m"] == pytest.approx(2.5)
    assert m["mean_tp_s"] == pytest.approx(10.0)
    assert m["mean_dir_deg"] == pytest.approx(190.0, abs=0.5)
    assert m["wave_cell_count"] > 0
    assert m["wave_area_km2"] > 0.0


def _fake_upload(local_cog, run_id, runs_bucket=None, *, dest_filename="x.tif"):
    # assert the COG is a valid EPSG:4326 raster before "uploading".
    import rasterio

    with rasterio.open(local_cog) as ds:
        assert str(ds.crs) == "EPSG:4326"
        assert ds.count == 1
    return f"s3://fake-runs/{run_id}/{dest_filename}"


def test_postprocess_swan_end_to_end_shape(tmp_path: Path):
    """A multi-frame synthetic run yields the EXACT (layers, metrics) shape:
    peak primary + contiguous 'Wave height step N' frames, all VALID COGs."""
    from grace2_agent.workflows import postprocess_swan as ps

    mat = tmp_path / "swan_out.mat"
    # 5 frames; wave height rises then falls so the peak is the middle frame.
    amps = [0.5, 1.5, 3.0, 1.0, 0.2]

    def hs_fn(i, j, f):
        return amps[f] if (i + j) % 2 == 0 else 0.0

    _synthetic_swan_mat(mat, 10, 10, hs_fn, frames=5)

    with patch.object(ps, "_upload_cog_to_runs_bucket", _fake_upload):
        layers, metrics = ps.postprocess_swan(
            tmp_path, _AOI, run_id="RID123", mode="nonstationary"
        )

    # layers[0] = peak primary.
    peak = layers[0]
    assert isinstance(peak, WaveFieldLayerURI)
    assert peak.role == "primary"
    assert peak.name == "Peak wave height"
    assert peak.style_preset == "continuous_wave_height"
    assert peak.mode == "nonstationary"
    assert peak.max_hs_m == pytest.approx(3.0)  # the middle frame amplitude
    assert peak.uri.startswith("s3://fake-runs/RID123/swan_wave_height_peak.tif")
    assert metrics["max_hs_m"] == pytest.approx(3.0)

    # layers[1:] = contiguous 'Wave height step N' frames, distinct URIs.
    frames = layers[1:]
    assert len(frames) >= 2
    uris = set()
    for n, fr in enumerate(frames, start=1):
        assert fr.role == "context"
        assert fr.name == f"Wave height step {n}"
        assert _WEB_STEP_TOKEN_RE.search(fr.name)
        uris.add(fr.uri)
    assert len(uris) == len(frames)  # distinct keys -> no dedup collapse


def test_postprocess_swan_empty_output_raises(tmp_path: Path):
    from grace2_agent.workflows.postprocess_swan import (
        PostprocessSwanError,
        postprocess_swan,
    )

    # no swan_out.mat at all -> SWAN_OUTPUT_EMPTY.
    with pytest.raises(PostprocessSwanError) as ei:
        postprocess_swan(tmp_path, _AOI, run_id="X", mode="stationary")
    assert ei.value.error_code == "SWAN_OUTPUT_EMPTY"


def test_postprocess_swan_all_calm_raises_honesty_floor(tmp_path: Path):
    """Honesty floor: a run whose Hs is everywhere below the calm threshold is NOT
    a usable wave field -- it raises SWAN_OUTPUT_EMPTY, never status ok."""
    from grace2_agent.workflows.postprocess_swan import (
        PostprocessSwanError,
        postprocess_swan,
    )

    mat = tmp_path / "swan_out.mat"
    _synthetic_swan_mat(mat, 8, 8, lambda i, j, f: 0.0)  # all calm
    with pytest.raises(PostprocessSwanError) as ei:
        postprocess_swan(tmp_path, _AOI, run_id="X", mode="stationary")
    assert ei.value.error_code == "SWAN_OUTPUT_EMPTY"


# ===========================================================================
# (5) Composer arg-assembly with run_solver / wait_for_completion MOCKED.
# ===========================================================================
class _FakeHandle:
    run_id = "BATCHRID"
    workflow_name = "aws-batch"


class _FakeRunResult:
    run_id = "BATCHRID"
    status = "complete"
    output_uri = "s3://runs/BATCHRID/"
    error_code = None
    error_message = None
    cancellation_reason = None
    batch_compute_meta = {"instance_type": "c7i.2xlarge"}


def test_composer_arg_assembly_and_dispatch(tmp_path: Path):
    """The composer stages a manifest, dispatches via a MOCKED run_solver, and
    returns the peak WaveFieldLayerURI -- no AWS, no SWAN. Asserts the run_solver
    call carries solver='swan' + the staged manifest_uri."""
    import asyncio

    from grace2_agent.workflows import model_wave_scenario as comp
    from grace2_agent.workflows.run_swan import SwanStaging

    run_args = SwanRunArgs(bbox=_AOI, mode="nonstationary", output_frames=4)

    captured: dict = {}

    def _fake_stage(ra, *, dem_uri, run_id=None, wind_uri=None, mesh_cells=(100, 100)):
        captured["dem_uri"] = dem_uri
        return SwanStaging(
            run_id="STAGERID",
            manifest_uri="s3://cache/swan_setup/STAGERID/manifest.json",
            build_spec={"mode": "nonstationary"},
            run_args=ra,
            bbox=tuple(ra.bbox),
            n_active_cells=10000,
        )

    def _fake_run_solver(*, solver, model_setup_uri, compute_class):
        captured["solver"] = solver
        captured["model_setup_uri"] = model_setup_uri
        captured["compute_class"] = compute_class
        return _FakeHandle()

    async def _fake_wait(handle):
        return _FakeRunResult()

    def _fake_download(run_id):
        return str(tmp_path)  # a dir with no .mat -> postprocess is mocked too

    def _fake_postprocess(out_dir, bbox, *, run_id, mode, **_kw):
        peak = WaveFieldLayerURI(
            layer_id=f"swan-wave-height-peak-{run_id}",
            name="Peak wave height",
            layer_type="raster",
            uri=f"s3://runs/{run_id}/swan_wave_height_peak.tif",
            style_preset=SWAN_WAVE_HEIGHT_STYLE_PRESET,
            role="primary",
            units="meters",
            bbox=tuple(bbox),
            max_hs_m=3.3,
            mean_tp_s=8.8,
            mean_dir_deg=181.0,
            wave_area_km2=4.2,
            mode=mode,
        )
        return [peak], {"max_hs_m": 3.3}

    def _fake_publish(raw_peak, run_id):
        return raw_peak.model_copy(update={"uri": "https://tiles/peak.png"})

    # The composer imports run_solver / wait_for_completion / EmitterBinding /
    # set_emitter_binding INSIDE the function (from ..tools.solver import ...), so
    # they must be patched at the SOURCE module, not on the composer module.
    from grace2_agent.tools import solver as solver_mod

    with patch.object(comp, "_fetch_bathy_for_swan", lambda b: "s3://cache/topo.tif"), \
         patch.object(comp, "stage_swan_manifest", _fake_stage), \
         patch.object(solver_mod, "run_solver", _fake_run_solver), \
         patch.object(solver_mod, "wait_for_completion", _fake_wait), \
         patch.object(solver_mod, "set_emitter_binding", lambda *a, **k: None), \
         patch.object(comp, "mint_dispatch_and_sim_cards", _amock(None)), \
         patch.object(comp, "route_sim_terminal", _amock(None)), \
         patch.object(comp, "_download_batch_swan_outputs", _fake_download), \
         patch.object(comp, "postprocess_swan", _fake_postprocess), \
         patch.object(comp, "_publish_peak_layer", _fake_publish), \
         patch.object(comp, "current_emitter", lambda: None), \
         patch.object(comp, "drive_live_solve_progress", _amock(None)):
        peak = asyncio.run(comp.model_wave_scenario(run_args))

    assert isinstance(peak, WaveFieldLayerURI)
    assert peak.uri == "https://tiles/peak.png"
    assert peak.max_hs_m == pytest.approx(3.3)
    assert captured["solver"] == "swan"
    assert captured["model_setup_uri"].endswith("manifest.json")
    assert captured["dem_uri"] == "s3://cache/topo.tif"


def _amock(ret):
    """Build an async no-op returning ``ret`` (for the emitter helpers)."""
    async def _inner(*a, **k):
        return ret
    return _inner
