"""Unit tests for the COASTAL SFINCS surge / obstacle PLUMBING in
``model_flood_scenario`` (AGENT A — the workflow-side glue that turns the
``surge_forcing`` / ``building_obstacles`` args into typed ``ForcingSpec`` +
``BuildOptions`` members handed to ``build_sfincs_model``).

These tests exercise the pure translation helpers (no network, no solver):

- ``_build_surge_forcing_members`` maps the nested ``surge_forcing`` dict into
  the four typed ``ForcingSpec`` members; partial / absent sub-dicts yield
  ``None`` (no block emitted).
- ``_resolve_building_obstacle_uri`` handles the three obstacle forms
  (``False`` → None; ``str`` → verbatim; ``True`` → best-effort OSM fetch,
  degrading to None on failure without aborting the flood).
"""

from __future__ import annotations

from grace2_agent.workflows.model_flood_scenario import (
    _build_surge_forcing_members,
    _resolve_building_obstacle_uri,
)
from grace2_agent.workflows.sfincs_builder import (
    DischargeForcing,
    PressureForcing,
    WaterlevelForcing,
    WindForcing,
)


def test_surge_members_full_dict() -> None:
    """A fully-populated surge_forcing dict maps to all four typed members."""
    wl, dq, wind, press = _build_surge_forcing_members(
        {
            "waterlevel": {
                "timeseries_uri": "/tmp/wl.csv",
                "locations_uri": "/tmp/bnd.fgb",
                "offset": 0.2,
                "buffer_m": 4000.0,
            },
            "discharge": {
                "timeseries_uri": "/tmp/dis.csv",
                "rivers_uri": "/tmp/riv.fgb",
                "river_upa_km2": 12.0,
            },
            "wind": {"magnitude": 40.0, "direction": 200.0},
            "pressure": {"grid_uri": "/tmp/p.nc", "fill_value": 101000.0},
        }
    )
    assert isinstance(wl, WaterlevelForcing)
    assert wl.timeseries_uri == "/tmp/wl.csv"
    assert wl.offset == 0.2 and wl.buffer_m == 4000.0
    assert isinstance(dq, DischargeForcing)
    assert dq.rivers_uri == "/tmp/riv.fgb" and dq.river_upa_km2 == 12.0
    assert isinstance(wind, WindForcing)
    assert wind.magnitude == 40.0 and wind.direction == 200.0
    assert isinstance(press, PressureForcing)
    assert press.grid_uri == "/tmp/p.nc" and press.fill_value == 101000.0


def test_surge_members_none_and_empty() -> None:
    """None / empty surge_forcing → all four members None (pure-pluvial deck)."""
    assert _build_surge_forcing_members(None) == (None, None, None, None)
    assert _build_surge_forcing_members({}) == (None, None, None, None)


def test_surge_members_partial_dict() -> None:
    """Only the present sub-dicts produce members; the rest stay None."""
    wl, dq, wind, press = _build_surge_forcing_members(
        {"waterlevel": {"geodataset_uri": "/tmp/wl.nc"}}
    )
    assert isinstance(wl, WaterlevelForcing) and wl.geodataset_uri == "/tmp/wl.nc"
    assert dq is None and wind is None and press is None


def test_surge_members_incomplete_subdicts_are_dropped() -> None:
    """A sub-dict missing its required URI/values yields None (no half-built block).

    e.g. a wind dict with only ``magnitude`` (no ``direction``) is not a valid
    uniform-wind forcing, so it must NOT produce a WindForcing.
    """
    wl, dq, wind, press = _build_surge_forcing_members(
        {
            "waterlevel": {"offset": 0.1},  # no timeseries / geodataset
            "discharge": {"locations_uri": "/tmp/x.fgb"},  # no series / rivers / hydro
            "wind": {"magnitude": 30.0},  # missing direction
            "pressure": {"fill_value": 101325.0},  # missing grid_uri
        }
    )
    assert wl is None
    assert dq is None
    assert wind is None
    assert press is None


def test_resolve_building_obstacle_false_and_str() -> None:
    """``False`` → None; a string is used verbatim as the obstacle geofile URI."""
    assert _resolve_building_obstacle_uri(False, (0.0, 0.0, 1.0, 1.0), []) is None
    assert (
        _resolve_building_obstacle_uri("/tmp/b.fgb", (0.0, 0.0, 1.0, 1.0), [])
        == "/tmp/b.fgb"
    )


def test_resolve_building_obstacle_true_degrades_on_fetch_failure(monkeypatch) -> None:
    """``True`` with a failing fetch_buildings degrades to None (never aborts).

    Same degrade policy as river geometry (job-0307): a footprint-fetch failure
    must NOT kill the flood — the deck just omits obstacles.
    """
    import grace2_agent.tools.data_fetch as data_fetch

    def _boom(*_a, **_k):
        raise RuntimeError("overpass down")

    monkeypatch.setattr(data_fetch, "fetch_buildings", _boom)
    ds: list = []
    assert _resolve_building_obstacle_uri(True, (-85.4, 29.9, -85.3, 30.0), ds) is None
    assert ds == []  # nothing recorded on failure


def test_resolve_building_obstacle_true_records_source_on_success(monkeypatch) -> None:
    """A successful OSM footprint fetch returns its URI + records a DataSource."""
    import grace2_agent.tools.data_fetch as data_fetch

    class _Layer:
        uri = "s3://cache/buildings.fgb"

    monkeypatch.setattr(data_fetch, "fetch_buildings", lambda *_a, **_k: _Layer())
    ds: list = []
    uri = _resolve_building_obstacle_uri(True, (-85.4, 29.9, -85.3, 30.0), ds)
    assert uri == "s3://cache/buildings.fgb"
    assert len(ds) == 1
    assert ds[0].uri == "s3://cache/buildings.fgb"
