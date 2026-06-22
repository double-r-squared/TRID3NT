"""Unit tests for the SWAN deck author (``deck_builder``) -- SWAN Phase 1.

The SWAN analogue of ``services/workers/geoclaw/test_setrun_builder.py``. These
pin the DETERMINISTIC, swan-free deck-authoring core (the heart of the engine):

  1. build_spec validation -- typed error on missing/invalid fields.
  2. .swn command-file generation -- the rendered command file carries the
     load-bearing SWAN keyword blocks (CGRID/CIRCLE, INPGRID+READINP BOTTOM,
     [WIND], GEN3, BOUND SHAPE + BOUNDSPEC, BLOCK output, COMPUTE) wired from the
     spec, per mode.
  3. bottom input array -- a rectangular depth grid of the right shape, demo flat
     bathymetry by default, overridable via depth_fn.
  4. full deck build into a tmp dir + the SwanDeckManifest provenance + the INPUT
     file (SWAN's literal command-file convention).

NO SWAN / gfortran is required -- the deck author never imports them; it is a pure
string render (mirrors the GeoClaw deck-author test).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.workers.swan.deck_builder import (
    INPUT_FILENAME,
    OUTPUT_MAT_FILENAME,
    SwanBuildSpec,
    SwanDeckError,
    build_swan_deck,
    parse_build_spec,
    render_bottom_input,
    render_swn_command_file,
)

_AOI = [-85.75, 29.55, -85.25, 30.20]  # Mexico Beach-ish demo box


def _spec(**over) -> dict:
    base = {
        "mode": "stationary",
        "bbox": list(_AOI),
        "bottom_file": "bottom.bot",
        "mx": 40,
        "my": 50,
        "n_dir": 36,
        "n_freq": 32,
        "freq_low_hz": 0.04,
        "freq_high_hz": 1.0,
        "boundary": {
            "hs_m": 3.0,
            "tp_s": 9.0,
            "dir_deg": 180.0,
            "spread_deg": 25.0,
            "side": "S",
        },
        "friction": True,
        "breaking": True,
        "triads": True,
        "output_quantities": ["HSIGN", "RTP", "DIR"],
    }
    base.update(over)
    return base


# ===========================================================================
# (1) build_spec validation.
# ===========================================================================
def test_parse_valid_spec_fills_defaults():
    spec = parse_build_spec({"bbox": _AOI, "bottom_file": "b.bot"})
    assert isinstance(spec, SwanBuildSpec)
    assert spec.mode == "stationary"  # default
    assert spec.n_dir == 36
    assert spec.n_freq == 32
    assert spec.bbox == tuple(_AOI)
    # HSIGN is always guaranteed present.
    assert "HSIGN" in spec.output_quantities


def test_parse_rejects_bad_mode():
    with pytest.raises(SwanDeckError) as ei:
        parse_build_spec(_spec(mode="nope"))
    assert ei.value.error_code == "SWAN_SPEC_INVALID"


def test_parse_rejects_bad_bbox():
    with pytest.raises(SwanDeckError):
        parse_build_spec({"bbox": [1, 2, 3], "bottom_file": "b.bot"})
    with pytest.raises(SwanDeckError):
        parse_build_spec({"bbox": [10, 10, 5, 5], "bottom_file": "b.bot"})


def test_parse_requires_bottom_file():
    with pytest.raises(SwanDeckError) as ei:
        parse_build_spec({"bbox": _AOI})
    assert ei.value.error_code == "SWAN_SPEC_INVALID"


def test_parse_rejects_bad_spectral_grid():
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(n_dir=8))  # < 12
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(n_freq=2))  # < 4
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(freq_low_hz=1.0, freq_high_hz=0.5))  # low >= high


def test_parse_rejects_bad_boundary():
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(boundary={"hs_m": -1.0}))
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(boundary={"dir_deg": 999.0}))
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(boundary={"side": "Z"}))


def test_parse_rejects_unknown_output_quantity():
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(output_quantities=["NOPE"]))


def test_parse_nonstationary_requires_positive_timing():
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(mode="nonstationary", sim_duration_s=0))
    with pytest.raises(SwanDeckError):
        parse_build_spec(_spec(mode="nonstationary", time_step_s=0))


# ===========================================================================
# (2) .swn command-file generation -- load-bearing SWAN keyword blocks.
# ===========================================================================
def test_render_swn_stationary_has_load_bearing_blocks():
    spec = parse_build_spec(_spec(mode="stationary"))
    text = render_swn_command_file(spec)
    # PROJECT + SET + run mode + coordinates.
    assert "PROJECT 'GRACE2' 'WAVE'" in text
    assert "SET NAUTICAL" in text
    assert "MODE STATIONARY TWODIMENSIONAL" in text
    assert "COORDINATES SPHERICAL" in text
    # CGRID with the spectral CIRCLE block (ndir flow fhigh nfreq).
    assert "CGRID REGULAR" in text
    assert "CIRCLE 36 0.0400 1.0000 32" in text
    # Domain origin wired from bbox (SW corner).
    assert "-85.750000 29.550000" in text
    # Bottom input grid + read of the staged bottom file.
    assert "INPGRID BOTTOM REGULAR" in text
    assert "READINP BOTTOM 1.0 'bottom.bot' 1 0 FREE" in text
    # Physics: GEN3 + friction + breaking + triads.
    assert "GEN3 WESTHUYSEN" in text
    assert "FRICTION JONSWAP" in text
    assert "BREAKING CONSTANT" in text
    assert "TRIAD" in text
    # Parametric boundary (JONSWAP shape + SIDE S CONSTANT PAR Hs Tp dir dd).
    assert "BOUND SHAPE JONSWAP" in text
    assert "BOUNDSPEC SIDE S CONSTANT PAR 3.000 9.000 180.00 25.00" in text
    # Gridded output BLOCK to the .mat + the requested quantities.
    assert f"BLOCK 'COMPGRID' NOHEADER '{OUTPUT_MAT_FILENAME}' LAYOUT 3 HSIGN RTP DIR" in text
    # Stationary compute + stop.
    assert "COMPUTE STATIONARY" in text
    assert "STOP" in text


def test_render_swn_nonstationary_has_nonstat_compute():
    spec = parse_build_spec(
        _spec(mode="nonstationary", sim_duration_s=10800.0, time_step_s=600.0)
    )
    text = render_swn_command_file(spec)
    assert "MODE NONSTATIONARY TWODIMENSIONAL" in text
    assert "COMPUTE NONSTATIONARY" in text
    assert "600.0 SEC" in text
    assert "COMPUTE STATIONARY" not in text


def test_render_swn_wind_block_only_when_wind_file_present():
    no_wind = parse_build_spec(_spec())
    assert "READINP WIND" not in render_swn_command_file(no_wind)
    with_wind = parse_build_spec(_spec(wind_file="wind.dat"))
    text = render_swn_command_file(with_wind)
    assert "INPGRID WIND REGULAR" in text
    assert "READINP WIND 1.0 'wind.dat' 1 0 FREE" in text


def test_render_swn_physics_toggles_omit_blocks_when_disabled():
    spec = parse_build_spec(_spec(friction=False, breaking=False, triads=False))
    text = render_swn_command_file(spec)
    assert "GEN3" in text  # GEN3 always on (third-generation core)
    assert "FRICTION" not in text
    assert "BREAKING" not in text
    assert "TRIAD" not in text


def test_render_swn_boundary_side_respected():
    spec = parse_build_spec(_spec(boundary={"side": "E", "hs_m": 4.5, "tp_s": 11.0}))
    text = render_swn_command_file(spec)
    assert "BOUNDSPEC SIDE E CONSTANT PAR 4.500 11.000" in text


# ===========================================================================
# (3) bottom input array render.
# ===========================================================================
def test_render_bottom_input_shape_and_flat_default():
    spec = parse_build_spec(_spec(mx=4, my=3))
    text = render_bottom_input(spec)
    rows = [r for r in text.splitlines() if r.strip()]
    # (my+1) rows of (mx+1) values each (SWAN grid POINTS = mesh + 1).
    assert len(rows) == 4  # my+1 = 3+1
    for r in rows:
        vals = r.split()
        assert len(vals) == 5  # mx+1 = 4+1
        # flat demo bathymetry = 10.0 m everywhere.
        assert all(abs(float(v) - 10.0) < 1e-6 for v in vals)


def test_render_bottom_input_uses_depth_fn():
    spec = parse_build_spec(_spec(mx=4, my=4))
    # depth = 5 m + 1 m per degree of longitude east of the SW corner.
    def depth_fn(lon, lat):
        return 5.0 + (lon - _AOI[0])

    text = render_bottom_input(spec, depth_fn=depth_fn)
    rows = [r for r in text.splitlines() if r.strip()]
    first_row_vals = [float(v) for v in rows[0].split()]
    # the west-most value is ~5.0, the east-most is deeper (5 + bbox width).
    assert first_row_vals[0] == pytest.approx(5.0, abs=1e-6)
    assert first_row_vals[-1] > first_row_vals[0]


# ===========================================================================
# (4) full deck build into a tmp dir + SwanDeckManifest provenance.
# ===========================================================================
def test_build_swan_deck_writes_input_and_bottom(tmp_path: Path):
    manifest = build_swan_deck(_spec(mode="stationary"), tmp_path)
    # The command file MUST be written as the file literally named INPUT (the SWAN
    # convention swanrun reads) -- this is the load-bearing convention.
    assert (tmp_path / INPUT_FILENAME).exists()
    assert (tmp_path / "swan_run.swn").exists()
    assert (tmp_path / "bottom.bot").exists()
    assert (tmp_path / "deck_manifest.json").exists()
    assert INPUT_FILENAME in manifest.files_written
    assert "bottom.bot" in manifest.files_written
    assert "stationary" in manifest.driver_descriptor
    assert manifest.wind_enabled is False
    # the on-disk INPUT carries the SWAN keyword sequence.
    input_text = (tmp_path / INPUT_FILENAME).read_text()
    assert "CGRID REGULAR" in input_text
    assert "COMPUTE STATIONARY" in input_text
    # the persisted manifest round-trips.
    disk = json.loads((tmp_path / "deck_manifest.json").read_text())
    assert disk["mode"] == "stationary"
    assert disk["boundary_hs_m"] == 3.0
    assert disk["output_quantities"] == ["HSIGN", "RTP", "DIR"]


def test_build_swan_deck_wind_enabled_manifest(tmp_path: Path):
    manifest = build_swan_deck(_spec(wind_file="wind.dat"), tmp_path)
    assert manifest.wind_enabled is True
    assert "ERA5 wind" in manifest.driver_descriptor
    assert "READINP WIND" in (tmp_path / INPUT_FILENAME).read_text()
