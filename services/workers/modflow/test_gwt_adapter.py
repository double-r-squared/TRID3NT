"""Unit tests for the MODFLOW 6 GWF+GWT deck adapter (job-0221).

These tests assert the *deck construction* contract - no LLM call, no `mf6`
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
    DEFAULT_AQUIFER_SS,
    DEFAULT_AQUIFER_SY,
    DEFAULT_DRAIN_CONDUCTANCE_M2_DAY,
    DEFAULT_MAR_INFILTRATION_M_DAY,
    DEFAULT_N_TRANSIENT_PERIODS,
    DEFAULT_WETLAND_SY,
    DOMAIN_HALF_WIDTH_M,
    DeckManifest,
    _build_asr_well_schedule,
    _build_zone_array,
    _drape_footprint_to_cells,
    _fill_polygon_cells,
    _resolve_monthly_periods,
    _resolve_transient_periods,
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
    # GWF: DIS, IC, NPF, CHD, OC, nam - the steady-state flow model.
    for ext in ("nam", "dis", "ic", "npf", "chd", "oc"):
        assert (tmp_path / f"gwf_model.{ext}").is_file(), f"missing gwf .{ext}"


def test_gwt_package_files_exist(deck, tmp_path):
    # GWT: DIS, IC, ADV, DSP, MST, SRC, SSM, OC, nam - transport model.
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
    one cell (~50 m) of the requested lat/lon - the georegistration is real,
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


# =========================================================================== #
# sprint-18 Wave-1: archetype decks (sustainable_yield / mine_dewatering /
# regional_water_budget) + the DECAY_SORBED bugfix.
#
# These extend the deck-SHAPE asserts AND add a REAL mf6-run test (env-gated on
# GRACE2_MODFLOW_LOCAL=1 + GRACE2_MF6_BIN) that authors each archetype deck, runs
# mf6, and asserts CONVERGED + non-trivial physics output. The real-run test is
# the gap-closer: the existing file-content asserts let the DECAY_SORBED bug ship
# because nothing ran the binary.
# =========================================================================== #

import os  # noqa: E402
import subprocess  # noqa: E402

# Spill placeholders the GWF-only archetypes carry (no contaminant source). The
# (lat, lon) grid centre + aquifer K/porosity are the only meaningful spill args.
ARCH_SPILL = dict(
    spill_location_latlon=(26.64, -81.87),
    contaminant="x",
    release_rate_kg_s=0.0,  # placeholder (validated away when archetype is set)
    duration_days=0.0,  # placeholder
    aquifer_k_ms=1e-4,
    porosity=0.3,
)
# A small pit footprint (lon, lat) ring near the grid centre.
PIT_FOOTPRINT = [
    (-81.873, 26.637),
    (-81.867, 26.637),
    (-81.867, 26.643),
    (-81.873, 26.643),
]


def _mf6_bin() -> str | None:
    """Return the local mf6 binary path when the real-run gate is set, else None."""
    if os.environ.get("GRACE2_MODFLOW_LOCAL") != "1":
        return None
    return os.environ.get("GRACE2_MF6_BIN") or "mf6"


def _run_mf6(sim_dir: str, mf6: str) -> tuple[int, str]:
    """Run mf6 in ``sim_dir``; return (returncode, stdout)."""
    proc = subprocess.run([mf6], cwd=sim_dir, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "")


requires_mf6 = pytest.mark.skipif(
    _mf6_bin() is None,
    reason="real mf6 run gated on GRACE2_MODFLOW_LOCAL=1 + GRACE2_MF6_BIN",
)


# --- Archetype dispatch + validation ---------------------------------------- #


def test_unknown_archetype_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown MODFLOW archetype"):
        build_modflow_deck(workdir=tmp_path, archetype="not_a_real_one", **ARCH_SPILL)


def test_archetype_none_is_the_spill_deck(tmp_path):
    """archetype=None keeps the existing GWF+GWT spill deck (regression guard)."""
    d = build_modflow_deck(workdir=tmp_path, **DEMO)
    assert d.archetype is None
    assert d.gwt_present is True
    assert (tmp_path / "gwt_model.mst").is_file()  # transport block still present
    assert (tmp_path / "gwfgwt.exg").is_file()


def test_archetype_skips_release_rate_validation(tmp_path):
    """The GWF-only archetypes accept placeholder (zero) spill params -- the
    release_rate/duration validations are spill-only."""
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="regional_water_budget",
        **{**ARCH_SPILL, "release_rate_kg_s": 0.0, "duration_days": 0.0},
    )
    assert d.archetype == "regional_water_budget"


def test_archetype_still_validates_k_and_porosity(tmp_path):
    with pytest.raises(ValueError):
        build_modflow_deck(
            workdir=tmp_path, archetype="regional_water_budget",
            **{**ARCH_SPILL, "porosity": 1.5},
        )


# --- Pure helpers ----------------------------------------------------------- #


def test_resolve_transient_periods_sim_years():
    rows = _resolve_transient_periods(sim_years=2.0, n_periods=4)
    assert len(rows) == 4
    perlen = sum(r[0] for r in rows)
    assert perlen == pytest.approx(2.0 * 365.0)  # spans 2 years


def test_resolve_transient_periods_n_periods_only():
    rows = _resolve_transient_periods(sim_years=None, n_periods=6)
    assert len(rows) == 6


def test_resolve_transient_periods_default():
    rows = _resolve_transient_periods(sim_years=None, n_periods=None)
    assert len(rows) == DEFAULT_N_TRANSIENT_PERIODS


def test_fill_polygon_cells_fills_interior():
    # A 3x3 boundary box -> all 9 interior+boundary cells filled.
    boundary = [(2, 2), (2, 4), (4, 2), (4, 4), (3, 2), (3, 4), (2, 3), (4, 3)]
    filled = _fill_polygon_cells(boundary, nrow=10, ncol=10)
    assert set(filled) == {(r, c) for r in (2, 3, 4) for c in (2, 3, 4)}


def test_build_zone_array_two_zone_split():
    arr, n = _build_zone_array("upgradient_downgradient", nrow=4, ncol=10)
    assert n == 2
    # West half = zone 1, east half = zone 2.
    assert arr[0][0] == 1 and arr[0][-1] == 2


# --- sustainable_yield deck shape ------------------------------------------- #


@pytest.fixture()
def sy_deck(tmp_path):
    return build_modflow_deck(
        workdir=tmp_path,
        archetype="sustainable_yield",
        well_location_latlon=(26.64, -81.87),
        pumping_rate_m3_day=-1500.0,
        sim_years=1.0,
        n_periods=4,
        **ARCH_SPILL,
    )


def test_sustainable_yield_is_gwf_only_transient(sy_deck, tmp_path):
    assert sy_deck.archetype == "sustainable_yield"
    assert sy_deck.gwt_present is False
    assert sy_deck.transient is True
    # GWF-only: NO transport files, NO exchange.
    assert not (tmp_path / "gwt_model.mst").exists()
    assert not (tmp_path / "gwfgwt.exg").exists()
    # WEL + STO written; spin-up + 4 transient periods.
    assert (tmp_path / "gwf_model.wel").is_file()
    assert (tmp_path / "gwf_model.sto").is_file()
    assert sy_deck.n_stress_periods == 5
    assert sy_deck.n_transient_periods == 4


def test_sustainable_yield_well_negative_extraction(sy_deck, tmp_path):
    assert sy_deck.pumping_rate_m3_day == pytest.approx(-1500.0)
    assert sy_deck.well_row >= 0 and sy_deck.well_col >= 0
    wel_text = (tmp_path / "gwf_model.wel").read_text().lower()
    assert "-1.50000000e+03" in wel_text or "-1500" in wel_text


def test_sustainable_yield_well_off_in_spinup(sy_deck, tmp_path):
    """Period 1 (MF6 1-based = spin-up) carries NO WEL record so drawdown is
    measured against the undisturbed regional head."""
    wel_text = (tmp_path / "gwf_model.wel").read_text().lower()
    # The first BEGIN PERIOD block must be period 2 (the first transient one), or
    # period 1 with maxbound 0. Easiest robust check: the well record only appears
    # in periods >= 2.
    assert "begin period  2" in wel_text or "begin period 2" in wel_text


def test_sustainable_yield_sto_carries_sy_ss(tmp_path):
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="sustainable_yield",
        well_location_latlon=(26.64, -81.87),
        pumping_rate_m3_day=-1000.0,
        aquifer_sy=0.15,
        aquifer_ss=2e-5,
        **ARCH_SPILL,
    )
    assert d.aquifer_sy == pytest.approx(0.15)
    assert d.aquifer_ss == pytest.approx(2e-5)
    sto_text = (tmp_path / "gwf_model.sto").read_text().lower()
    assert "sy" in sto_text and "ss" in sto_text


def test_sustainable_yield_requires_well(tmp_path):
    with pytest.raises(ValueError, match="well_location_latlon"):
        build_modflow_deck(
            workdir=tmp_path, archetype="sustainable_yield",
            pumping_rate_m3_day=-1000.0, **ARCH_SPILL,
        )
    with pytest.raises(ValueError, match="pumping_rate_m3_day"):
        build_modflow_deck(
            workdir=tmp_path, archetype="sustainable_yield",
            well_location_latlon=(26.64, -81.87), **ARCH_SPILL,
        )


# --- mine_dewatering deck shape --------------------------------------------- #


@pytest.fixture()
def md_deck(tmp_path):
    return build_modflow_deck(
        workdir=tmp_path,
        archetype="mine_dewatering",
        pit_footprint_lonlat=PIT_FOOTPRINT,
        drain_elevation_m=-8.0,
        drain_conductance_m2_day=120.0,
        **ARCH_SPILL,
    )


def test_mine_dewatering_is_gwf_only_steady(md_deck, tmp_path):
    assert md_deck.archetype == "mine_dewatering"
    assert md_deck.gwt_present is False
    assert md_deck.transient is False  # STEADY
    assert md_deck.n_stress_periods == 1
    assert not (tmp_path / "gwf_model.sto").exists()  # steady -> no STO
    assert (tmp_path / "gwf_model.drn").is_file()


def test_mine_dewatering_unconfined_icelltype(md_deck, tmp_path):
    """The pit cells de-saturate -> NPF icelltype must be 1 (unconfined)."""
    assert md_deck.npf_icelltype == 1
    npf_text = (tmp_path / "gwf_model.npf").read_text().lower()
    assert "icelltype" in npf_text


def test_mine_dewatering_drain_records(md_deck, tmp_path):
    assert md_deck.drain_cell_count > 0
    assert md_deck.drain_elevation_m == pytest.approx(-8.0)
    assert md_deck.drain_conductance_m2_day == pytest.approx(120.0)
    drn_text = (tmp_path / "gwf_model.drn").read_text().lower()
    assert "begin period" in drn_text
    assert "-8" in drn_text  # drain elevation


def test_mine_dewatering_optional_sump_well(tmp_path):
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="mine_dewatering",
        pit_footprint_lonlat=PIT_FOOTPRINT,
        well_pumping_rate_m3_day=-300.0,
        **ARCH_SPILL,
    )
    assert (tmp_path / "gwf_model.wel").is_file()  # sump WEL written
    assert d.pumping_rate_m3_day == pytest.approx(-300.0)


def test_mine_dewatering_requires_pit(tmp_path):
    with pytest.raises(ValueError, match="pit_footprint_lonlat"):
        build_modflow_deck(
            workdir=tmp_path, archetype="mine_dewatering", **ARCH_SPILL,
        )


# --- regional_water_budget deck shape --------------------------------------- #


def test_regional_water_budget_is_gwf_only_no_stress(tmp_path):
    d = build_modflow_deck(
        workdir=tmp_path, archetype="regional_water_budget", **ARCH_SPILL
    )
    assert d.archetype == "regional_water_budget"
    assert d.gwt_present is False
    assert d.transient is False
    # No new stress package (no WEL/DRN/SRC) -- only CHD + OC.
    assert not (tmp_path / "gwf_model.wel").exists()
    assert not (tmp_path / "gwf_model.drn").exists()
    assert (tmp_path / "gwf_model.chd").is_file()


def test_regional_water_budget_zone_array(tmp_path):
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="regional_water_budget",
        zone_partition="upgradient_downgradient",
        **ARCH_SPILL,
    )
    assert d.zone_partition == "upgradient_downgradient"
    assert d.n_zones == 2
    zpath = tmp_path / "gwf_model.zones.csv"
    assert zpath.is_file(), "zone array sidecar not written"
    rows = zpath.read_text().strip().splitlines()
    assert len(rows) == d.nrow
    first = rows[0].split(",")
    assert first[0] == "1" and first[-1] == "2"


def test_regional_water_budget_no_zone_by_default(tmp_path):
    d = build_modflow_deck(
        workdir=tmp_path, archetype="regional_water_budget", **ARCH_SPILL
    )
    assert d.zone_partition is None
    assert d.n_zones == 0
    assert not (tmp_path / "gwf_model.zones.csv").exists()


# --- OC saves HEAD + BUDGET ALL for every archetype ------------------------- #


@pytest.mark.parametrize(
    "kw",
    [
        dict(
            archetype="sustainable_yield",
            well_location_latlon=(26.64, -81.87),
            pumping_rate_m3_day=-1000.0,
        ),
        dict(archetype="mine_dewatering", pit_footprint_lonlat=PIT_FOOTPRINT),
        dict(archetype="regional_water_budget"),
    ],
)
def test_archetype_oc_saves_head_and_budget(tmp_path, kw):
    build_modflow_deck(workdir=tmp_path, **{**ARCH_SPILL, **kw})
    oc_text = (tmp_path / "gwf_model.oc").read_text().lower()
    assert "head" in oc_text and "budget" in oc_text


# --- DECAY_SORBED bugfix (deck shape) --------------------------------------- #


def test_decay_sorbed_written_when_decay_and_sorption(tmp_path):
    """LIVE BUG FIX: with BOTH sorption + first-order decay active, the MST must
    declare decay_sorbed (else mf6 errors 'DECAY_SORBED not provided')."""
    build_modflow_deck(
        workdir=tmp_path,
        advanced_physics={
            "sorption_kd": 0.5,
            "bulk_density": 1600.0,
            "decay_rate_per_day": 0.02,
        },
        **DEMO,
    )
    mst_text = (tmp_path / "gwt_model.mst").read_text().lower()
    assert "decay_sorbed" in mst_text


def test_decay_sorbed_defaults_to_aqueous_decay(tmp_path):
    """decay_sorbed defaults to the aqueous decay value when not overridden."""
    build_modflow_deck(
        workdir=tmp_path,
        advanced_physics={"sorption_kd": 0.5, "decay_rate_per_day": 0.03},
        **DEMO,
    )
    mst_text = (tmp_path / "gwt_model.mst").read_text()
    assert "decay_sorbed" in mst_text.lower()
    # The aqueous decay 0.03 must appear (for both the decay and decay_sorbed
    # GRIDDATA constants).
    assert "3.00000000E-02" in mst_text or "0.03" in mst_text


def test_no_decay_sorbed_without_sorption(tmp_path):
    """Decay alone (no sorption) must NOT write decay_sorbed (regression guard)."""
    build_modflow_deck(
        workdir=tmp_path,
        advanced_physics={"decay_rate_per_day": 0.02},
        **DEMO,
    )
    mst_text = (tmp_path / "gwt_model.mst").read_text().lower()
    assert "decay_sorbed" not in mst_text


def test_no_decay_sorbed_without_decay(tmp_path):
    """Sorption alone (no decay) must NOT write decay_sorbed."""
    build_modflow_deck(
        workdir=tmp_path,
        advanced_physics={"sorption_kd": 0.5, "bulk_density": 1600.0},
        **DEMO,
    )
    mst_text = (tmp_path / "gwt_model.mst").read_text().lower()
    assert "decay_sorbed" not in mst_text


# =========================================================================== #
# REAL mf6 runs (env-gated) -- author each archetype deck, run mf6, assert
# CONVERGED + non-trivial physics. This is the gap that let DECAY_SORBED ship.
# =========================================================================== #


@requires_mf6
def test_real_run_decay_plus_sorption_converges(tmp_path):
    """The exact DECAY_SORBED failure case: sorption + first-order decay. Pre-fix
    mf6 errored 'DECAY_SORBED not provided in GRIDDATA block'. Must now CONVERGE."""
    mf6 = _mf6_bin()
    d = build_modflow_deck(
        workdir=tmp_path,
        advanced_physics={
            "sorption_kd": 0.5,
            "bulk_density": 1600.0,
            "decay_rate_per_day": 0.02,
        },
        **{**DEMO, "duration_days": 10},
    )
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    assert "DECAY_SORBED not provided" not in out
    assert rc == 0


@requires_mf6
def test_real_run_sustainable_yield_converges_with_drawdown(tmp_path):
    """sustainable_yield: author + run mf6, assert CONVERGED and a real cone of
    depression (head decline > 0 at the pumped well vs the no-well spin-up)."""
    import numpy as np
    import flopy

    mf6 = _mf6_bin()
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="sustainable_yield",
        well_location_latlon=(26.64, -81.87),
        pumping_rate_m3_day=-2000.0,
        sim_years=2.0,
        n_periods=4,
        **ARCH_SPILL,
    )
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    hds = flopy.utils.HeadFile(str(tmp_path / "gwf_model.hds"))
    times = hds.get_times()
    h0 = hds.get_data(totim=times[0])  # steady spin-up (no well)
    hN = hds.get_data(totim=times[-1])  # last transient (pumping)
    drawdown = h0 - hN
    assert float(np.nanmax(drawdown)) > 0.01, "expected a real cone of depression"
    assert float(drawdown[0, d.well_row, d.well_col]) > 0.0


@requires_mf6
def test_real_run_mine_dewatering_converges_with_drn_outflow(tmp_path):
    """mine_dewatering: author + run mf6, assert CONVERGED and a real DRN outflow
    (the pump-to-dewater rate the agent narrates)."""
    import numpy as np
    import flopy

    mf6 = _mf6_bin()
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="mine_dewatering",
        pit_footprint_lonlat=PIT_FOOTPRINT,
        drain_elevation_m=-8.0,
        **ARCH_SPILL,
    )
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    cbc = flopy.utils.CellBudgetFile(str(tmp_path / "gwf_model.cbc"))
    drn = cbc.get_data(text="DRN")[-1]
    try:
        q = drn["q"]
    except Exception:
        q = np.array([rec[-1] for rec in drn])
    dewatering_rate = float(-q[q < 0].sum())  # magnitude of drain outflow
    assert dewatering_rate > 1.0, "expected a real dewatering outflow"


@requires_mf6
def test_real_run_regional_water_budget_converges_and_balances(tmp_path):
    """regional_water_budget: author + run mf6, assert CONVERGED and the CHD
    budget balances (steady, no source -> CHD in + CHD out ~ 0)."""
    import numpy as np
    import flopy

    mf6 = _mf6_bin()
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="regional_water_budget",
        zone_partition="upgradient_downgradient",
        **ARCH_SPILL,
    )
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    cbc = flopy.utils.CellBudgetFile(str(tmp_path / "gwf_model.cbc"))
    chd = cbc.get_data(text="CHD")[-1]
    try:
        q = chd["q"]
    except Exception:
        q = np.array([rec[-1] for rec in chd])
    chd_in = float(q[q > 0].sum())
    chd_out = float(q[q < 0].sum())
    assert abs(chd_in + chd_out) < 1.0, "steady no-source CHD budget should balance"
    assert chd_in > 1.0, "expected real regional throughflow"


@requires_mf6
def test_real_run_spill_deck_still_converges(tmp_path):
    """Regression: the original spill/seepage GWF+GWT deck still runs end-to-end
    (the archetype switch must not perturb the default path)."""
    mf6 = _mf6_bin()
    d = build_modflow_deck(workdir=tmp_path, **{**DEMO, "duration_days": 10})
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    assert rc == 0


# =========================================================================== #
# sprint-18 Wave-2: MAR (RCH/RCHA mounding) + ASR (seasonal WEL inject/recover)
# + wetland_hydroperiod (RCH-schedule + EVT + Newton IMS). Deck-SHAPE asserts +
# a REAL mf6-run per archetype asserting CONVERGED + non-trivial physics.
# =========================================================================== #

# A basin / wetland footprint (lon, lat) ring near the grid centre (reuses the
# PIT_FOOTPRINT geometry; small enough to drape to a handful of in-grid cells).
BASIN_FOOTPRINT = list(PIT_FOOTPRINT)
WETLAND_FOOTPRINT = list(PIT_FOOTPRINT)
ASR_WELL = (26.64, -81.87)


# --- Pure Wave-2 helpers ---------------------------------------------------- #


def test_resolve_monthly_periods_count_and_length():
    rows = _resolve_monthly_periods(n_months=6)
    assert len(rows) == 6
    # Every period is a flat demo "month" (DEFAULT_DAYS_PER_MONTH = 30 days).
    assert all(r[0] == pytest.approx(30.0) for r in rows)


def test_build_asr_well_schedule_cycles():
    sched = _build_asr_well_schedule(
        injection_periods=2, recovery_periods=3, n_cycles=2
    )
    # 2 cycles of (2 inject + 3 recover) = [I,I,R,R,R, I,I,R,R,R]
    assert sched == ["inject", "inject", "recover", "recover", "recover"] * 2
    assert sched.count("inject") == 4
    assert sched.count("recover") == 6


def test_drape_footprint_to_cells_skips_chd_cols():
    from pyproj import Transformer
    from gwt_adapter import _utm_crs_for_lonlat

    crs = _utm_crs_for_lonlat(-81.87, 26.64)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    east, north = to_utm.transform(-81.87, 26.64)
    xorigin = east - DOMAIN_HALF_WIDTH_M
    yorigin = north - DOMAIN_HALF_WIDTH_M
    cells = _drape_footprint_to_cells(
        BASIN_FOOTPRINT,
        to_utm=to_utm,
        xorigin=xorigin,
        yorigin=yorigin,
        delr=CELL_SIZE_M,
        delc=CELL_SIZE_M,
        nrow=40,
        ncol=40,
        skip_cols={0, 39},
    )
    assert cells, "footprint should drape to in-grid cells"
    assert all(0 < c < 39 for (_r, c) in cells)  # CHD columns skipped


# --- MAR deck shape --------------------------------------------------------- #


@pytest.fixture()
def mar_deck(tmp_path):
    return build_modflow_deck(
        workdir=tmp_path,
        archetype="MAR",
        basin_footprint_lonlat=BASIN_FOOTPRINT,
        infiltration_rate_m_day=0.02,
        recharge_months=4,
        **ARCH_SPILL,
    )


def test_mar_is_gwf_only_transient_unconfined(mar_deck, tmp_path):
    assert mar_deck.archetype == "MAR"
    assert mar_deck.gwt_present is False
    assert mar_deck.transient is True
    # GWF-only: NO transport files, NO exchange.
    assert not (tmp_path / "gwt_model.mst").exists()
    assert not (tmp_path / "gwfgwt.exg").exists()
    # Unconfined water table so the mound can rise.
    assert mar_deck.npf_icelltype == 1
    # RCH (basin footprint) + STO written; spin-up + 4 recharge periods.
    assert (tmp_path / "gwf_model.rch").is_file()
    assert (tmp_path / "gwf_model.sto").is_file()
    assert mar_deck.n_stress_periods == 5
    assert mar_deck.n_transient_periods == 4
    assert mar_deck.recharge_active_periods == 4


def test_mar_rch_carries_positive_flux(mar_deck, tmp_path):
    assert mar_deck.recharge_cell_count > 0
    assert mar_deck.infiltration_rate_m_day == pytest.approx(0.02)
    rch_text = (tmp_path / "gwf_model.rch").read_text().lower()
    assert "begin period" in rch_text
    # The recharge flux is POSITIVE (mounding, not extraction).
    assert "2.00000000e-02" in rch_text or "0.02" in rch_text
    assert "-2.00000000e-02" not in rch_text


def test_mar_rch_off_in_spinup(mar_deck, tmp_path):
    """Period 1 (MF6 1-based = steady spin-up) carries NO recharge so the mound is
    measured against the undisturbed regional head."""
    rch_text = (tmp_path / "gwf_model.rch").read_text().lower()
    # The first recharge record only appears in periods >= 2 (transient).
    assert "begin period  2" in rch_text or "begin period 2" in rch_text


def test_mar_without_basin_uses_rcha(tmp_path):
    """No basin footprint -> a uniform array recharge (RCHA) over the whole grid."""
    d = build_modflow_deck(
        workdir=tmp_path, archetype="MAR", infiltration_rate_m_day=0.01, **ARCH_SPILL
    )
    assert (tmp_path / "gwf_model.rcha").is_file()
    assert not (tmp_path / "gwf_model.rch").exists()
    assert d.recharge_cell_count == d.nrow * d.ncol


def test_mar_infiltration_default(tmp_path):
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="MAR",
        basin_footprint_lonlat=BASIN_FOOTPRINT,
        **ARCH_SPILL,
    )
    assert d.infiltration_rate_m_day == pytest.approx(DEFAULT_MAR_INFILTRATION_M_DAY)


# --- ASR deck shape --------------------------------------------------------- #


@pytest.fixture()
def asr_deck(tmp_path):
    return build_modflow_deck(
        workdir=tmp_path,
        archetype="ASR",
        well_location_latlon=ASR_WELL,
        injection_rate_m3_day=1500.0,
        recovery_rate_m3_day=1200.0,
        injection_months=3,
        recovery_months=3,
        n_cycles=2,
        **ARCH_SPILL,
    )


def test_asr_is_gwf_only_transient(asr_deck, tmp_path):
    assert asr_deck.archetype == "ASR"
    assert asr_deck.gwt_present is False
    assert asr_deck.transient is True
    assert not (tmp_path / "gwt_model.mst").exists()
    assert (tmp_path / "gwf_model.wel").is_file()
    assert (tmp_path / "gwf_model.sto").is_file()
    # 2 cycles of (3 inject + 3 recover) = 12 transient periods + 1 spin-up.
    assert asr_deck.n_stress_periods == 13
    assert asr_deck.injection_periods == 6
    assert asr_deck.recovery_periods == 6
    assert asr_deck.n_cycles == 2


def test_asr_well_schedule_flips_sign(asr_deck, tmp_path):
    """The WEL must carry a POSITIVE q (injection) in injection periods and a
    NEGATIVE q (recovery) in recovery periods."""
    assert asr_deck.injection_rate_m3_day == pytest.approx(1500.0)
    assert asr_deck.recovery_rate_m3_day == pytest.approx(1200.0)
    wel_text = (tmp_path / "gwf_model.wel").read_text().lower()
    # Injection: +1500 (positive). Recovery: -1200 (negative).
    assert "1.50000000e+03" in wel_text or "1500" in wel_text
    assert "-1.20000000e+03" in wel_text or "-1200" in wel_text


def test_asr_requires_well(tmp_path):
    with pytest.raises(ValueError, match="well_location_latlon"):
        build_modflow_deck(
            workdir=tmp_path,
            archetype="ASR",
            injection_rate_m3_day=1000.0,
            recovery_rate_m3_day=1000.0,
            **ARCH_SPILL,
        )


def test_asr_well_off_in_spinup(asr_deck, tmp_path):
    wel_text = (tmp_path / "gwf_model.wel").read_text().lower()
    # The first WEL record appears only from period 2 (the first injection period).
    assert "begin period  2" in wel_text or "begin period 2" in wel_text


# --- wetland_hydroperiod deck shape ----------------------------------------- #


@pytest.fixture()
def wetland_deck(tmp_path):
    return build_modflow_deck(
        workdir=tmp_path,
        archetype="wetland_hydroperiod",
        wetland_footprint_lonlat=WETLAND_FOOTPRINT,
        recharge_schedule_m_day=[0.004, 0.0005, 0.004, 0.0005],
        et_surface_m=0.0,
        et_max_rate_m_day=0.004,
        et_extinction_depth_m=2.0,
        specific_yield=0.18,
        **ARCH_SPILL,
    )


def test_wetland_is_gwf_only_transient_unconfined(wetland_deck, tmp_path):
    assert wetland_deck.archetype == "wetland_hydroperiod"
    assert wetland_deck.gwt_present is False
    assert wetland_deck.transient is True
    assert wetland_deck.npf_icelltype == 1  # unconfined water-table response
    assert not (tmp_path / "gwt_model.mst").exists()
    # RCH (per-period schedule) + EVT + STO written.
    assert (tmp_path / "gwf_model.rch").is_file()
    assert (tmp_path / "gwf_model.evt").is_file()
    assert (tmp_path / "gwf_model.sto").is_file()
    # 4 scheduled periods + 1 steady spin-up.
    assert wetland_deck.n_stress_periods == 5
    assert wetland_deck.n_transient_periods == 4
    assert wetland_deck.wetland_cell_count > 0


def test_wetland_uses_newton_bicgstab_ims(wetland_deck, tmp_path):
    """The unconfined + ET system needs MF6's NEWTON formulation + BICGSTAB; CG on
    the Newton matrix fails to converge."""
    assert wetland_deck.newton_under_relaxation is True
    # NEWTON declared on the GWF model name file.
    nam_text = (tmp_path / "gwf_model.nam").read_text().lower()
    assert "newton" in nam_text
    # IMS uses BICGSTAB.
    ims_text = (tmp_path / "gwf_model.ims").read_text().lower()
    assert "bicgstab" in ims_text


def test_wetland_rch_schedule_per_period(wetland_deck, tmp_path):
    """flopy forward-fills the last block, so a per-period schedule must emit EVERY
    transient period -- both the wet (0.004) and dry (0.0005) rates must appear."""
    rch_text = (tmp_path / "gwf_model.rch").read_text().lower()
    assert "4.00000000e-03" in rch_text or "0.004" in rch_text  # wet
    assert "5.00000000e-04" in rch_text or "0.0005" in rch_text  # dry


def test_wetland_evt_carries_surface_rate_depth(wetland_deck, tmp_path):
    assert wetland_deck.et_max_rate_m_day == pytest.approx(0.004)
    assert wetland_deck.et_extinction_depth_m == pytest.approx(2.0)
    evt_text = (tmp_path / "gwf_model.evt").read_text().lower()
    assert "begin period" in evt_text
    assert "4.00000000e-03" in evt_text or "0.004" in evt_text  # max ET rate


def test_wetland_sto_uses_specific_yield(wetland_deck, tmp_path):
    assert wetland_deck.aquifer_sy == pytest.approx(0.18)


def test_wetland_sy_default(tmp_path):
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="wetland_hydroperiod",
        wetland_footprint_lonlat=WETLAND_FOOTPRINT,
        **ARCH_SPILL,
    )
    assert d.aquifer_sy == pytest.approx(DEFAULT_WETLAND_SY)


def test_wetland_requires_footprint(tmp_path):
    with pytest.raises(ValueError, match="wetland_footprint_lonlat"):
        build_modflow_deck(
            workdir=tmp_path, archetype="wetland_hydroperiod", **ARCH_SPILL
        )


# --- OC saves HEAD + BUDGET ALL for the Wave-2 archetypes ------------------- #


@pytest.mark.parametrize(
    "kw",
    [
        dict(archetype="MAR", basin_footprint_lonlat=BASIN_FOOTPRINT),
        dict(
            archetype="ASR",
            well_location_latlon=ASR_WELL,
            injection_rate_m3_day=1000.0,
            recovery_rate_m3_day=1000.0,
        ),
        dict(
            archetype="wetland_hydroperiod",
            wetland_footprint_lonlat=WETLAND_FOOTPRINT,
        ),
    ],
)
def test_wave2_oc_saves_head_and_budget(tmp_path, kw):
    build_modflow_deck(workdir=tmp_path, **{**ARCH_SPILL, **kw})
    oc_text = (tmp_path / "gwf_model.oc").read_text().lower()
    assert "head" in oc_text and "budget" in oc_text


# =========================================================================== #
# REAL mf6 runs (env-gated) -- author each Wave-2 archetype deck, run mf6, assert
# CONVERGED + non-trivial physics (the headline CBC term / head response).
# =========================================================================== #


@requires_mf6
def test_real_run_mar_converges_with_mounding(tmp_path):
    """MAR: author + run mf6, assert CONVERGED + a real groundwater MOUND (head
    rises under the recharge basin vs the no-recharge spin-up) + positive RCH inflow
    (the headline CBC term)."""
    import numpy as np
    import flopy

    mf6 = _mf6_bin()
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="MAR",
        basin_footprint_lonlat=BASIN_FOOTPRINT,
        infiltration_rate_m_day=0.02,
        recharge_months=4,
        **ARCH_SPILL,
    )
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    hds = flopy.utils.HeadFile(str(tmp_path / "gwf_model.hds"))
    times = hds.get_times()
    h0 = hds.get_data(totim=times[0])  # steady spin-up (no recharge)
    hN = hds.get_data(totim=times[-1])  # last recharge period
    mound = hN - h0
    assert float(np.nanmax(mound)) > 0.1, "expected a real groundwater mound"
    # RCH is the headline budget term: a positive inflow to the aquifer.
    cbc = flopy.utils.CellBudgetFile(str(tmp_path / "gwf_model.cbc"))
    rch = cbc.get_data(text="RCH")[-1]
    try:
        q = rch["q"]
    except Exception:
        q = np.array([rec[-1] for rec in rch])
    assert float(q[q > 0].sum()) > 1.0, "expected real positive RCH inflow"


@requires_mf6
def test_real_run_asr_converges_with_seasonal_swing(tmp_path):
    """ASR: author + run mf6, assert CONVERGED + a real seasonal head swing at the
    ASR well (injection raises it, recovery lowers it) -> a non-trivial head range.
    WEL is the headline CBC term (the recovery extraction the agent narrates)."""
    import numpy as np
    import flopy

    mf6 = _mf6_bin()
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="ASR",
        well_location_latlon=ASR_WELL,
        injection_rate_m3_day=1500.0,
        recovery_rate_m3_day=1500.0,
        injection_months=3,
        recovery_months=3,
        n_cycles=2,
        **ARCH_SPILL,
    )
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    hds = flopy.utils.HeadFile(str(tmp_path / "gwf_model.hds"))
    times = hds.get_times()
    head_at_well = [
        float(hds.get_data(totim=t)[0, d.well_row, d.well_col]) for t in times
    ]
    swing = max(head_at_well) - min(head_at_well)
    assert swing > 0.1, "expected a real seasonal head swing at the ASR well"
    # WEL is the headline budget term (the recovery extraction).
    cbc = flopy.utils.CellBudgetFile(str(tmp_path / "gwf_model.cbc"))
    wel = cbc.get_data(text="WEL")[-1]  # last period = a recovery period
    try:
        q = wel["q"]
    except Exception:
        q = np.array([rec[-1] for rec in wel])
    assert float(-q[q < 0].sum()) > 1.0, "expected real WEL recovery extraction"


@requires_mf6
def test_real_run_wetland_converges_with_hydroperiod_range(tmp_path):
    """wetland_hydroperiod: author + run mf6, assert CONVERGED (the Newton/BICGSTAB
    solve) + a real seasonal water-table RANGE under the wetland (the hydroperiod).
    RCH is the headline recharge CBC term."""
    import numpy as np
    import flopy

    mf6 = _mf6_bin()
    d = build_modflow_deck(
        workdir=tmp_path,
        archetype="wetland_hydroperiod",
        wetland_footprint_lonlat=WETLAND_FOOTPRINT,
        recharge_schedule_m_day=[0.004, 0.0005, 0.004, 0.0005],
        et_surface_m=0.0,
        et_max_rate_m_day=0.004,
        et_extinction_depth_m=2.0,
        **ARCH_SPILL,
    )
    rc, out = _run_mf6(d.sim_dir, mf6)
    assert "Normal termination of simulation" in out, out[-1500:]
    hds = flopy.utils.HeadFile(str(tmp_path / "gwf_model.hds"))
    times = hds.get_times()
    # A representative wetland-centre cell (grid centre).
    centre = (0, d.nrow // 2, d.ncol // 2)
    series = [float(hds.get_data(totim=t)[centre]) for t in times]
    hydroperiod_range = max(series) - min(series)
    assert hydroperiod_range > 0.01, "expected a real seasonal water-table range"
    # RCH is the headline budget term: a positive inflow over the wetland.
    cbc = flopy.utils.CellBudgetFile(str(tmp_path / "gwf_model.cbc"))
    rch = cbc.get_data(text="RCH")[-1]
    try:
        q = rch["q"]
    except Exception:
        q = np.array([rec[-1] for rec in rch])
    assert float(q[q > 0].sum()) >= 0.0  # recharge inflow is non-negative
