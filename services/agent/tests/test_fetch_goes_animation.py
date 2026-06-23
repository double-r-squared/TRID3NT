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


# ---- emitted name token: "GOES <ProductLabel> step <N> <ISO> (<SAT>)" -----
#
# The scrubber-group contract: each frame name carries (a) a "step <N>" MONOTONIC
# token (the web detectSequentialGroups grouping value -- a raw ISO is NOT a
# recognized token), (b) the product label so GeoColor / Fire Temperature form
# TWO distinct stems (two scrubber groups), and (c) the real UTC valid-time as
# the per-frame display label. ``<N>`` is the position in the shared windowed
# frame list, so the same step maps to the same SLIDER timestamp across both GOES
# products -> the two scrubbers stay time-synchronized.


class _FakeReadResult:
    def __init__(self, uri):
        self.uri = uri


def _patch_slider_for_three_frames(monkeypatch, product_seen):
    """Stub the SLIDER substrate so fetch_goes_animation emits 3 deterministic frames."""
    from grace2_agent.tools import fetch_goes_animation as mod

    frame_ts = [
        _ts(2026, 6, 22, 18, 0),
        _ts(2026, 6, 22, 18, 5),
        _ts(2026, 6, 22, 18, 10),
    ]

    def _fake_timestamps(satellite, sector, product):
        product_seen.append(product)
        return list(frame_ts)

    def _fake_read_through(metadata, params, ext, fetch_fn):
        # Never actually fetch tiles; return a deterministic per-frame URI.
        return _FakeReadResult(uri=f"s3://fake/{params['product']}-{params['ts_int']}.tif")

    monkeypatch.setattr(mod, "fetch_slider_timestamps", _fake_timestamps)
    monkeypatch.setattr(mod, "read_through", _fake_read_through)
    monkeypatch.setattr(mod, "pick_zoom_for_aoi", lambda *a, **k: 5)
    return frame_ts


def test_emitted_name_carries_step_token_and_iso(monkeypatch):
    seen: list[str] = []
    frame_ts = _patch_slider_for_three_frames(monkeypatch, seen)
    layers = fetch_goes_animation(
        bbox=_UT_BBOX,
        band="fire_temperature",
        satellite="goes-18",
        start_utc="2026-06-22T17:30:00Z",
        end_utc="2026-06-22T18:30:00Z",
    )
    assert len(layers) == len(frame_ts) == 3
    # Each name: "GOES Fire Temperature step <N> <ISO> (GOES-18)".
    for n, (layer, ts) in enumerate(zip(layers, frame_ts), start=1):
        iso = ts_int_to_iso(ts)
        assert layer.name == f"GOES Fire Temperature step {n} {iso} (GOES-18)"
    # Monotonic, distinct step values 1..3 in order.
    import re

    steps = [int(re.search(r"step (\d+)", lyr.name).group(1)) for lyr in layers]
    assert steps == [1, 2, 3]
    # Shared style preset (scrubber-group contract).
    assert {lyr.style_preset for lyr in layers} == {"goes_rgb_animation"}


def test_two_goes_products_are_time_synchronized_by_step(monkeypatch):
    """GeoColor + Fire Temperature over the SAME window share the same step->ISO
    mapping, so step <N> picks the SAME valid-time in both -> synchronized."""
    seen: list[str] = []
    frame_ts = _patch_slider_for_three_frames(monkeypatch, seen)

    def _names_for(band):
        layers = fetch_goes_animation(
            bbox=_UT_BBOX,
            band=band,
            satellite="goes-18",
            start_utc="2026-06-22T17:30:00Z",
            end_utc="2026-06-22T18:30:00Z",
        )
        # step value -> ISO valid-time it points at, per product.
        out = {}
        import re

        for lyr in layers:
            step = int(re.search(r"step (\d+)", lyr.name).group(1))
            iso = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", lyr.name).group(1)
            out[step] = iso
        return out

    geocolor = _names_for("geocolor")
    fire = _names_for("fire_temperature")
    # Same step keys.
    assert set(geocolor) == set(fire) == {1, 2, 3}
    # CO-TEMPORAL: step N is the SAME valid-time in both products.
    for step in (1, 2, 3):
        assert geocolor[step] == fire[step]
    # And the two products produce DISTINCT product labels (-> two stems/groups).
    # (verified at the name level above; here assert the labels differ)
    assert geocolor[1] == fire[1]  # same time
    # The product token differs in the stem (GeoColor vs Fire Temperature):
    g_layers = fetch_goes_animation(
        bbox=_UT_BBOX, band="geocolor", satellite="goes-18",
        start_utc="2026-06-22T17:30:00Z", end_utc="2026-06-22T18:30:00Z",
    )
    f_layers = fetch_goes_animation(
        bbox=_UT_BBOX, band="fire_temperature", satellite="goes-18",
        start_utc="2026-06-22T17:30:00Z", end_utc="2026-06-22T18:30:00Z",
    )
    assert "GeoColor" in g_layers[0].name
    assert "Fire Temperature" in f_layers[0].name


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
