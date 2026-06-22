"""Unit tests for ``fetch_goes_animation`` (fire-animation demo S3).

Coverage:
- Registration + metadata.
- ``_band_to_slider_product`` maps geocolor / fire_temperature -> SLIDER slugs.
- ``_parse_utc`` parses ISO-8601 (Z / +00:00 / space / bare date).
- ``_select_frame_indices`` keeps endpoints + even-subsamples over the cap.
- ``_build_frame_list`` windows the SLIDER time index + orders ascending +
  caps -- the frame-list assembly with real UTC.
- bbox-required + unknown band/satellite raise typed errors.
- The SLIDER timestamp helpers round-trip a real UTC valid-time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools._satellite_slider import ts_int_to_datetime, ts_int_to_iso
from grace2_agent.tools.fetch_goes_animation import (
    GOESAnimBboxRequiredError,
    GOESAnimInputError,
    _band_to_slider_product,
    _build_frame_list,
    _parse_utc,
    _select_frame_indices,
    fetch_goes_animation,
)

_UT_BBOX = (-113.346, 39.57, -111.765, 41.115)


# ---- registration ---------------------------------------------------------


def test_tool_is_registered():
    assert "fetch_goes_animation" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["fetch_goes_animation"]
    assert entry.metadata.name == "fetch_goes_animation"
    assert entry.metadata.ttl_class == "dynamic-1h"
    assert entry.metadata.source_class == "goes_animation"
    assert entry.metadata.cacheable is True


# ---- product slug mapping -------------------------------------------------


def test_band_to_slider_product_confirmed_slugs():
    # CONFIRMED slugs from define-products.js.
    assert _band_to_slider_product("geocolor") == "geocolor"
    assert _band_to_slider_product("fire_temperature") == "fire_temperature"


def test_band_to_slider_product_unknown_raises():
    with pytest.raises(GOESAnimInputError):
        _band_to_slider_product("ultraviolet")


# ---- _parse_utc -----------------------------------------------------------


def test_parse_utc_forms():
    assert _parse_utc("2026-06-22T13:30:00Z") == datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    assert _parse_utc("2026-06-22T13:30:00+00:00") == datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    assert _parse_utc("2026-06-22 13:30:00") == datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    assert _parse_utc("2026-06-22") == datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc)


def test_parse_utc_rejects_garbage():
    with pytest.raises(GOESAnimInputError):
        _parse_utc("not-a-date")


# ---- frame-list assembly --------------------------------------------------


def test_select_frame_indices_keeps_all_under_cap():
    assert _select_frame_indices(5, cap=10) == [0, 1, 2, 3, 4]


def test_select_frame_indices_subsamples_keeping_endpoints():
    kept = _select_frame_indices(100, cap=10)
    assert kept[0] == 0
    assert kept[-1] == 99
    assert len(kept) <= 10
    # strictly increasing
    assert all(kept[i] < kept[i + 1] for i in range(len(kept) - 1))


def _ts(y, mo, d, h, mi):
    return int(f"{y:04d}{mo:02d}{d:02d}{h:02d}{mi:02d}00")


def test_build_frame_list_windows_and_orders():
    # 5-min GOES cadence across a date; reverse-chron input is sorted by the
    # SLIDER reader, but _build_frame_list also sorts the windowed slice.
    all_ts = [_ts(2026, 6, 22, 13, m) for m in (0, 5, 10, 15, 20, 25, 30)]
    start = datetime(2026, 6, 22, 13, 5, tzinfo=timezone.utc)
    end = datetime(2026, 6, 22, 13, 20, tzinfo=timezone.utc)
    frames = _build_frame_list(all_ts, start, end)
    # Only 13:05..13:20 inclusive (5,10,15,20).
    assert frames == [
        _ts(2026, 6, 22, 13, 5),
        _ts(2026, 6, 22, 13, 10),
        _ts(2026, 6, 22, 13, 15),
        _ts(2026, 6, 22, 13, 20),
    ]
    # Ascending.
    assert frames == sorted(frames)


def test_build_frame_list_caps_and_keeps_endpoints():
    # 300 valid 5-min timestamps spanning ~25 h from 2026-06-22T00:00Z.
    base = datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc)
    all_ts = []
    for i in range(300):
        dt = base + timedelta(minutes=5 * i)
        all_ts.append(int(dt.strftime("%Y%m%d%H%M%S")))
    start = ts_int_to_datetime(all_ts[0])
    end = ts_int_to_datetime(all_ts[-1])
    frames = _build_frame_list(all_ts, start, end, cap=20)
    assert len(frames) <= 20
    assert frames[0] == all_ts[0]
    assert frames[-1] == all_ts[-1]


def test_build_frame_list_empty_window():
    all_ts = [_ts(2026, 6, 22, 13, 0)]
    start = datetime(2026, 6, 23, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 23, 1, 0, tzinfo=timezone.utc)
    assert _build_frame_list(all_ts, start, end) == []


def test_frame_labels_carry_real_utc():
    ts = _ts(2026, 6, 22, 19, 26)
    assert ts_int_to_iso(ts) == "2026-06-22T19:26:00Z"
    assert ts_int_to_datetime(ts).tzinfo == timezone.utc


# ---- typed-error surface --------------------------------------------------


def test_bbox_none_raises_bbox_required():
    with pytest.raises(GOESAnimBboxRequiredError):
        fetch_goes_animation(bbox=None)  # type: ignore[arg-type]


def test_unknown_band_raises():
    with pytest.raises(GOESAnimInputError):
        fetch_goes_animation(bbox=_UT_BBOX, band="xyz")


def test_unknown_satellite_raises():
    with pytest.raises(GOESAnimInputError):
        fetch_goes_animation(bbox=_UT_BBOX, satellite="himawari-9")


def test_degenerate_bbox_raises():
    with pytest.raises(GOESAnimInputError):
        fetch_goes_animation(bbox=(-112.0, 39.0, -112.0, 39.0))
