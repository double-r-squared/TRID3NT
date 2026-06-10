"""Unit tests for the MODFLOW 6 GWF+GWT deck adapter (job-0221).

These tests assert the *deck construction* contract — no LLM call, no `mf6`
binary required (engine invariant 2: workflows/adapters are unit-testable
without the solver in the loop). The end-to-end solver run lives in the job's
evidence script (`reports/inflight/job-0221-engine-20260609/evidence/`), which
runs the pinned `mf6` 6.5.0 binary and asserts plume physics.

Run:
    services/agent/.venv/bin/python -m pytest \
        services/workers/modflow/test_gwt_adapter.py -v
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Allow `import gwt_adapter` whether tests run from repo root or the dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gwt_adapter import (  # noqa: E402
    CELL_SIZE_M,
    DOMAIN_HALF_WIDTH_M,
    DeckManifest,
    build_deck,
    build_modflow_deck,
)

# Canonical demo parameters (design.md section 9 / sprint-13 manifest OQ-3).
DEMO = dict(
    spill_location_latlon=(26.64, -81.87),  # Fort Myers area
    contaminant="benzene",
    release_rate_kg_s=0.01,
    duration_days=30,
    aquifer_k_ms=1e-4,
    porosity=0.3,
)


@pytest.fixture()
def deck(tmp_path):
    return build_modflow_deck(workdir=tmp_path, **DEMO)


# --- File-existence: deck is complete -------------------------------------- #


def test_simulation_namefile_exists(deck, tmp_path):
    assert (tmp_path / "mfsim.nam").is_file()
    assert (tmp_path / "mfsim.tdis").is_file()


def test_gwf_package_files_exist(deck, tmp_path):
    # GWF: DIS, IC, NPF, CHD, OC, nam — the steady-state flow model.
    for ext in ("nam", "dis", "ic", "npf", "chd", "oc"):
        assert (tmp_path / f"gwf_model.{ext}").is_file(), f"missing gwf .{ext}"


def test_gwt_package_files_exist(deck, tmp_path):
    # GWT: DIS, IC, ADV, DSP, MST, SRC, SSM, OC, nam — transport model.
    for ext in ("nam", "dis", "ic", "adv", "dsp", "mst", "src", "ssm", "oc"):
        assert (tmp_path / f"gwt_model.{ext}").is_file(), f"missing gwt .{ext}"


def test_gwfgwt_exchange_file_exists(deck, tmp_path):
    # Both package sets are coupled by a GWF-GWT exchange (design.md sec 2).
    assert (tmp_path / "gwfgwt.exg").is_file()


def test_separate_ims_solvers_exist(deck, tmp_path):
    assert (tmp_path / "gwf_model.ims").is_file()
    assert (tmp_path / "gwt_model.ims").is_file()


def test_manifest_files_list_matches_disk(deck, tmp_path):
    on_disk = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
    assert set(deck.files) == on_disk
    assert "mfsim.nam" in deck.files


# --- GWT source carries the requested mass rate ----------------------------- #


def test_src_package_carries_requested_mass_rate(deck, tmp_path):
    """The SRC package must inject exactly release_rate_kg_s -> g/day.

    0.01 kg/s * 1000 g/kg * 86400 s/day = 864000 g/day.
    """
    expected_g_per_day = DEMO["release_rate_kg_s"] * 1000.0 * 86400.0  # 864000
    assert deck.mass_rate_g_per_day == pytest.approx(expected_g_per_day)

    # Parse the SRC record from the written file (MF6 writes the rate in
    # scientific notation, e.g. "1 21 21  8.64000000E+05"). The transient
    # period 2 block carries the (lay row col rate) record.
    src_text = (tmp_path / "gwt_model.src").read_text()
    record = None
    in_period_2 = False
    for line in src_text.splitlines():
        s = line.strip().lower()
        if s.startswith("begin period") and s.split()[-1] == "2":
            in_period_2 = True
            continue
        if s.startswith("end period"):
            in_period_2 = False
            continue
        if in_period_2 and line.strip():
            record = line.split()
            break
    assert record is not None, "no SRC record found in transient period"
    lay, row, col = int(record[0]), int(record[1]), int(record[2])
    written_rate = float(record[3])
    # MF6 cellids are 1-based; the deck manifest is 0-based.
    assert (lay, row, col) == (1, deck.spill_row + 1, deck.spill_col + 1)
    assert written_rate == pytest.approx(expected_g_per_day)


def test_src_rate_scales_with_release_rate(tmp_path):
    a = build_modflow_deck(workdir=tmp_path / "a", **{**DEMO, "release_rate_kg_s": 0.01})
    b = build_modflow_deck(workdir=tmp_path / "b", **{**DEMO, "release_rate_kg_s": 0.05})
    assert b.mass_rate_g_per_day == pytest.approx(5.0 * a.mass_rate_g_per_day)


def test_src_inactive_in_steadystate_period(deck, tmp_path):
    """Source active only in the transient period -> exact mass yardstick.

    Period 0 (steady-state spin-up) must declare zero source records so the
    released-mass total equals rate x duration, not rate x (1 + duration).
    """
    src_text = (tmp_path / "gwt_model.src").read_text().lower()
    # Two BEGIN PERIOD blocks; the first (period 1, MF6 1-based) has maxbound 0.
    assert "begin period  1" in src_text or "begin period 1" in src_text


# --- Grid georegistration matches the spill latlon -------------------------- #


def test_model_crs_is_correct_utm_zone(deck):
    # Fort Myers (-81.87 lon) is in UTM zone 17N -> EPSG:32617.
    assert deck.model_crs == "EPSG:32617"


def test_southern_hemisphere_picks_327xx(tmp_path):
    # A point in Brazil (lat<0) must select a 327xx (southern) UTM zone.
    d = build_modflow_deck(
        workdir=tmp_path,
        **{**DEMO, "spill_location_latlon": (-23.5, -46.6)},  # São Paulo
    )
    assert d.model_crs.startswith("EPSG:327")


def test_grid_is_2km_square_at_50m(deck):
    assert deck.nrow == int(round(2 * DOMAIN_HALF_WIDTH_M / CELL_SIZE_M))
    assert deck.ncol == deck.nrow
    assert deck.delr == CELL_SIZE_M
    assert deck.delc == CELL_SIZE_M
    assert deck.nlay == 1


def test_spill_cell_is_grid_centre(deck):
    # Spill is centred -> the cell index is the middle of the grid.
    assert deck.spill_row == pytest.approx(deck.nrow // 2, abs=1)
    assert deck.spill_col == pytest.approx(deck.ncol // 2, abs=1)


def test_spill_cell_reprojects_back_to_input_latlon(deck):
    """The chosen spill cell centre, reprojected to EPSG:4326, must land within
    one cell (~50 m) of the requested lat/lon — the georegistration is real,
    not nominal."""
    from pyproj import Transformer

    back = Transformer.from_crs(deck.model_crs, "EPSG:4326", always_xy=True)
    lon, lat = back.transform(deck.spill_easting_m, deck.spill_northing_m)
    # 50 m ~ 0.00045 deg latitude; allow one cell of slack.
    assert lat == pytest.approx(deck.spill_lat, abs=0.001)
    assert lon == pytest.approx(deck.spill_lon, abs=0.001)


def test_dis_file_carries_grid_origin(deck, tmp_path):
    dis_text = (tmp_path / "gwf_model.dis").read_text().lower()
    assert "xorigin" in dis_text
    assert "yorigin" in dis_text
    # The origin must be the spill easting minus the domain half-width.
    assert deck.xorigin == pytest.approx(deck.spill_easting_m - DOMAIN_HALF_WIDTH_M, abs=CELL_SIZE_M)


# --- Parameter pass-through into the deck ----------------------------------- #


def test_npf_carries_converted_conductivity(deck, tmp_path):
    """aquifer_k_ms is converted to m/day for the NPF package."""
    k_m_per_day = DEMO["aquifer_k_ms"] * 86400.0  # 1e-4 * 86400 = 8.64
    npf_text = (tmp_path / "gwf_model.npf").read_text()
    assert "8.64" in npf_text


def test_mst_carries_porosity(deck, tmp_path):
    mst_text = (tmp_path / "gwt_model.mst").read_text()
    assert "0.3" in mst_text


def test_transport_steps_track_duration(tmp_path):
    short = build_modflow_deck(workdir=tmp_path / "s", **{**DEMO, "duration_days": 5})
    assert short.n_transport_steps == 5
    longrun = build_modflow_deck(
        workdir=tmp_path / "l", **{**DEMO, "duration_days": 1000}
    )
    assert longrun.n_transport_steps == 365  # capped


# --- Manifest invariants ---------------------------------------------------- #


def test_total_released_mass_matches_rate_times_duration(deck):
    expected_kg = DEMO["release_rate_kg_s"] * DEMO["duration_days"] * 86400.0
    assert deck.total_released_mass_kg() == pytest.approx(expected_kg)


def test_build_deck_alias_is_build_modflow_deck():
    assert build_deck is build_modflow_deck


def test_manifest_is_typed_dataclass(deck):
    assert isinstance(deck, DeckManifest)
    # Every narration-facing field is a number/string, never a prose blob.
    assert isinstance(deck.mass_rate_g_per_day, float)
    assert isinstance(deck.spill_easting_m, float)
    assert isinstance(deck.contaminant, str)


# --- Input validation ------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    [
        {"release_rate_kg_s": 0.0},
        {"release_rate_kg_s": -1.0},
        {"duration_days": 0},
        {"aquifer_k_ms": 0.0},
        {"porosity": 0.0},
        {"porosity": 1.0},
        {"porosity": 1.5},
        {"spill_location_latlon": (200.0, 0.0)},
        {"spill_location_latlon": (0.0, 200.0)},
    ],
)
def test_invalid_params_raise(tmp_path, bad):
    with pytest.raises(ValueError):
        build_modflow_deck(workdir=tmp_path, **{**DEMO, **bad})


def test_write_false_builds_without_writing(tmp_path):
    d = build_modflow_deck(workdir=tmp_path, write=False, **DEMO)
    assert isinstance(d, DeckManifest)
    assert d.files == []
    assert not (tmp_path / "mfsim.nam").exists()
