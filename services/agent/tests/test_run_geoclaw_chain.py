"""End-to-end MODULE tests for the GeoClaw (Clawpack) shallow-water engine
(sprint-17), exercised in ISOLATION with run_solver / boto3 / network MOCKED.

GeoClaw is NEW + BATCH-only + not yet registry-wired by the orchestrator, so we
do NOT run the full agent suite or a live Clawpack solve. These tests pin the
agent-side MODULES the lane owns:

  1. **Contract round-trip + scenario alias normalization** —
     ``GeoClawRunArgs`` / ``GeoClawDepthLayerURI`` (no SWMM/clawpack dep).
  2. **build_spec assembly** — ``build_geoclaw_build_spec`` maps run args onto the
     worker's setrun_builder field dict, per scenario.
  3. **Solver registration** — ``'geoclaw'`` is a first-class entry in
     ``SOLVER_WORKFLOW_REGISTRY`` and the bridge tool is registered.
  4. **postprocess on a SYNTHETIC fort.q** — a hand-built GeoClaw fort.q frame
     parses + rasterizes + writes a VALID EPSG:4326 depth COG (upload stubbed),
     yielding the EXACT postprocess_flood (layers, metrics) shape.
  5. **Composer arg-assembly with run_solver MOCKED** — the composer stages a
     manifest, dispatches via a mocked run_solver/wait_for_completion, downloads
     a mocked Batch output, postprocesses, and returns the peak GeoClawDepthLayerURI
     + emits frames out-of-band — all without touching AWS or Clawpack.

rasterio + numpy are required for (4)+(5); they are in the agent venv.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from grace2_contracts.geoclaw_contracts import (
    GEOCLAW_DEPTH_STYLE_PRESET,
    GeoClawDepthLayerURI,
    GeoClawRunArgs,
)

_AOI = (-85.75, 29.55, -85.25, 30.20)

# The web parseFrameToken regex — the frame NAMES must match it or the
# sequential group never forms (same guard as test_postprocess_swmm/flood).
_WEB_STEP_TOKEN_RE = re.compile(r"\b(?:step|frame|idx|index)\s*\+?(\d{1,4})\b", re.I)


# ===========================================================================
# (1) Contract round-trip + alias normalization.
# ===========================================================================
def test_run_args_round_trip_and_scenario_aliases():
    a = GeoClawRunArgs(bbox=_AOI, scenario="breach", dam_break_depth_m=8.0)
    assert a.scenario == "dam_break"  # alias
    b = GeoClawRunArgs(bbox=_AOI, scenario="wave")
    assert b.scenario == "tsunami"  # alias
    c = GeoClawRunArgs(bbox=_AOI, scenario="storm_surge")
    assert c.scenario == "surge"  # alias
    # round-trip.
    a2 = GeoClawRunArgs(**a.model_dump())
    assert a2 == a


def test_run_args_rejects_bad_scenario_and_source_point():
    with pytest.raises(Exception):
        GeoClawRunArgs(bbox=_AOI, scenario="not_a_scenario")
    with pytest.raises(Exception):
        GeoClawRunArgs(bbox=_AOI, source_lonlat=(999.0, 0.0))


def test_depth_layer_uri_round_trip():
    lyr = GeoClawDepthLayerURI(
        layer_id="geoclaw-depth-peak-x",
        name="Peak flood depth",
        layer_type="raster",
        uri="s3://b/k.tif",
        style_preset=GEOCLAW_DEPTH_STYLE_PRESET,
        role="primary",
        units="meters",
        bbox=_AOI,
        max_depth_m=2.5,
        flooded_area_km2=1.2,
        max_inundation_m=1.1,
        scenario="tsunami",
    )
    assert GeoClawDepthLayerURI(**lyr.model_dump()) == lyr
    # reuses the shared depth preset (no new style key).
    assert lyr.style_preset == "continuous_flood_depth"


# ===========================================================================
# (2) build_spec assembly.
# ===========================================================================
def test_build_spec_dam_break():
    from grace2_agent.workflows.run_geoclaw import build_geoclaw_build_spec

    args = GeoClawRunArgs(
        bbox=_AOI, scenario="dam_break", dam_break_depth_m=12.0, output_frames=10
    )
    spec = build_geoclaw_build_spec(args, base_num_cells=(50, 60))
    assert spec["scenario"] == "dam_break"
    assert spec["bbox"] == list(_AOI)
    assert spec["topo_file"] == "topo.asc"
    assert spec["dam_break_depth_m"] == 12.0
    assert spec["base_num_cells"] == [50, 60]
    assert spec["output_frames"] == 10
    # no dtopo/surge keys for dam_break.
    assert "dtopo_file" not in spec
    assert "surge_forcing_file" not in spec


def test_build_spec_tsunami_with_staged_dtopo():
    from grace2_agent.workflows.run_geoclaw import build_geoclaw_build_spec

    args = GeoClawRunArgs(bbox=_AOI, scenario="tsunami", source_magnitude=8.4)
    spec = build_geoclaw_build_spec(args, dtopo_dest="dtopo.tt3")
    assert spec["scenario"] == "tsunami"
    assert spec["source_magnitude"] == 8.4
    assert spec["dtopo_file"] == "dtopo.tt3"


def test_build_spec_surge_with_forcing_and_source_point():
    from grace2_agent.workflows.run_geoclaw import build_geoclaw_build_spec

    args = GeoClawRunArgs(
        bbox=_AOI, scenario="surge", sea_level_m=2.0, source_lonlat=(-85.4, 29.8)
    )
    spec = build_geoclaw_build_spec(args, surge_dest="surge.csv")
    assert spec["scenario"] == "surge"
    assert spec["sea_level_m"] == 2.0
    assert spec["surge_forcing_file"] == "surge.csv"
    assert spec["source_lonlat"] == [-85.4, 29.8]


# ===========================================================================
# (3) Solver registration + bridge tool registered.
# ===========================================================================
def test_geoclaw_registered_in_solver_workflow_registry():
    from grace2_agent.tools.solver import SOLVER_WORKFLOW_REGISTRY
    from grace2_agent.workflows.run_geoclaw import (
        GEOCLAW_SOLVER_NAME,
        register_geoclaw_solver,
    )

    register_geoclaw_solver()  # idempotent
    assert GEOCLAW_SOLVER_NAME in SOLVER_WORKFLOW_REGISTRY


def test_run_geoclaw_inundation_typed_error_on_missing_bbox():
    import asyncio

    from grace2_agent.tools.run_geoclaw_tool import run_geoclaw_inundation

    out = asyncio.run(run_geoclaw_inundation(bbox=None))
    assert isinstance(out, dict)
    assert out["status"] == "error"
    assert out["error_code"] == "GEOCLAW_PARAMS_INCOMPLETE"

    out2 = asyncio.run(run_geoclaw_inundation(bbox="garbage"))
    assert out2["status"] == "error"
    assert out2["error_code"] == "GEOCLAW_PARAMS_INVALID"


# ===========================================================================
# (4) postprocess on a SYNTHETIC fort.q frame (upload stubbed).
# ===========================================================================
def _synthetic_fort_q(mx: int, my: int, bbox, depth_fn, level: int = 1) -> str:
    """Build a GeoClaw fort.qNNNN frame text for a single AMR patch.

    Format: 8 header lines ("<value>    <name>"), a blank line, then mx*my data
    rows of "h hu hv" with i (x) inner, j (y) outer ascending; a blank line
    separates consecutive j-rows. ``depth_fn(i, j) -> h`` supplies q[0].
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    dx = (max_lon - min_lon) / mx
    dy = (max_lat - min_lat) / my
    lines = [
        "1    grid_number",
        f"{level}    AMR_level",
        f"{mx}    mx",
        f"{my}    my",
        f"{min_lon:.8f}    xlow",
        f"{min_lat:.8f}    ylow",
        f"{dx:.8f}    dx",
        f"{dy:.8f}    dy",
        "",
    ]
    for j in range(my):
        for i in range(mx):
            h = depth_fn(i, j)
            lines.append(f"{h:.6e}  0.0  0.0")
        lines.append("")  # blank line ends a j-row block
    return "\n".join(lines) + "\n"


def test_parse_fort_q_frame_reads_patch_depths():
    from grace2_agent.workflows.postprocess_geoclaw import parse_fort_q_frame

    # a 4x3 patch with depth = i + 10*j so each cell is identifiable.
    text = _synthetic_fort_q(4, 3, _AOI, lambda i, j: float(i + 10 * j))
    patches = parse_fort_q_frame(text)
    assert len(patches) == 1
    p = patches[0]
    assert (p.mx, p.my, p.level) == (4, 3, 1)
    # h[j, i] == i + 10*j (row 0 = ylow = south).
    assert p.h[0, 0] == pytest.approx(0.0)
    assert p.h[0, 3] == pytest.approx(3.0)
    assert p.h[2, 1] == pytest.approx(21.0)


def test_rasterize_and_metrics_on_synthetic_frame():
    from grace2_agent.workflows.postprocess_geoclaw import (
        compute_geoclaw_depth_metrics,
        parse_fort_q_frame,
        rasterize_frame_to_grid,
    )

    # half the patch wet at 2.0 m, half dry (0 -> masked).
    text = _synthetic_fort_q(
        8, 8, _AOI, lambda i, j: 2.0 if j >= 4 else 0.0
    )
    patches = parse_fort_q_frame(text)
    grid = rasterize_frame_to_grid(patches, _AOI, (32, 32))
    wet = np.isfinite(grid)
    assert wet.any()
    # all wet cells carry 2.0.
    assert np.nanmax(grid) == pytest.approx(2.0)
    m = compute_geoclaw_depth_metrics(grid, bbox=_AOI)
    assert m["max_depth_m"] == pytest.approx(2.0)
    assert m["flooded_cell_count"] > 0
    assert m["flooded_area_km2"] > 0.0


def _fake_upload(local_cog, run_id, runs_bucket=None, *, dest_filename="x.tif"):
    # assert the COG is a valid EPSG:4326 raster before "uploading".
    import rasterio

    with rasterio.open(local_cog) as ds:
        assert str(ds.crs) == "EPSG:4326"
        assert ds.count == 1
    return f"s3://fake-runs/{run_id}/{dest_filename}"


def test_postprocess_geoclaw_end_to_end_shape(tmp_path: Path):
    """A multi-frame synthetic run yields the EXACT (layers, metrics) shape:
    peak primary + contiguous 'Flood depth step N' frames, all VALID COGs."""
    from grace2_agent.workflows import postprocess_geoclaw as pg

    out = tmp_path / "_output"
    out.mkdir()
    # 5 frames; depth rises then falls so the peak is the middle frame.
    amps = [0.5, 1.5, 3.0, 1.0, 0.2]
    for fi, amp in enumerate(amps):
        text = _synthetic_fort_q(
            10, 10, _AOI, (lambda a: (lambda i, j: a if (i + j) % 2 == 0 else 0.0))(amp)
        )
        (out / f"fort.q{fi:04d}").write_text(text)
        (out / f"fort.t{fi:04d}").write_text(f"{fi * 60.0:.6e}    time\n")

    with patch.object(pg, "_upload_cog_to_runs_bucket", _fake_upload):
        layers, metrics = pg.postprocess_geoclaw(
            tmp_path, _AOI, run_id="RID123", scenario="dam_break", grid_shape=(40, 40)
        )

    # layers[0] = peak primary.
    peak = layers[0]
    assert isinstance(peak, GeoClawDepthLayerURI)
    assert peak.role == "primary"
    assert peak.name == "Peak flood depth"
    assert peak.style_preset == "continuous_flood_depth"
    assert peak.scenario == "dam_break"
    assert peak.max_depth_m == pytest.approx(3.0)  # the middle frame amplitude
    assert peak.uri.startswith("s3://fake-runs/RID123/geoclaw_depth_peak.tif")
    assert metrics["max_depth_m"] == pytest.approx(3.0)

    # layers[1:] = contiguous 'Flood depth step N' frames, distinct URIs.
    frames = layers[1:]
    assert len(frames) >= 2
    uris = set()
    for n, fr in enumerate(frames, start=1):
        assert fr.role == "context"
        assert fr.name == f"Flood depth step {n}"
        assert _WEB_STEP_TOKEN_RE.search(fr.name)
        uris.add(fr.uri)
    assert len(uris) == len(frames)  # distinct keys -> no dedup collapse


def test_postprocess_geoclaw_empty_output_raises(tmp_path: Path):
    from grace2_agent.workflows.postprocess_geoclaw import (
        PostprocessGeoClawError,
        postprocess_geoclaw,
    )

    with pytest.raises(PostprocessGeoClawError) as ei:
        postprocess_geoclaw(tmp_path, _AOI, run_id="X", scenario="dam_break")
    assert ei.value.error_code == "GEOCLAW_OUTPUT_EMPTY"


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
    returns the peak GeoClawDepthLayerURI — no AWS, no Clawpack. Asserts the
    run_solver call carries solver='geoclaw' + the staged manifest_uri."""
    import asyncio

    from grace2_agent.workflows import model_dambreak_geoclaw_scenario as comp
    from grace2_agent.workflows.run_geoclaw import GeoClawStaging

    run_args = GeoClawRunArgs(bbox=_AOI, scenario="dam_break", output_frames=4)

    captured: dict = {}

    def _fake_stage(ra, *, dem_uri, run_id=None, dtopo_uri=None, surge_uri=None,
                    base_num_cells=(40, 40)):
        captured["dem_uri"] = dem_uri
        return GeoClawStaging(
            run_id="STAGERID",
            manifest_uri="s3://cache/geoclaw_setup/STAGERID/manifest.json",
            build_spec={"scenario": "dam_break"},
            run_args=ra,
            bbox=tuple(ra.bbox),
            n_active_cells=1600,
        )

    def _fake_run_solver(*, solver, model_setup_uri, compute_class):
        captured["solver"] = solver
        captured["model_setup_uri"] = model_setup_uri
        captured["compute_class"] = compute_class
        return _FakeHandle()

    async def _fake_wait(handle):
        return _FakeRunResult()

    def _fake_download(run_id):
        return str(tmp_path)  # a dir with no _output -> postprocess is mocked too

    def _fake_postprocess(out_dir, bbox, *, run_id, scenario, **_kw):
        peak = GeoClawDepthLayerURI(
            layer_id=f"geoclaw-depth-peak-{run_id}",
            name="Peak flood depth",
            layer_type="raster",
            uri=f"s3://runs/{run_id}/geoclaw_depth_peak.tif",
            style_preset=GEOCLAW_DEPTH_STYLE_PRESET,
            role="primary",
            units="meters",
            bbox=tuple(bbox),
            max_depth_m=3.3,
            flooded_area_km2=2.1,
            max_inundation_m=1.4,
            scenario=scenario,
        )
        return [peak], {"max_depth_m": 3.3}

    def _fake_publish(raw_peak, run_id):
        return raw_peak.model_copy(update={"uri": "https://tiles/peak.png"})

    # The composer imports run_solver / wait_for_completion / EmitterBinding /
    # set_emitter_binding INSIDE the function (from ..tools.solver import ...), so
    # they must be patched at the SOURCE module, not on the composer module.
    from grace2_agent.tools import solver as solver_mod

    with patch.object(comp, "_fetch_topo_for_geoclaw", lambda b: "s3://cache/topo.tif"), \
         patch.object(comp, "stage_geoclaw_manifest", _fake_stage), \
         patch.object(solver_mod, "run_solver", _fake_run_solver), \
         patch.object(solver_mod, "wait_for_completion", _fake_wait), \
         patch.object(solver_mod, "set_emitter_binding", lambda *a, **k: None), \
         patch.object(comp, "mint_dispatch_and_sim_cards", _amock(None)), \
         patch.object(comp, "route_sim_terminal", _amock(None)), \
         patch.object(comp, "_download_batch_geoclaw_outputs", _fake_download), \
         patch.object(comp, "postprocess_geoclaw", _fake_postprocess), \
         patch.object(comp, "_publish_peak_layer", _fake_publish), \
         patch.object(comp, "current_emitter", lambda: None), \
         patch.object(comp, "drive_live_solve_progress", _amock(None)):
        peak = asyncio.run(comp.model_dambreak_geoclaw_scenario(run_args))

    assert isinstance(peak, GeoClawDepthLayerURI)
    assert peak.uri == "https://tiles/peak.png"
    assert peak.max_depth_m == pytest.approx(3.3)
    assert captured["solver"] == "geoclaw"
    assert captured["model_setup_uri"].endswith("manifest.json")
    assert captured["dem_uri"] == "s3://cache/topo.tif"


def _amock(ret):
    """Build an async no-op returning ``ret`` (for the emitter helpers)."""
    async def _inner(*a, **k):
        return ret
    return _inner
