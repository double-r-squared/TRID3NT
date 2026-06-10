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

from grace2_contracts import MODFLOWRunArgs, PlumeLayerURI
from grace2_contracts.execution import LayerURI
from grace2_contracts.envelope import TemporalConfig
from grace2_contracts.modflow_contracts import (
    DEFAULT_AQUIFER_K_MS,
    DEFAULT_POROSITY,
)


# --------------------------------------------------------------------------- #
# MODFLOWRunArgs — defaults (OQ-3 TENTATIVE demo parameterization)
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
    assert args.schema_version == "v1"


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
# MODFLOWRunArgs — validation bounds
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
    """GraceModel extra='forbid' — an unknown field is a defect."""
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
# PlumeLayerURI — inheritance + round-trip
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
    """PlumeLayerURI extends LayerURI — it is substitutable as a LayerURI."""
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
    """The two plume scalars are required (no defaults) — a plume without them
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
