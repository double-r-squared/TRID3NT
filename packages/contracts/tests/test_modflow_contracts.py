"""Validation + round-trip tests for MODFLOW groundwater contracts (sprint-13
Stage 1, §2.3 MODFLOW integration / OQ-9 mf6-gwt).

Covers:
- ``MODFLOWRunArgs`` validation bounds (positive rates/durations, porosity
  0-1, lat/lon ranges, contaminant non-empty) and TENTATIVE OQ-3 defaults.
- ``PlumeLayerURI`` round-trip JSON serialization and inheritance from
  ``LayerURI`` (it still maps onto map-command load-layer; the two plume
  scalars are present and bounded >= 0).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from grace2_contracts import (
    ASRLayerURI,
    BudgetPartitionLayerURI,
    DewaterLayerURI,
    DrawdownLayerURI,
    HydroperiodLayerURI,
    MODFLOWRunArgs,
    MoundingLayerURI,
    PlumeLayerURI,
)
from grace2_contracts.execution import LayerURI
from grace2_contracts.envelope import TemporalConfig
from grace2_contracts.modflow_contracts import (
    DEFAULT_AQUIFER_K_MS,
    DEFAULT_AQUIFER_SS,
    DEFAULT_AQUIFER_SY,
    DEFAULT_POROSITY,
    DEFAULT_WETLAND_SY,
)


# --------------------------------------------------------------------------- #
# MODFLOWRunArgs - defaults (OQ-3 TENTATIVE demo parameterization)
# --------------------------------------------------------------------------- #


def test_modflow_run_args_minimal_applies_oq3_defaults() -> None:
    """K and porosity default to the TENTATIVE OQ-3 demo values."""
    args = MODFLOWRunArgs(
        spill_location_latlon=(26.6, -81.9),  # Fort-Myers-ish (lat, lon)
        contaminant="benzene",
        release_rate_kg_s=0.5,
        duration_days=3.0,
    )
    assert args.aquifer_k_ms == DEFAULT_AQUIFER_K_MS == 1e-4
    assert args.porosity == DEFAULT_POROSITY == 0.3
    assert args.schema_version == "v2"
    # River-coupling fields default off -> the deck stays the pure-spill deck.
    assert args.river_geometry_uri is None
    assert args.along_river_source is False


def test_modflow_run_args_explicit_overrides_defaults() -> None:
    args = MODFLOWRunArgs(
        spill_location_latlon=(40.0, -100.0),
        contaminant="TCE",
        release_rate_kg_s=1.0,
        duration_days=10.0,
        aquifer_k_ms=5e-5,
        porosity=0.25,
    )
    assert args.aquifer_k_ms == 5e-5
    assert args.porosity == 0.25


# --------------------------------------------------------------------------- #
# MODFLOWRunArgs - validation bounds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("rate", [0.0, -1.0, -0.001])
def test_release_rate_must_be_positive(rate: float) -> None:
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=(26.6, -81.9),
            contaminant="benzene",
            release_rate_kg_s=rate,
            duration_days=3.0,
        )


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_duration_must_be_positive(duration: float) -> None:
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=(26.6, -81.9),
            contaminant="benzene",
            release_rate_kg_s=0.5,
            duration_days=duration,
        )


@pytest.mark.parametrize("k", [0.0, -1e-4])
def test_aquifer_k_must_be_positive(k: float) -> None:
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=(26.6, -81.9),
            contaminant="benzene",
            release_rate_kg_s=0.5,
            duration_days=3.0,
            aquifer_k_ms=k,
        )


@pytest.mark.parametrize("porosity", [0.0, -0.1, 1.01, 2.0])
def test_porosity_must_be_in_0_1_interval(porosity: float) -> None:
    """Porosity is dimensionless in (0, 1]; 0 and >1 are rejected."""
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=(26.6, -81.9),
            contaminant="benzene",
            release_rate_kg_s=0.5,
            duration_days=3.0,
            porosity=porosity,
        )


def test_porosity_boundary_one_is_allowed() -> None:
    """porosity == 1.0 is valid (le bound); 0.0 is not (gt bound)."""
    args = MODFLOWRunArgs(
        spill_location_latlon=(26.6, -81.9),
        contaminant="benzene",
        release_rate_kg_s=0.5,
        duration_days=3.0,
        porosity=1.0,
    )
    assert args.porosity == 1.0


def test_contaminant_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=(26.6, -81.9),
            contaminant="",
            release_rate_kg_s=0.5,
            duration_days=3.0,
        )


@pytest.mark.parametrize(
    "latlon",
    [
        (91.0, -81.9),  # lat > 90
        (-91.0, -81.9),  # lat < -90
        (26.6, 181.0),  # lon > 180
        (26.6, -181.0),  # lon < -180
    ],
)
def test_spill_location_latlon_range_validated(latlon: tuple[float, float]) -> None:
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=latlon,
            contaminant="benzene",
            release_rate_kg_s=0.5,
            duration_days=3.0,
        )


def test_spill_location_latlon_order_is_lat_then_lon() -> None:
    """A swapped (lon, lat) pair like (-81.9, 26.6) is fine numerically here
    (both in range), but a clearly-lon-first value like (-81.9, 200.0) is
    rejected because the second slot is the longitude and 200 is out of range.

    This documents the (lat, lon) contract: the FIRST slot is bounded [-90, 90].
    """
    # Valid (lat, lon)
    ok = MODFLOWRunArgs(
        spill_location_latlon=(26.6, -81.9),
        contaminant="benzene",
        release_rate_kg_s=0.5,
        duration_days=3.0,
    )
    assert ok.spill_location_latlon == (26.6, -81.9)
    # First slot (lat) bounded to [-90, 90]: 100 is invalid as a latitude
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=(100.0, -81.9),
            contaminant="benzene",
            release_rate_kg_s=0.5,
            duration_days=3.0,
        )


def test_modflow_run_args_forbids_extra_fields() -> None:
    """GraceModel extra='forbid' - an unknown field is a defect."""
    with pytest.raises(ValidationError):
        MODFLOWRunArgs(
            spill_location_latlon=(26.6, -81.9),
            contaminant="benzene",
            release_rate_kg_s=0.5,
            duration_days=3.0,
            dispersivity_m=10.0,  # not a field
        )


def test_modflow_run_args_roundtrip() -> None:
    args = MODFLOWRunArgs(
        spill_location_latlon=(26.6, -81.9),
        contaminant="benzene",
        release_rate_kg_s=0.5,
        duration_days=3.0,
        aquifer_k_ms=2e-4,
        porosity=0.35,
    )
    a = args.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MODFLOWRunArgs.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    # tuple serializes to a JSON list and round-trips back to a tuple
    assert a["spill_location_latlon"] == [26.6, -81.9]
    assert (
        MODFLOWRunArgs.model_validate(json.loads(text_a)).spill_location_latlon
        == (26.6, -81.9)
    )


# --------------------------------------------------------------------------- #
# PlumeLayerURI - inheritance + round-trip
# --------------------------------------------------------------------------- #


def _plume(**overrides: object) -> PlumeLayerURI:
    base = dict(
        layer_id="run-01HX-plume",
        name="Benzene plume (mg/L)",
        layer_type="raster",
        uri="gs://grace-2/runs/01HX/plume.cog.tif",
        style_preset="plume_concentration",
        max_concentration_mgl=12.5,
        plume_area_km2=3.2,
    )
    base.update(overrides)
    return PlumeLayerURI(**base)  # type: ignore[arg-type]


def test_plume_layer_uri_is_a_layer_uri() -> None:
    """PlumeLayerURI extends LayerURI - it is substitutable as a LayerURI."""
    plume = _plume()
    assert isinstance(plume, LayerURI)
    # Inherited base fields are present and behave identically.
    assert plume.layer_id == "run-01HX-plume"
    assert plume.layer_type == "raster"
    assert plume.role == "primary"  # inherited default
    assert plume.temporal is None  # inherited default


def test_plume_layer_uri_inherits_temporal_and_bbox() -> None:
    plume = _plume(
        temporal=TemporalConfig(
            start="2026-06-01T00:00:00Z",
            end="2026-06-04T00:00:00Z",
            step_seconds=86400,
        ),
        bbox=(-82.0, 26.4, -81.7, 26.8),
        units="mg/L",
    )
    assert plume.temporal is not None
    assert plume.temporal.step_seconds == 86400
    assert plume.bbox == (-82.0, 26.4, -81.7, 26.8)
    assert plume.units == "mg/L"


def test_plume_scalars_present_in_dump_and_are_added_fields() -> None:
    plume = _plume()
    dumped = plume.model_dump(mode="json")
    assert dumped["max_concentration_mgl"] == 12.5
    assert dumped["plume_area_km2"] == 3.2
    # Confirm the two scalars are NOT on the base LayerURI (added by subclass).
    assert "max_concentration_mgl" not in LayerURI.model_fields
    assert "plume_area_km2" not in LayerURI.model_fields
    assert "max_concentration_mgl" in PlumeLayerURI.model_fields
    assert "plume_area_km2" in PlumeLayerURI.model_fields


@pytest.mark.parametrize("conc", [-0.1, -1.0])
def test_max_concentration_must_be_non_negative(conc: float) -> None:
    with pytest.raises(ValidationError):
        _plume(max_concentration_mgl=conc)


@pytest.mark.parametrize("area", [-0.1, -5.0])
def test_plume_area_must_be_non_negative(area: float) -> None:
    with pytest.raises(ValidationError):
        _plume(plume_area_km2=area)


def test_plume_zero_scalars_allowed() -> None:
    """A plume with zero concentration/area (e.g. below detection) is valid."""
    plume = _plume(max_concentration_mgl=0.0, plume_area_km2=0.0)
    assert plume.max_concentration_mgl == 0.0
    assert plume.plume_area_km2 == 0.0


def test_plume_layer_uri_roundtrip() -> None:
    plume = _plume(
        temporal=TemporalConfig(
            start="2026-06-01T00:00:00Z",
            end="2026-06-04T00:00:00Z",
            step_seconds=86400,
        ),
        bbox=(-82.0, 26.4, -81.7, 26.8),
        units="mg/L",
        role="primary",
    )
    a = plume.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = PlumeLayerURI.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)


def test_plume_layer_uri_requires_the_added_scalars() -> None:
    """The two plume scalars are required (no defaults) - a plume without them
    is incomplete."""
    with pytest.raises(ValidationError):
        PlumeLayerURI(
            layer_id="run-01HX-plume",
            name="Plume",
            layer_type="raster",
            uri="gs://grace-2/runs/01HX/plume.cog.tif",
            style_preset="plume_concentration",
            # missing max_concentration_mgl + plume_area_km2
        )


def test_plume_layer_uri_forbids_extra_fields() -> None:
    """Inherited GraceModel extra='forbid' still applies on the subclass."""
    with pytest.raises(ValidationError):
        _plume(some_unknown_field=1.0)


# --------------------------------------------------------------------------- #
# sprint-18 Wave-1: archetype run-args fields + new LayerURI subclasses
# (ADDITIVE / DEFAULTED - the existing spill/seepage path stays byte-identical)
# --------------------------------------------------------------------------- #


def _spill_args(**overrides: object) -> MODFLOWRunArgs:
    """The minimal EXISTING spill run-args (no archetype) as the additive base."""
    base: dict[str, object] = dict(
        spill_location_latlon=(26.6, -81.9),
        contaminant="benzene",
        release_rate_kg_s=0.5,
        duration_days=3.0,
    )
    base.update(overrides)
    return MODFLOWRunArgs(**base)  # type: ignore[arg-type]


def test_additive_safety_no_new_fields_still_validates() -> None:
    """A run-args with NONE of the sprint-18 archetype fields validates, all the
    new fields default off, and schema_version is unchanged (additive growth)."""
    args = _spill_args()
    # archetype selector defaults to None -> existing spill/seepage path.
    assert args.archetype is None
    # sustainable_yield fields default off (storage uses the demo SY/SS defaults).
    assert args.well_location_latlon is None
    assert args.pumping_rate_m3_day is None
    assert args.aquifer_sy == DEFAULT_AQUIFER_SY == 0.2
    assert args.aquifer_ss == DEFAULT_AQUIFER_SS == 1e-5
    assert args.sim_years is None
    assert args.n_periods is None
    # mine_dewatering fields default off.
    assert args.pit_footprint_lonlat is None
    assert args.drain_elevation_m is None
    assert args.drain_conductance_m2_day is None
    assert args.well_pumping_rate_m3_day is None
    # regional_water_budget field defaults off.
    assert args.zone_partition is None
    # --- sprint-18 Wave-2 archetype fields all default off ---
    # MAR (managed aquifer recharge) fields.
    assert args.basin_footprint_lonlat is None
    assert args.infiltration_rate_m_day is None
    assert args.recharge_months is None
    # ASR (aquifer storage & recovery) fields.
    assert args.injection_rate_m3_day is None
    assert args.recovery_rate_m3_day is None
    assert args.injection_months is None
    assert args.recovery_months is None
    assert args.n_cycles is None
    # wetland_hydroperiod fields (specific_yield uses the demo default).
    assert args.wetland_footprint_lonlat is None
    assert args.recharge_schedule_m_day is None
    assert args.et_surface_m is None
    assert args.et_max_rate_m_day is None
    assert args.et_extinction_depth_m is None
    assert args.specific_yield == DEFAULT_WETLAND_SY == 0.2
    # schema_version UNCHANGED by the additive growth.
    assert args.schema_version == "v2"


def test_schema_version_unchanged_after_additive_fields() -> None:
    """The contract version pin stays v2 (no schema_version bump for additive)."""
    assert MODFLOWRunArgs.model_fields["schema_version"].default == "v2"


def test_sustainable_yield_archetype_roundtrip() -> None:
    args = _spill_args(
        archetype="sustainable_yield",
        well_location_latlon=(40.0, -100.0),
        pumping_rate_m3_day=-2000.0,  # extraction (WEL negative)
        aquifer_sy=0.15,
        aquifer_ss=2e-5,
        sim_years=10.0,
        n_periods=12,
    )
    assert args.archetype == "sustainable_yield"
    assert args.well_location_latlon == (40.0, -100.0)
    assert args.pumping_rate_m3_day == -2000.0
    assert args.aquifer_sy == 0.15
    assert args.aquifer_ss == 2e-5
    assert args.sim_years == 10.0
    assert args.n_periods == 12
    a = args.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MODFLOWRunArgs.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    # the well-location tuple round-trips through a JSON list back to a tuple.
    assert a["well_location_latlon"] == [40.0, -100.0]
    assert (
        MODFLOWRunArgs.model_validate(json.loads(text_a)).well_location_latlon
        == (40.0, -100.0)
    )


def test_well_location_latlon_range_validated() -> None:
    """The pumping-well location honors the (lat, lon) range contract."""
    with pytest.raises(ValidationError):
        _spill_args(archetype="sustainable_yield", well_location_latlon=(100.0, -100.0))
    with pytest.raises(ValidationError):
        _spill_args(archetype="sustainable_yield", well_location_latlon=(40.0, 200.0))


@pytest.mark.parametrize("sy", [0.0, -0.1, 1.5])
def test_aquifer_sy_bounds(sy: float) -> None:
    """Specific yield is in (0, 1]; 0 and >1 are rejected."""
    with pytest.raises(ValidationError):
        _spill_args(aquifer_sy=sy)


@pytest.mark.parametrize("ss", [0.0, -1e-6])
def test_aquifer_ss_must_be_positive(ss: float) -> None:
    with pytest.raises(ValidationError):
        _spill_args(aquifer_ss=ss)


@pytest.mark.parametrize("n", [0, -1])
def test_n_periods_must_be_at_least_one(n: int) -> None:
    with pytest.raises(ValidationError):
        _spill_args(n_periods=n)


def test_mine_dewatering_archetype_roundtrip() -> None:
    args = _spill_args(
        archetype="mine_dewatering",
        pit_footprint_lonlat=[(-100.0, 40.0), (-100.0, 40.1), (-99.9, 40.1)],
        drain_elevation_m=12.5,
        drain_conductance_m2_day=500.0,
        well_pumping_rate_m3_day=-300.0,
    )
    assert args.archetype == "mine_dewatering"
    assert args.pit_footprint_lonlat == [
        (-100.0, 40.0),
        (-100.0, 40.1),
        (-99.9, 40.1),
    ]
    assert args.drain_elevation_m == 12.5
    assert args.drain_conductance_m2_day == 500.0
    assert args.well_pumping_rate_m3_day == -300.0
    a = args.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MODFLOWRunArgs.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    # list-of-tuples round-trips to list-of-lists in JSON and back to tuples.
    assert a["pit_footprint_lonlat"] == [[-100.0, 40.0], [-100.0, 40.1], [-99.9, 40.1]]
    assert MODFLOWRunArgs.model_validate(json.loads(text_a)).pit_footprint_lonlat == [
        (-100.0, 40.0),
        (-100.0, 40.1),
        (-99.9, 40.1),
    ]


def test_drain_conductance_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _spill_args(archetype="mine_dewatering", drain_conductance_m2_day=0.0)


def test_regional_water_budget_archetype_roundtrip() -> None:
    args = _spill_args(
        archetype="regional_water_budget",
        zone_partition="upgradient_downgradient",
    )
    assert args.archetype == "regional_water_budget"
    assert args.zone_partition == "upgradient_downgradient"
    a = args.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MODFLOWRunArgs.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)


def test_unknown_archetype_rejected_by_literal() -> None:
    with pytest.raises(ValidationError):
        _spill_args(archetype="not_an_archetype")


# --------------------------------------------------------------------------- #
# sprint-18 Wave-2: MAR / ASR / wetland_hydroperiod run-args fields
# (ADDITIVE / DEFAULTED - Wave-1 + spill/seepage paths stay byte-identical)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("archetype", ["MAR", "ASR", "wetland_hydroperiod"])
def test_wave2_archetypes_accepted_by_literal(archetype: str) -> None:
    """The three Wave-2 archetype literals validate (additive on the Wave-1 set)."""
    args = _spill_args(archetype=archetype)
    assert args.archetype == archetype


def test_mar_archetype_roundtrip() -> None:
    args = _spill_args(
        archetype="MAR",
        basin_footprint_lonlat=[(-100.0, 40.0), (-100.0, 40.1), (-99.9, 40.1)],
        infiltration_rate_m_day=0.5,
        recharge_months=6,
        n_periods=6,
    )
    assert args.archetype == "MAR"
    assert args.basin_footprint_lonlat == [
        (-100.0, 40.0),
        (-100.0, 40.1),
        (-99.9, 40.1),
    ]
    assert args.infiltration_rate_m_day == 0.5
    assert args.recharge_months == 6
    assert args.n_periods == 6
    a = args.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MODFLOWRunArgs.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    # list-of-tuples round-trips to list-of-lists in JSON and back to tuples.
    assert a["basin_footprint_lonlat"] == [[-100.0, 40.0], [-100.0, 40.1], [-99.9, 40.1]]
    assert MODFLOWRunArgs.model_validate(json.loads(text_a)).basin_footprint_lonlat == [
        (-100.0, 40.0),
        (-100.0, 40.1),
        (-99.9, 40.1),
    ]


def test_mar_infiltration_rate_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _spill_args(archetype="MAR", infiltration_rate_m_day=0.0)


def test_mar_recharge_months_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        _spill_args(archetype="MAR", recharge_months=0)


def test_asr_archetype_roundtrip() -> None:
    args = _spill_args(
        archetype="ASR",
        well_location_latlon=(40.0, -100.0),  # reused from sustainable_yield
        injection_rate_m3_day=1500.0,
        recovery_rate_m3_day=1200.0,
        injection_months=6,
        recovery_months=4,
        n_cycles=3,
    )
    assert args.archetype == "ASR"
    assert args.well_location_latlon == (40.0, -100.0)
    assert args.injection_rate_m3_day == 1500.0
    assert args.recovery_rate_m3_day == 1200.0
    assert args.injection_months == 6
    assert args.recovery_months == 4
    assert args.n_cycles == 3
    a = args.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MODFLOWRunArgs.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    # the reused well-location tuple round-trips through a JSON list back to a tuple.
    assert a["well_location_latlon"] == [40.0, -100.0]
    assert (
        MODFLOWRunArgs.model_validate(json.loads(text_a)).well_location_latlon
        == (40.0, -100.0)
    )


@pytest.mark.parametrize("rate", [0.0, -1.0])
def test_asr_injection_rate_must_be_positive(rate: float) -> None:
    """ASR injection rate is a POSITIVE magnitude (the adapter applies the sign)."""
    with pytest.raises(ValidationError):
        _spill_args(archetype="ASR", injection_rate_m3_day=rate)


@pytest.mark.parametrize("rate", [0.0, -1.0])
def test_asr_recovery_rate_must_be_positive(rate: float) -> None:
    with pytest.raises(ValidationError):
        _spill_args(archetype="ASR", recovery_rate_m3_day=rate)


@pytest.mark.parametrize("n", [0, -1])
def test_asr_n_cycles_must_be_at_least_one(n: int) -> None:
    with pytest.raises(ValidationError):
        _spill_args(archetype="ASR", n_cycles=n)


def test_wetland_hydroperiod_archetype_roundtrip() -> None:
    args = _spill_args(
        archetype="wetland_hydroperiod",
        wetland_footprint_lonlat=[(-81.0, 26.0), (-81.0, 26.1), (-80.9, 26.1)],
        recharge_schedule_m_day=[0.01, 0.005, 0.0, 0.002],
        et_surface_m=2.0,
        et_max_rate_m_day=0.004,
        et_extinction_depth_m=1.5,
        specific_yield=0.18,
    )
    assert args.archetype == "wetland_hydroperiod"
    assert args.wetland_footprint_lonlat == [
        (-81.0, 26.0),
        (-81.0, 26.1),
        (-80.9, 26.1),
    ]
    assert args.recharge_schedule_m_day == [0.01, 0.005, 0.0, 0.002]
    assert args.et_surface_m == 2.0
    assert args.et_max_rate_m_day == 0.004
    assert args.et_extinction_depth_m == 1.5
    assert args.specific_yield == 0.18
    a = args.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MODFLOWRunArgs.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    assert a["recharge_schedule_m_day"] == [0.01, 0.005, 0.0, 0.002]


@pytest.mark.parametrize("sy", [0.0, -0.1, 1.5])
def test_wetland_specific_yield_bounds(sy: float) -> None:
    """Wetland specific yield is in (0, 1]; 0 and >1 are rejected."""
    with pytest.raises(ValidationError):
        _spill_args(archetype="wetland_hydroperiod", specific_yield=sy)


def test_wetland_specific_yield_defaults_to_demo_value() -> None:
    args = _spill_args(archetype="wetland_hydroperiod")
    assert args.specific_yield == DEFAULT_WETLAND_SY == 0.2


@pytest.mark.parametrize(
    "field,value",
    [
        ("et_max_rate_m_day", 0.0),
        ("et_max_rate_m_day", -0.1),
        ("et_extinction_depth_m", 0.0),
        ("et_extinction_depth_m", -1.0),
    ],
)
def test_wetland_et_params_must_be_positive(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        _spill_args(archetype="wetland_hydroperiod", **{field: value})


# --------------------------------------------------------------------------- #
# DrawdownLayerURI / DewaterLayerURI / BudgetPartitionLayerURI
# --------------------------------------------------------------------------- #


def _drawdown(**overrides: object) -> DrawdownLayerURI:
    base: dict[str, object] = dict(
        layer_id="run-01HX-drawdown",
        name="Pumping drawdown (m)",
        layer_type="raster",
        uri="s3://grace-2/runs/01HX/drawdown.cog.tif",
        style_preset="continuous_drawdown_m",
        max_drawdown_m=4.2,
    )
    base.update(overrides)
    return DrawdownLayerURI(**base)  # type: ignore[arg-type]


def test_drawdown_layer_uri_is_a_layer_uri_and_roundtrips() -> None:
    layer = _drawdown(
        head_decline_timeseries=[0.0, 1.1, 2.4, 3.7, 4.2],
        units="meters",
        bbox=(-100.2, 39.9, -99.8, 40.3),
    )
    assert isinstance(layer, LayerURI)
    assert layer.max_drawdown_m == 4.2
    assert layer.head_decline_timeseries == [0.0, 1.1, 2.4, 3.7, 4.2]
    a = layer.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = DrawdownLayerURI.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)


def test_drawdown_timeseries_optional_and_scalar_added() -> None:
    layer = _drawdown()
    assert layer.head_decline_timeseries is None  # optional, defaults None
    assert "max_drawdown_m" not in LayerURI.model_fields
    assert "max_drawdown_m" in DrawdownLayerURI.model_fields


@pytest.mark.parametrize("dd", [-0.1, -5.0])
def test_max_drawdown_must_be_non_negative(dd: float) -> None:
    with pytest.raises(ValidationError):
        _drawdown(max_drawdown_m=dd)


def _dewater(**overrides: object) -> DewaterLayerURI:
    base: dict[str, object] = dict(
        layer_id="run-01HX-dewater",
        name="Mine dewatering rate (m^3/day)",
        layer_type="raster",
        uri="s3://grace-2/runs/01HX/dewater.cog.tif",
        style_preset="continuous_dewatering_rate",
        dewatering_rate_m3_day=18500.0,
        drain_cell_count=42,
    )
    base.update(overrides)
    return DewaterLayerURI(**base)  # type: ignore[arg-type]


def test_dewater_layer_uri_is_a_layer_uri_and_roundtrips() -> None:
    layer = _dewater(units="m^3/day")
    assert isinstance(layer, LayerURI)
    assert layer.dewatering_rate_m3_day == 18500.0
    assert layer.drain_cell_count == 42
    a = layer.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = DewaterLayerURI.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    assert "dewatering_rate_m3_day" not in LayerURI.model_fields
    assert "dewatering_rate_m3_day" in DewaterLayerURI.model_fields


@pytest.mark.parametrize("rate", [-0.1, -100.0])
def test_dewatering_rate_must_be_non_negative(rate: float) -> None:
    with pytest.raises(ValidationError):
        _dewater(dewatering_rate_m3_day=rate)


def test_drain_cell_count_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _dewater(drain_cell_count=-1)


def _budget(**overrides: object) -> BudgetPartitionLayerURI:
    base: dict[str, object] = dict(
        layer_id="run-01HX-budget",
        name="Regional water budget partition",
        layer_type="vector",
        uri="s3://grace-2/runs/01HX/budget.fgb",
        style_preset="continuous_head_m",
        budget_partition_m3_day={
            "upgradient_chd_in": 1200.0,
            "downgradient_chd_out": -1180.0,
            "storage": -20.0,
        },
    )
    base.update(overrides)
    return BudgetPartitionLayerURI(**base)  # type: ignore[arg-type]


def test_budget_partition_layer_uri_is_a_layer_uri_and_roundtrips() -> None:
    layer = _budget(units="m^3/day")
    assert isinstance(layer, LayerURI)
    assert layer.budget_partition_m3_day["upgradient_chd_in"] == 1200.0
    assert layer.budget_partition_m3_day["downgradient_chd_out"] == -1180.0
    a = layer.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = BudgetPartitionLayerURI.model_validate(json.loads(text_a)).model_dump(
        mode="json"
    )
    assert text_a == json.dumps(b, sort_keys=True)
    assert "budget_partition_m3_day" not in LayerURI.model_fields
    assert "budget_partition_m3_day" in BudgetPartitionLayerURI.model_fields


def test_budget_partition_required_and_extra_forbidden() -> None:
    # the partition dict is required (no default).
    with pytest.raises(ValidationError):
        BudgetPartitionLayerURI(
            layer_id="run-01HX-budget",
            name="Budget",
            layer_type="vector",
            uri="s3://grace-2/runs/01HX/budget.fgb",
            style_preset="continuous_head_m",
            # missing budget_partition_m3_day
        )
    # inherited GraceModel extra='forbid' still applies.
    with pytest.raises(ValidationError):
        _budget(some_unknown_field=1.0)


# --------------------------------------------------------------------------- #
# Output-quantity registry: the three new modflow quantities are registered.
# --------------------------------------------------------------------------- #


def test_new_modflow_output_quantities_registered() -> None:
    """drawdown / dewatering-rate / budget-partition are registered + default-on."""
    from grace2_contracts.output_quantities import get_output_registry

    registry = get_output_registry("modflow")
    by_id = {spec.quantity_id: spec for spec in registry}
    for qid in ("drawdown", "dewatering-rate", "budget-partition"):
        assert qid in by_id, f"missing modflow output quantity {qid!r}"
        assert by_id[qid].default_on is True
    # the new quantities are ADDITIVE: the existing headline quantities still exist.
    assert "plume-concentration" in by_id
    assert "river-seepage" in by_id


# --------------------------------------------------------------------------- #
# sprint-18 Wave-2: MoundingLayerURI / ASRLayerURI / HydroperiodLayerURI
# --------------------------------------------------------------------------- #


def _mounding(**overrides: object) -> MoundingLayerURI:
    base: dict[str, object] = dict(
        layer_id="run-01HX-mounding",
        name="Recharge mounding (m)",
        layer_type="raster",
        uri="s3://grace-2/runs/01HX/mounding.cog.tif",
        style_preset="continuous_mounding_m",
        max_mounding_m=3.4,
    )
    base.update(overrides)
    return MoundingLayerURI(**base)  # type: ignore[arg-type]


def test_mounding_layer_uri_is_a_layer_uri_and_roundtrips() -> None:
    layer = _mounding(recharged_volume_m3=125000.0, units="meters")
    assert isinstance(layer, LayerURI)
    assert layer.max_mounding_m == 3.4
    assert layer.recharged_volume_m3 == 125000.0
    a = layer.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = MoundingLayerURI.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    assert "max_mounding_m" not in LayerURI.model_fields
    assert "max_mounding_m" in MoundingLayerURI.model_fields


def test_mounding_recharged_volume_optional_defaults_none() -> None:
    layer = _mounding()
    assert layer.recharged_volume_m3 is None  # optional, defaults None


@pytest.mark.parametrize("m", [-0.1, -5.0])
def test_max_mounding_must_be_non_negative(m: float) -> None:
    with pytest.raises(ValidationError):
        _mounding(max_mounding_m=m)


def test_recharged_volume_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _mounding(recharged_volume_m3=-1.0)


def _asr(**overrides: object) -> ASRLayerURI:
    base: dict[str, object] = dict(
        layer_id="run-01HX-asr",
        name="ASR well head (m)",
        layer_type="raster",
        uri="s3://grace-2/runs/01HX/asr.cog.tif",
        style_preset="continuous_head_m",
    )
    base.update(overrides)
    return ASRLayerURI(**base)  # type: ignore[arg-type]


def test_asr_layer_uri_is_a_layer_uri_and_roundtrips() -> None:
    layer = _asr(
        recovery_efficiency=0.82,
        head_timeseries=[10.0, 14.0, 11.0, 15.0, 12.0],
        units="meters",
    )
    assert isinstance(layer, LayerURI)
    assert layer.recovery_efficiency == 0.82
    assert layer.head_timeseries == [10.0, 14.0, 11.0, 15.0, 12.0]
    a = layer.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = ASRLayerURI.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    assert "recovery_efficiency" not in LayerURI.model_fields
    assert "recovery_efficiency" in ASRLayerURI.model_fields


def test_asr_scalars_optional_default_none() -> None:
    layer = _asr()
    assert layer.recovery_efficiency is None
    assert layer.head_timeseries is None


@pytest.mark.parametrize("eff", [-0.1, 1.5])
def test_asr_recovery_efficiency_bounds(eff: float) -> None:
    """Recovery efficiency is a fraction in [0, 1]; <0 and >1 are rejected."""
    with pytest.raises(ValidationError):
        _asr(recovery_efficiency=eff)


def test_asr_recovery_efficiency_boundaries_allowed() -> None:
    assert _asr(recovery_efficiency=0.0).recovery_efficiency == 0.0
    assert _asr(recovery_efficiency=1.0).recovery_efficiency == 1.0


def _hydroperiod(**overrides: object) -> HydroperiodLayerURI:
    base: dict[str, object] = dict(
        layer_id="run-01HX-hydroperiod",
        name="Wetland hydroperiod (m)",
        layer_type="raster",
        uri="s3://grace-2/runs/01HX/hydroperiod.cog.tif",
        style_preset="continuous_hydroperiod_m",
        seasonal_head_range_m=1.2,
    )
    base.update(overrides)
    return HydroperiodLayerURI(**base)  # type: ignore[arg-type]


def test_hydroperiod_layer_uri_is_a_layer_uri_and_roundtrips() -> None:
    layer = _hydroperiod(
        head_timeseries=[1.0, 1.6, 2.2, 1.4, 1.0],
        units="meters",
    )
    assert isinstance(layer, LayerURI)
    assert layer.seasonal_head_range_m == 1.2
    assert layer.head_timeseries == [1.0, 1.6, 2.2, 1.4, 1.0]
    a = layer.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = HydroperiodLayerURI.model_validate(json.loads(text_a)).model_dump(mode="json")
    assert text_a == json.dumps(b, sort_keys=True)
    assert "seasonal_head_range_m" not in LayerURI.model_fields
    assert "seasonal_head_range_m" in HydroperiodLayerURI.model_fields


def test_hydroperiod_timeseries_optional_defaults_none() -> None:
    layer = _hydroperiod()
    assert layer.head_timeseries is None


@pytest.mark.parametrize("r", [-0.1, -2.0])
def test_seasonal_head_range_must_be_non_negative(r: float) -> None:
    with pytest.raises(ValidationError):
        _hydroperiod(seasonal_head_range_m=r)


def test_wave2_layer_uris_require_their_added_scalar_and_forbid_extra() -> None:
    """The required Wave-2 scalars (max_mounding_m / seasonal_head_range_m) have no
    default; inherited GraceModel extra='forbid' still applies on every subclass."""
    with pytest.raises(ValidationError):
        MoundingLayerURI(
            layer_id="run-01HX-mounding",
            name="Mounding",
            layer_type="raster",
            uri="s3://grace-2/runs/01HX/mounding.cog.tif",
            style_preset="continuous_mounding_m",
            # missing max_mounding_m
        )
    with pytest.raises(ValidationError):
        HydroperiodLayerURI(
            layer_id="run-01HX-hydroperiod",
            name="Hydroperiod",
            layer_type="raster",
            uri="s3://grace-2/runs/01HX/hydroperiod.cog.tif",
            style_preset="continuous_hydroperiod_m",
            # missing seasonal_head_range_m
        )
    with pytest.raises(ValidationError):
        _mounding(some_unknown_field=1.0)
    with pytest.raises(ValidationError):
        _asr(some_unknown_field=1.0)
    with pytest.raises(ValidationError):
        _hydroperiod(some_unknown_field=1.0)


def test_wave2_modflow_output_quantities_registered() -> None:
    """mounding / recovery-efficiency / hydroperiod are registered + default-on,
    additive on top of the Wave-1 + headline quantities."""
    from grace2_contracts.output_quantities import get_output_registry

    registry = get_output_registry("modflow")
    by_id = {spec.quantity_id: spec for spec in registry}
    for qid in ("mounding", "recovery-efficiency", "hydroperiod"):
        assert qid in by_id, f"missing modflow output quantity {qid!r}"
        assert by_id[qid].default_on is True
    # ADDITIVE: the Wave-1 + headline quantities still exist.
    assert "drawdown" in by_id
    assert "dewatering-rate" in by_id
    assert "budget-partition" in by_id
    assert "plume-concentration" in by_id
    assert "river-seepage" in by_id
