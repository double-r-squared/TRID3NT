"""Unit tests for the GeoClaw deck author (``setrun_builder``) — sprint-17.

The GeoClaw analogue of ``services/workers/modflow/test_gwt_adapter.py``. These
pin the DETERMINISTIC, clawpack-free deck-authoring core:

  1. build_spec validation — typed error on missing/invalid fields.
  2. setrun.py generation — the rendered module is valid Python with the
     load-bearing GeoClaw blocks (clawdata domain/grid/output, geo_data,
     topofiles, amrdata) wired from the spec, per scenario.
  3. scenario source files — dam_break writes qinit.xyz, tsunami (synthetic)
     writes maketopo.py, surge writes neither.
  4. full deck build into a tmp dir + the DeckManifest provenance.

NO clawpack / gfortran is required — the deck author never imports them (the
rendered maketopo.py does, but is only EXECUTED by the entrypoint, never here).
We py-compile the rendered setrun.py to prove it is syntactically valid Python.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from services.workers.geoclaw.setrun_builder import (
    GeoClawBuildSpec,
    GeoClawDeckError,
    build_geoclaw_deck,
    parse_build_spec,
    render_maketopo_dtopo,
    render_qinit_data,
    render_setrun_py,
)

_AOI = [-85.75, 29.55, -85.25, 30.20]  # Mexico Beach-ish demo box


def _spec(**over) -> dict:
    base = {
        "scenario": "dam_break",
        "bbox": list(_AOI),
        "topo_file": "topo.asc",
        "sim_duration_s": 1800.0,
        "output_frames": 12,
        "amr_levels": 2,
        "manning_n": 0.03,
        "sea_level_m": 0.0,
        "base_num_cells": [30, 30],
        "dam_break_depth_m": 8.0,
    }
    base.update(over)
    return base


# ===========================================================================
# (1) build_spec validation.
# ===========================================================================
def test_parse_valid_spec_fills_defaults():
    spec = parse_build_spec({"bbox": _AOI, "topo_file": "t.asc"})
    assert isinstance(spec, GeoClawBuildSpec)
    assert spec.scenario == "dam_break"  # default
    assert spec.output_frames == 24
    assert spec.amr_levels == 2
    assert spec.bbox == tuple(_AOI)


def test_parse_rejects_bad_scenario():
    with pytest.raises(GeoClawDeckError) as ei:
        parse_build_spec(_spec(scenario="nope"))
    assert ei.value.error_code == "GEOCLAW_SPEC_INVALID"


def test_parse_rejects_bad_bbox():
    # wrong length
    with pytest.raises(GeoClawDeckError):
        parse_build_spec({"bbox": [1, 2, 3], "topo_file": "t.asc"})
    # min >= max
    with pytest.raises(GeoClawDeckError):
        parse_build_spec({"bbox": [10, 10, 5, 5], "topo_file": "t.asc"})


def test_parse_requires_topo_file():
    with pytest.raises(GeoClawDeckError) as ei:
        parse_build_spec({"bbox": _AOI})
    assert ei.value.error_code == "GEOCLAW_SPEC_INVALID"


def test_parse_rejects_nonpositive_duration_and_frames():
    with pytest.raises(GeoClawDeckError):
        parse_build_spec(_spec(sim_duration_s=0))
    with pytest.raises(GeoClawDeckError):
        parse_build_spec(_spec(output_frames=0))


# ===========================================================================
# (2) setrun.py generation — valid Python + load-bearing blocks.
# ===========================================================================
def test_render_setrun_is_valid_python_dam_break():
    spec = parse_build_spec(_spec(scenario="dam_break"))
    text = render_setrun_py(spec)
    # Must parse as valid Python (proves no f-string / quoting break).
    ast.parse(text)
    # The clawpack import is INSIDE the generated module (executed only by the
    # entrypoint), not in the author module.
    assert "from clawpack.clawutil import data" in text
    assert "def setrun(" in text
    assert "def setgeo(" in text
    # Domain wired from bbox.
    assert "clawdata.lower[0] = -85.75" in text
    assert "clawdata.upper[0] = -85.25" in text
    assert "clawdata.lower[1] = 29.55" in text
    assert "clawdata.upper[1] = 30.2" in text
    # Base grid + output frames wired from spec.
    assert "clawdata.num_cells[0] = 30" in text
    assert "clawdata.num_output_times = 12" in text
    assert "clawdata.tfinal = 1800.0" in text
    # geo_data: lat/lon coordinate system + manning + sea level.
    assert "geo_data.coordinate_system = 2" in text
    assert "geo_data.manning_coefficient = 0.03" in text
    assert "geo_data.sea_level = 0.0" in text
    # topofile wired.
    assert "topo_data.topofiles.append([3, 'topo.asc'])" in text
    # AMR levels.
    assert "amrdata.amr_levels_max = 2" in text
    # dam_break -> qinit block present.
    assert "qinit_data.qinit_type = 4" in text
    assert "qinit.xyz" in text


def test_render_setrun_tsunami_has_dtopo_block_not_qinit():
    spec = parse_build_spec(_spec(scenario="tsunami", source_magnitude=8.2))
    text = render_setrun_py(spec)
    ast.parse(text)
    assert "dtopo_data.dtopofiles" in text
    assert "dtopo.tt3" in text
    assert "qinit_data.qinit_type" not in text


def test_render_setrun_surge_has_neither_qinit_nor_dtopo():
    spec = parse_build_spec(_spec(scenario="surge", sea_level_m=1.5))
    text = render_setrun_py(spec)
    ast.parse(text)
    assert "qinit_data.qinit_type" not in text
    assert "dtopo_data.dtopofiles" not in text
    # sea_level offset is the surge v0.1 fallback.
    assert "geo_data.sea_level = 1.5" in text


def test_render_setrun_amr_ratios_scale_with_levels():
    spec = parse_build_spec(_spec(amr_levels=3))
    text = render_setrun_py(spec)
    ast.parse(text)
    # 3 levels -> 2 refinement ratios (between consecutive levels).
    assert "amrdata.refinement_ratios_x = [2, 2]" in text


# ===========================================================================
# (3) scenario source-file renders.
# ===========================================================================
def test_render_qinit_is_xyz_grid_with_raised_column():
    spec = parse_build_spec(_spec(scenario="dam_break", dam_break_depth_m=7.0))
    xyz = render_qinit_data(spec)
    rows = [r for r in xyz.splitlines() if r.strip()]
    assert len(rows) == 16 * 16  # n x n perturbation grid
    # at least one cell carries the raised-column height; corners are dry (0).
    zs = [float(r.split()[2]) for r in rows]
    assert max(zs) == pytest.approx(7.0)
    assert min(zs) == pytest.approx(0.0)


def test_render_maketopo_dtopo_is_valid_python_and_uses_dtopotools():
    spec = parse_build_spec(_spec(scenario="tsunami", source_magnitude=9.0))
    text = render_maketopo_dtopo(spec)
    ast.parse(text)
    assert "from clawpack.geoclaw import dtopotools" in text
    assert "mw = 9.0" in text
    assert 'fault.dtopo.write("dtopo.tt3"' in text


# ===========================================================================
# (4) full deck build into a tmp dir + DeckManifest provenance.
# ===========================================================================
def test_build_dam_break_deck_writes_setrun_and_qinit(tmp_path: Path):
    manifest = build_geoclaw_deck(_spec(scenario="dam_break"), tmp_path)
    assert manifest.scenario == "dam_break"
    assert (tmp_path / "setrun.py").exists()
    assert (tmp_path / "qinit.xyz").exists()
    assert (tmp_path / "deck_manifest.json").exists()
    assert "setrun.py" in manifest.files_written
    assert "qinit.xyz" in manifest.files_written
    assert "dam_break" in manifest.driver_descriptor
    # the on-disk setrun.py is valid Python.
    ast.parse((tmp_path / "setrun.py").read_text())
    # the persisted manifest round-trips.
    disk = json.loads((tmp_path / "deck_manifest.json").read_text())
    assert disk["scenario"] == "dam_break"
    assert disk["output_frames"] == 12


def test_build_tsunami_synthetic_writes_maketopo(tmp_path: Path):
    manifest = build_geoclaw_deck(_spec(scenario="tsunami"), tmp_path)
    assert (tmp_path / "maketopo.py").exists()
    assert "maketopo.py" in manifest.files_written
    assert "tsunami" in manifest.driver_descriptor
    assert not (tmp_path / "qinit.xyz").exists()


def test_build_tsunami_staged_dtopo_skips_maketopo(tmp_path: Path):
    manifest = build_geoclaw_deck(
        _spec(scenario="tsunami", dtopo_file="my_dtopo.tt3"), tmp_path
    )
    assert not (tmp_path / "maketopo.py").exists()
    assert "staged dtopo" in manifest.driver_descriptor
    # the setrun references the staged dtopo file.
    assert "my_dtopo.tt3" in (tmp_path / "setrun.py").read_text()


def test_build_surge_deck_writes_only_setrun(tmp_path: Path):
    manifest = build_geoclaw_deck(_spec(scenario="surge", sea_level_m=2.0), tmp_path)
    assert (tmp_path / "setrun.py").exists()
    assert not (tmp_path / "qinit.xyz").exists()
    assert not (tmp_path / "maketopo.py").exists()
    assert manifest.files_written == ["setrun.py"]


def test_source_lonlat_overrides_centroid_in_qinit(tmp_path: Path):
    src = (-85.40, 29.80)
    build_geoclaw_deck(
        _spec(scenario="dam_break", source_lonlat=list(src)), tmp_path
    )
    xyz = (tmp_path / "qinit.xyz").read_text()
    rows = [r for r in xyz.splitlines() if r.strip()]
    # the perturbation grid is centred on the explicit source -> its x-range
    # straddles src lon, distinct from the AOI centroid (-85.5).
    xs = [float(r.split()[0]) for r in rows]
    assert min(xs) < src[0] < max(xs)
    assert max(xs) < -85.30  # well left of the AOI centroid box if centred on src
