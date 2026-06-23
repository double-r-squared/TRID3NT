"""Unit tests for the per-engine physics-override registry (STEP 2).

Pins validate_and_resolve_physics (None -> {}, unknown engine/key, type coercion,
range checks, bool/str literals) + applied_physics_delta + the registry shape.
"""

from __future__ import annotations

import pytest

from grace2_agent.workflows.physics_registry import (
    PHYSICS_REGISTRY,
    PhysicsRegistryError,
    applied_physics_delta,
    get_engine_physics,
    validate_and_resolve_physics,
)


# --------------------------------------------------------------------------- #
# Registry shape
# --------------------------------------------------------------------------- #
def test_every_entry_has_the_required_keys() -> None:
    for engine, table in PHYSICS_REGISTRY.items():
        for key, spec in table.items():
            for required in ("type", "range", "default", "deck_target", "doc"):
                assert required in spec, f"{engine}.{key} missing {required!r}"
            # the default must itself pass the spec (a self-consistent table).
            resolved = validate_and_resolve_physics(engine, {key: spec["default"]})
            assert key in resolved


def test_get_engine_physics_unknown_raises() -> None:
    with pytest.raises(PhysicsRegistryError):
        get_engine_physics("not_an_engine")


# --------------------------------------------------------------------------- #
# validate_and_resolve_physics
# --------------------------------------------------------------------------- #
def test_none_overrides_returns_empty_dict() -> None:
    assert validate_and_resolve_physics("sfincs", None) == {}


def test_empty_overrides_returns_empty_dict() -> None:
    assert validate_and_resolve_physics("sfincs", {}) == {}


def test_valid_overrides_resolve_and_coerce() -> None:
    r = validate_and_resolve_physics(
        "sfincs", {"alpha": 0.5, "advection": 2}
    )
    assert r == {"alpha": 0.5, "advection": 2}
    assert isinstance(r["alpha"], float) and isinstance(r["advection"], int)


def test_unknown_key_raises_typed() -> None:
    with pytest.raises(PhysicsRegistryError) as ei:
        validate_and_resolve_physics("sfincs", {"bogus": 1.0})
    assert ei.value.engine == "sfincs" and ei.value.key == "bogus"


def test_unknown_engine_raises() -> None:
    with pytest.raises(PhysicsRegistryError):
        validate_and_resolve_physics("nope", {"x": 1})


def test_out_of_range_raises() -> None:
    with pytest.raises(PhysicsRegistryError) as ei:
        validate_and_resolve_physics("sfincs", {"alpha": 99.0})
    assert ei.value.key == "alpha"
    with pytest.raises(PhysicsRegistryError):
        validate_and_resolve_physics("modflow", {"sorption_kd": -1.0})


def test_int_key_rejects_non_integer_float() -> None:
    with pytest.raises(PhysicsRegistryError):
        validate_and_resolve_physics("sfincs", {"advection": 1.5})
    # an integral float is accepted + coerced to int.
    assert validate_and_resolve_physics("sfincs", {"advection": 2.0}) == {"advection": 2}


def test_bool_key_coerces_strings() -> None:
    assert validate_and_resolve_physics("sfincs", {"coriolis": "false"}) == {
        "coriolis": False
    }
    assert validate_and_resolve_physics("sfincs", {"coriolis": "on"}) == {
        "coriolis": True
    }
    assert validate_and_resolve_physics("sfincs", {"coriolis": True}) == {
        "coriolis": True
    }


def test_bool_key_rejects_garbage() -> None:
    with pytest.raises(PhysicsRegistryError):
        validate_and_resolve_physics("sfincs", {"coriolis": "maybe"})


def test_str_literal_key_enforces_allowed_values() -> None:
    assert validate_and_resolve_physics("swan", {"whitecapping": "komen"}) == {
        "whitecapping": "komen"
    }
    with pytest.raises(PhysicsRegistryError):
        validate_and_resolve_physics("swan", {"whitecapping": "nonsense"})


def test_str_value_is_stripped() -> None:
    assert validate_and_resolve_physics("swan", {"friction": " jonswap "}) == {
        "friction": "jonswap"
    }


def test_non_dict_overrides_raises() -> None:
    with pytest.raises(PhysicsRegistryError):
        validate_and_resolve_physics("sfincs", ["alpha", 0.5])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# applied_physics_delta
# --------------------------------------------------------------------------- #
def test_delta_empty_for_empty_resolved() -> None:
    assert applied_physics_delta("sfincs", {}) == {}


def test_delta_reports_from_to_and_deck_target() -> None:
    resolved = validate_and_resolve_physics("modflow", {"sorption_kd": 5.0})
    delta = applied_physics_delta("modflow", resolved)
    assert delta["sorption_kd"]["from"] == 0.0
    assert delta["sorption_kd"]["to"] == 5.0
    assert delta["sorption_kd"]["deck_target"] == "GwtMst:distcoef"
    assert "doc" in delta["sorption_kd"]
