"""Unit tests for ``model_satellite_fire_animation`` (fire-animation demos S5/J5).

Coverage:
- run_model_satellite_fire_animation registered (workflow_dispatch) + the new
  fire-animation tools grew the TOOL_REGISTRY.
- Product routing: GOES products -> fetch_goes_animation; day_fire ->
  fetch_viirs_day_fire.
- Default window per family (GOES ~6.5h, VIIRS ~4d) + discovery floor.
- The workflow STOPS at the bbox/window review gate (confirm=false) and returns
  the AOI bbox + planned frame counts WITHOUT fetching imagery.
- On confirm=true it emits frames in the postprocess_flood SHAPE (distinct
  layer_ids + shared style_preset + an ISO-time NAME token + identical bbox) and
  honesty-floors an empty run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_contracts.execution import LayerURI
from grace2_agent.workflows.model_satellite_fire_animation import (
    GOES_PRODUCTS,
    SUPPORTED_PRODUCTS,
    VIIRS_PRODUCTS,
    SatelliteFireAnimationInputError,
    _default_window_for_product,
    _product_to_fetcher,
    model_satellite_fire_animation,
)


# ---- registration / registry growth ---------------------------------------


def test_composer_registered_as_workflow_dispatch():
    assert "run_model_satellite_fire_animation" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["run_model_satellite_fire_animation"]
    assert entry.metadata.source_class == "workflow_dispatch"
    assert entry.metadata.cacheable is False


def test_registry_grew_by_the_new_fire_animation_tools():
    # The four new tools this build adds must all be present.
    for name in (
        "fetch_wfigs_incident",
        "fetch_goes_animation",
        "fetch_viirs_day_fire",
        "run_model_satellite_fire_animation",
    ):
        assert name in TOOL_REGISTRY, f"{name} not registered"


# ---- product routing ------------------------------------------------------


def test_product_routing():
    assert _product_to_fetcher("geocolor") == "fetch_goes_animation"
    assert _product_to_fetcher("fire_temperature") == "fetch_goes_animation"
    assert _product_to_fetcher("day_fire") == "fetch_viirs_day_fire"
    assert set(GOES_PRODUCTS) | set(VIIRS_PRODUCTS) == set(SUPPORTED_PRODUCTS)


def test_product_routing_unknown_raises():
    with pytest.raises(SatelliteFireAnimationInputError):
        _product_to_fetcher("night_microphysics")


# ---- window derivation ----------------------------------------------------


def test_default_window_goes_is_about_six_and_a_half_hours():
    end = datetime(2026, 6, 22, 20, 0, tzinfo=timezone.utc)
    start, e = _default_window_for_product("geocolor", None, end)
    assert e == end
    assert (end - start).total_seconds() == pytest.approx(6.5 * 3600)


def test_default_window_viirs_is_four_days():
    end = datetime(2026, 5, 19, 22, 1, tzinfo=timezone.utc)
    start, e = _default_window_for_product("day_fire", None, end)
    assert (e - start).days == 4


def test_default_window_respects_discovery_floor():
    end = datetime(2026, 6, 22, 20, 0, tzinfo=timezone.utc)
    # Discovery at 18:00Z is LATER than end - 6.5h (13:30Z) so it floors start.
    start, _ = _default_window_for_product("geocolor", "2026-06-22T18:00:00Z", end)
    assert start == datetime(2026, 6, 22, 18, 0, tzinfo=timezone.utc)


# ---- review gate + execute (mocked registry) ------------------------------


_INCIDENT = {
    "incident_name": "Iron",
    "lat": 39.96976,
    "lon": -112.16481,
    "bbox": [-113.346, 39.57, -111.765, 41.115],
    "fire_discovery_datetime": "2026-06-20T00:00:00Z",
    "incident_size_acres": 21935,
    "poo_state": "US-UT",
}


def _fake_wfigs(name, state=None, *a, **k):
    return dict(_INCIDENT)


def _run(coro):
    return asyncio.run(coro)


def test_review_gate_stops_without_fetching_frames():
    """confirm=false returns the bbox + planned frame counts; no imagery fetched."""
    fetched_imagery = {"called": False}

    def _fake_peek(product, bbox, start, end):
        return 78 if product == "geocolor" else 12

    def _fake_goes(*a, **k):
        fetched_imagery["called"] = True
        return []

    with patch.dict(
        TOOL_REGISTRY,
        {
            "fetch_wfigs_incident": _reg(_fake_wfigs),
            "fetch_goes_animation": _reg(_fake_goes),
        },
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._peek_frame_count",
        _fake_peek,
    ):
        result = _run(
            model_satellite_fire_animation(
                "Iron",
                products=["geocolor"],
                state="UT",
                end_utc="2026-06-22T20:00:00Z",
            )
        )

    assert result["status"] == "review"
    assert result["bbox"] == _INCIDENT["bbox"]
    assert result["frame_counts"]["geocolor"] == 78
    assert result["start_utc"].endswith("Z")
    assert "presentation_text" in result
    # The review gate must NOT have fetched any imagery.
    assert fetched_imagery["called"] is False


def test_confirm_emits_postprocess_flood_frame_shape():
    """confirm=true returns frames in the postprocess_flood shape (distinct ids,
    shared preset, ISO-time name token, identical bbox)."""
    bbox = tuple(_INCIDENT["bbox"])

    def _frame(ts_iso):
        return LayerURI(
            layer_id=f"goes-anim-geocolor-{ts_iso}",
            name=f"GOES GeoColor {ts_iso} (GOES-18)",
            layer_type="raster",
            uri=f"s3://fake/{ts_iso}.tif",
            style_preset="goes_rgb_animation",
            role="context",
            units=None,
            bbox=bbox,
        )

    frames = [
        _frame("2026-06-22T13:30:00Z"),
        _frame("2026-06-22T13:35:00Z"),
        _frame("2026-06-22T13:40:00Z"),
    ]

    def _fake_goes(*a, **k):
        return frames

    def _fake_peek(product, b, s, e):
        return len(frames)

    with patch.dict(
        TOOL_REGISTRY,
        {
            "fetch_wfigs_incident": _reg(_fake_wfigs),
            "fetch_goes_animation": _reg(_fake_goes),
        },
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._peek_frame_count",
        _fake_peek,
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._safe_overlay_firms",
        _async_none,
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._safe_overlay_perimeters",
        _async_none,
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._publish_layers",
        _async_empty_dict,
    ):
        result = _run(
            model_satellite_fire_animation(
                "Iron",
                products=["geocolor"],
                state="UT",
                start_utc="2026-06-22T13:30:00Z",
                end_utc="2026-06-22T20:00:00Z",
                confirm=True,
                overlay_firms=False,
                overlay_perimeters=False,
            )
        )

    assert result["status"] == "ok"
    assert result["n_frames"] == 3
    layers = result["layers"]
    # distinct layer_ids
    ids = [lyr["layer_id"] for lyr in layers]
    assert len(set(ids)) == len(ids)
    # shared style_preset
    assert {lyr["style_preset"] for lyr in layers} == {"goes_rgb_animation"}
    # ISO-time name token present in every frame name (each its real UTC stamp)
    assert all("2026-06-22T13:" in lyr["name"] for lyr in layers)
    assert any("13:30:00Z" in lyr["name"] for lyr in layers)
    assert any("13:40:00Z" in lyr["name"] for lyr in layers)
    # role context (frames, not the primary peak)
    assert all(lyr["role"] == "context" for lyr in layers)


def test_confirm_empty_run_is_not_ok_honesty_floor():
    """A confirmed run that produced NO imagery frames must NOT read status=ok."""

    def _fake_goes(*a, **k):
        from grace2_agent.tools.fetch_goes_animation import GOESAnimEmptyError

        raise GOESAnimEmptyError("no frames")

    def _fake_peek(product, b, s, e):
        return 0

    with patch.dict(
        TOOL_REGISTRY,
        {
            "fetch_wfigs_incident": _reg(_fake_wfigs),
            "fetch_goes_animation": _reg(_fake_goes),
        },
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._peek_frame_count",
        _fake_peek,
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._safe_overlay_firms",
        _async_none,
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._safe_overlay_perimeters",
        _async_none,
    ), patch(
        "grace2_agent.workflows.model_satellite_fire_animation._publish_layers",
        _async_empty_dict,
    ):
        result = _run(
            model_satellite_fire_animation(
                "Iron",
                products=["geocolor"],
                state="UT",
                start_utc="2026-06-22T13:30:00Z",
                end_utc="2026-06-22T20:00:00Z",
                confirm=True,
                overlay_firms=False,
                overlay_perimeters=False,
            )
        )

    assert result["status"] == "empty"
    assert result["n_frames"] == 0


# ---- helpers --------------------------------------------------------------


def _reg(fn):
    """Wrap a plain callable as a RegisteredTool for patch.dict(TOOL_REGISTRY)."""
    from grace2_agent.tools import RegisteredTool
    from grace2_contracts.tool_registry import AtomicToolMetadata

    return RegisteredTool(
        metadata=AtomicToolMetadata(
            name="_fake", ttl_class="live-no-cache", source_class="workflow_dispatch", cacheable=False
        ),
        fn=fn,
        module="test",
    )


async def _async_none(*a, **k):
    return None


async def _async_empty_dict(*a, **k):
    return {}
