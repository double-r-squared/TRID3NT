"""Unit tests for ``fetch_goes_archive_animation`` (fire-animation demo B+C).

The HISTORICAL Fire Temperature animation built from the RAW noaa-goes18 S3
ABI-L2-MCMIPC archive (PATH B). Coverage:

- Registration + metadata + the SAME style preset / output shape as
  ``fetch_goes_animation`` (Track A + the scrubber consume it unchanged).
- The Fire Temperature band-math recipe: the C07 BT 0-60 C RED stretch, the C06
  100 % GREEN + C05 75 % BLUE reflectance stretches, gamma 1, per-channel
  clip [0,1], scaled to 0-255 uint8.
- A HOT-PIXEL red->white range assertion: a fire core (hot 3.9um BT + high
  2.2um + 1.6um reflectance) reads red->yellow->white; a cool/dark pixel reads
  black; a warm-but-not-saturated pixel reads pure red.
- CF scale_factor/add_offset is applied (rasterio NETCDF does NOT auto-apply).
- The historical S3 key listing: parse the _s<...> start-time, window, order
  ascending, dedupe, even-subsample to the frame cap.
- bbox-required + unknown band/satellite raise typed errors.
- The emitted "GOES Fire Temperature (Archive) step <N> <ISO> (<SAT>)" name
  token (the scrubber-group contract).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools import fetch_goes_archive_animation as mod
from grace2_agent.tools.fetch_goes_archive_animation import (
    FIRE_TEMP_BLUE_REFL_MAX,
    FIRE_TEMP_GREEN_REFL_MAX,
    FIRE_TEMP_RED_KELVIN_RANGE,
    GOESArchiveBboxRequiredError,
    GOESArchiveEmptyError,
    GOESArchiveInputError,
    GOESArchiveUpstreamError,
    _fire_temperature_rgb,
    _key_start_datetime,
    _list_archive_keys_in_window,
    _parse_utc,
    _select_window_keys,
    _stretch_brightness_temp_red,
    _stretch_reflectance,
    fetch_goes_archive_animation,
)

# Utah fire cluster (Iron + Hastings + the eastern-NV fires) from the design spike.
_UT_BBOX = (-114.05, 37.0, -109.04, 42.0)


# ---- registration ---------------------------------------------------------


def test_tool_is_registered():
    assert "fetch_goes_archive_animation" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["fetch_goes_archive_animation"]
    assert entry.metadata.name == "fetch_goes_archive_animation"
    assert entry.metadata.ttl_class == "dynamic-1h"
    assert entry.metadata.source_class == "goes_animation"
    assert entry.metadata.cacheable is True


# ---- _parse_utc -----------------------------------------------------------


def test_parse_utc_forms():
    assert _parse_utc("2026-06-22T13:30:00Z") == datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    assert _parse_utc("2026-06-22T13:30:00+00:00") == datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    assert _parse_utc("2026-06-22 13:30:00") == datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    assert _parse_utc("2026-06-22") == datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc)


def test_parse_utc_rejects_garbage():
    with pytest.raises(GOESArchiveInputError):
        _parse_utc("not-a-date")


# ---- ABI key start-time parsing (JULIAN day-of-year) ----------------------


def test_key_start_datetime_parses_julian_doy():
    # DOY 173 = 2026-06-22 (2026 is not a leap year). 19:26:00.1 -> 19:26:00.
    key = "ABI-L2-MCMIPC/2026/173/19/OR_ABI-L2-MCMIPC-M6_G18_s20261731926001_e20261731928374_c20261731928....nc"
    dt = _key_start_datetime(key)
    assert dt == datetime(2026, 6, 22, 19, 26, 0, tzinfo=timezone.utc)
    assert dt.tzinfo == timezone.utc


def test_key_start_datetime_doy_001_is_jan_1():
    key = "OR_ABI-L2-MCMIPC-M6_G18_s20240010000000_e..._c....nc"
    assert _key_start_datetime(key) == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_key_start_datetime_none_when_no_start_time():
    assert _key_start_datetime("ABI-L2-MCMIPC/2026/173/19/") is None


# ---- frame-list assembly --------------------------------------------------


def test_select_window_keys_keeps_all_under_cap():
    keys = [f"k{i}" for i in range(5)]
    assert _select_window_keys(keys, cap=10) == keys


def test_select_window_keys_subsamples_keeping_endpoints():
    keys = [f"k{i}" for i in range(100)]
    kept = _select_window_keys(keys, cap=10)
    assert kept[0] == "k0"
    assert kept[-1] == "k99"
    assert len(kept) <= 10
    # Strictly increasing (preserves the ascending input order).
    idx = [int(k[1:]) for k in kept]
    assert all(idx[i] < idx[i + 1] for i in range(len(idx) - 1))


def _mk_key(dt: datetime) -> str:
    """Build a synthetic MCMIPC key with the given start datetime (JULIAN DOY)."""
    doy = dt.timetuple().tm_yday
    s = f"{dt.year:04d}{doy:03d}{dt.hour:02d}{dt.minute:02d}{dt.second:02d}0"
    return (
        f"ABI-L2-MCMIPC/{dt.year}/{doy:03d}/{dt.hour:02d}/"
        f"OR_ABI-L2-MCMIPC-M6_G18_s{s}_e..._c....nc"
    )


def test_list_archive_keys_in_window_windows_and_orders(monkeypatch):
    """The S3 walk lists every touched hour-partition, parses each key's start-
    time, keeps the in-window ones, and returns them ASCENDING by time."""
    base = datetime(2026, 6, 22, 13, 0, tzinfo=timezone.utc)
    # 5-min cadence across 13:00..14:00; the listing returns reverse-chron tiles
    # (S3 is unordered) so the function must sort.
    all_times = [base + timedelta(minutes=5 * i) for i in range(13)]  # 13:00..14:00
    keys_by_hour: dict[tuple[int, int], list[str]] = {}
    for dt in all_times:
        keys_by_hour.setdefault((dt.hour,), []).append(_mk_key(dt))
    # Shuffle within each hour to prove we sort.
    for v in keys_by_hour.values():
        v.reverse()

    def _fake_list(bucket, prefix, *, session=None):
        # prefix = ABI-L2-MCMIPC/2026/173/<HH>/
        hh = int(prefix.rstrip("/").split("/")[-1])
        return keys_by_hour.get((hh,), [])

    monkeypatch.setattr(mod, "_list_keys_for_prefix", _fake_list)

    start = datetime(2026, 6, 22, 13, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    pairs = _list_archive_keys_in_window("goes-18", start, end)
    times = [t for t, _ in pairs]
    # 13:10, 13:15, 13:20, 13:25, 13:30 inclusive.
    assert times == [
        datetime(2026, 6, 22, 13, m, tzinfo=timezone.utc) for m in (10, 15, 20, 25, 30)
    ]
    # Ascending.
    assert times == sorted(times)


def test_list_archive_keys_empty_window_returns_empty(monkeypatch):
    monkeypatch.setattr(mod, "_list_keys_for_prefix", lambda *a, **k: [])
    start = datetime(2026, 6, 22, 13, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    assert _list_archive_keys_in_window("goes-18", start, end) == []


def test_list_archive_keys_all_listings_fail_raises_upstream(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("S3 down")

    monkeypatch.setattr(mod, "_list_keys_for_prefix", _boom)
    start = datetime(2026, 6, 22, 13, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    with pytest.raises(GOESArchiveUpstreamError):
        _list_archive_keys_in_window("goes-18", start, end)


def test_list_archive_keys_unknown_satellite_raises():
    start = datetime(2026, 6, 22, 13, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc)
    with pytest.raises(GOESArchiveInputError):
        _list_archive_keys_in_window("himawari-9", start, end)


# ---- Fire Temperature band math (the testable recipe core) ----------------


def test_red_stretch_brightness_temp_endpoints():
    """RED: 273.15 K (0 C) -> 0.0; 333.15 K (60 C) -> 1.0; clipped; gamma 1."""
    lo, hi = FIRE_TEMP_RED_KELVIN_RANGE
    arr = np.array([lo, (lo + hi) / 2.0, hi, lo - 50.0, hi + 50.0], dtype=np.float32)
    red = _stretch_brightness_temp_red(arr)
    assert red[0] == pytest.approx(0.0)        # 0 C
    assert red[1] == pytest.approx(0.5)        # 30 C -> midpoint (gamma 1, linear)
    assert red[2] == pytest.approx(1.0)        # 60 C
    assert red[3] == pytest.approx(0.0)        # below range clips to 0
    assert red[4] == pytest.approx(1.0)        # above range clips to 1


def test_red_stretch_nan_to_zero():
    red = _stretch_brightness_temp_red(np.array([np.nan, 303.15], dtype=np.float32))
    assert red[0] == pytest.approx(0.0)  # no-data reads dark, not NaN


def test_green_reflectance_stretch_100pct():
    """GREEN: 0..1.0 reflectance -> 0..1 (100 %); gamma 1; clip."""
    g = _stretch_reflectance(
        np.array([0.0, 0.5, 1.0, 1.5], dtype=np.float32), FIRE_TEMP_GREEN_REFL_MAX
    )
    assert g[0] == pytest.approx(0.0)
    assert g[1] == pytest.approx(0.5)
    assert g[2] == pytest.approx(1.0)
    assert g[3] == pytest.approx(1.0)  # over 100 % clips


def test_blue_reflectance_stretch_75pct():
    """BLUE: 0..0.75 reflectance -> 0..1 (75 %); so 0.75 saturates, 0.375 -> 0.5."""
    b = _stretch_reflectance(
        np.array([0.0, 0.375, 0.75, 1.0], dtype=np.float32), FIRE_TEMP_BLUE_REFL_MAX
    )
    assert b[0] == pytest.approx(0.0)
    assert b[1] == pytest.approx(0.5)
    assert b[2] == pytest.approx(1.0)
    assert b[3] == pytest.approx(1.0)  # above 0.75 clips


def test_fire_temperature_rgb_shape_and_dtype():
    c07 = np.full((4, 5), 303.15, dtype=np.float32)  # 30 C
    c06 = np.full((4, 5), 0.5, dtype=np.float32)
    c05 = np.full((4, 5), 0.375, dtype=np.float32)
    rgb = _fire_temperature_rgb(c07, c06, c05)
    assert rgb.shape == (3, 4, 5)
    assert rgb.dtype == np.uint8


def test_fire_temperature_hot_pixel_reads_red_to_white():
    """HOT-PIXEL recipe assertion: a saturated fire core (very hot 3.9um BT + high
    2.2um + 1.6um reflectance) reads WHITE (R=G=B=255); a hot-but-low-reflectance
    pixel reads pure RED; a moderately warm pixel reads partial red; a cool/dark
    pixel reads near-black. This is the red->yellow->white fire ramp."""
    # One 3-pixel row: [cool, warm-only-red, white-hot-core].
    # RED channel (C07 BT, K): 263 K (cold, below 0 C) | 303.15 K (30 C) | 343 K (>60 C, saturates)
    c07 = np.array([[263.0, 303.15, 343.0]], dtype=np.float32)
    # GREEN channel (C06 refl): 0 | 0 | 1.0 (max -> 100 %)
    c06 = np.array([[0.0, 0.0, 1.0]], dtype=np.float32)
    # BLUE channel (C05 refl): 0 | 0 | 0.75 (max -> 75 % saturates)
    c05 = np.array([[0.0, 0.0, 0.75]], dtype=np.float32)
    rgb = _fire_temperature_rgb(c07, c06, c05)

    # Pixel 0 (cool/dark): all channels near 0 -> black.
    assert tuple(int(v) for v in rgb[:, 0, 0]) == (0, 0, 0)
    # Pixel 1 (warm, only thermal): R high (30 C -> ~0.5 -> ~128), G=B=0 -> pure red.
    assert rgb[0, 0, 1] > 100      # red present
    assert rgb[1, 0, 1] == 0       # no green
    assert rgb[2, 0, 1] == 0       # no blue
    # Pixel 2 (white-hot core): R saturates, G + B climb -> white (all 255).
    assert tuple(int(v) for v in rgb[:, 0, 2]) == (255, 255, 255)
    # The ramp: red rises across the row (cold -> warm -> hot).
    assert rgb[0, 0, 0] < rgb[0, 0, 1] < rgb[0, 0, 2]


def test_fire_temperature_rgb_co_registration_shape_mismatch_raises():
    c07 = np.zeros((4, 5), dtype=np.float32)
    c06 = np.zeros((4, 6), dtype=np.float32)  # wrong width
    c05 = np.zeros((4, 5), dtype=np.float32)
    with pytest.raises(GOESArchiveUpstreamError):
        _fire_temperature_rgb(c07, c06, c05)


# ---- CF scale_factor/add_offset is applied --------------------------------


def test_cf_scaling_applied_in_reproject(monkeypatch, tmp_path):
    """``_reproject_fire_temperature`` must apply CF scale_factor/add_offset per
    band (rasterio NETCDF does NOT auto-apply). We stub netCDF4 + rasterio so the
    test asserts the raw int16 DN are unscaled into physical units BEFORE the Fire-
    Temp composite -- the band-math contract.

    We use a DISTINCTIVE scale/offset so a missing-CF bug is unmissable: C07
    scale=0.05, offset=200 -> DN 300 * 0.05 + 200 = 215 K (below 0 C -> red 0). If
    CF scaling were NOT applied, DN 300 would read as 300 K (= 26.85 C -> red ~114).
    The two outcomes are far apart, so RED==0 proves the CF transform ran. All raw
    DN stay inside the ABI valid_range [0, 4095] (a DN above 4095 is masked to NaN
    as out-of-range, so test inputs must respect that).
    """
    import grace2_agent.tools.fetch_goes_archive_animation as m

    # Fake netCDF4: a Dataset whose variables carry scale_factor/add_offset.
    class _FakeVar:
        def __init__(self, scale, offset, fill):
            self.scale_factor = scale
            self.add_offset = offset
            self._FillValue = fill

    class _FakeDataset:
        def __init__(self, path):
            self.variables = {
                "CMI_C07": _FakeVar(0.05, 200.0, -1),   # DN 300 -> 215 K (cold!)
                "CMI_C06": _FakeVar(0.0001, 0.0, -1),   # DN 4000 -> 0.4 refl
                "CMI_C05": _FakeVar(0.0001, 0.0, -1),   # DN 3000 -> 0.3 refl
            }

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeNetCDF4:
        Dataset = _FakeDataset

    monkeypatch.setattr(m, "netCDF4", _FakeNetCDF4, raising=False)
    # Patch the lazily-imported netCDF4 inside the function via sys.modules so the
    # `import netCDF4` line picks up the fake.
    import sys

    monkeypatch.setitem(sys.modules, "netCDF4", _FakeNetCDF4)

    # Fake rasterio: src.open returns a handle; reproject fills the destination
    # with a fixed raw DN per variable (keyed off the subdataset URI).
    raw_dn = {"CMI_C07": 300, "CMI_C06": 4000, "CMI_C05": 3000}

    class _FakeSrc:
        def __init__(self, var):
            self.var = var
            self.crs = "GEOSTATIONARY"
            self.nodata = None
            self.transform = object()

        def close(self):
            pass

    class _FakeRasterio:
        @staticmethod
        def open(uri):
            var = uri.rsplit(":", 1)[-1]
            return _FakeSrc(var)

        @staticmethod
        def band(src, n):
            return src.var

    def _fake_reproject(source=None, destination=None, **kw):
        # ``source`` is the var name (from FakeRasterio.band). Fill dest with the
        # raw DN so the CF transform downstream is observable.
        destination[:] = raw_dn[source]

    from rasterio.transform import from_bounds as _real_from_bounds

    class _FakeWarp:
        Resampling = type("R", (), {"nearest": 0})()
        reproject = staticmethod(_fake_reproject)

    monkeypatch.setattr(m, "rasterio", _FakeRasterio, raising=False)
    monkeypatch.setitem(sys.modules, "rasterio", _FakeRasterio)
    monkeypatch.setitem(sys.modules, "rasterio.warp", _FakeWarp)
    # rasterio.transform.from_bounds is real (pure math); keep it.
    import types

    _transform_mod = types.SimpleNamespace(from_bounds=_real_from_bounds)
    monkeypatch.setitem(sys.modules, "rasterio.transform", _transform_mod)

    bbox = (-112.0, 39.0, -111.9, 39.08)
    rgb, transform, w, h = m._reproject_fire_temperature("/fake/path.nc", bbox)
    assert rgb.shape[0] == 3
    # C07 DN 300 with scale=0.05 offset=200 -> 215 K (< 273.15 = 0 C) -> RED == 0.
    # If CF scaling were NOT applied, DN 300 read as 300 K -> RED ~114. So RED==0
    # PROVES scale_factor/add_offset ran.
    assert int(rgb[0].max()) == 0, "RED must be 0 (215 K is below 0 C) -- proves CF scaling applied"
    # C06 DN 4000 * 0.0001 = 0.4 refl over 100 % max -> GREEN round(0.4*255) = 102.
    # If CF scaling were skipped, DN 4000 read raw would be masked (>4095 boundary)
    # or wildly wrong; 102 proves scale_factor ran on GREEN too.
    assert int(rgb[1].max()) == 102
    # C05 DN 3000 * 0.0001 = 0.3 refl over 0.75 max -> 0.4 -> BLUE round(0.4*255) = 102.
    assert int(rgb[2].max()) == 102


# ---- typed-error surface --------------------------------------------------


def test_bbox_none_raises_bbox_required():
    with pytest.raises(GOESArchiveBboxRequiredError):
        fetch_goes_archive_animation(bbox=None)  # type: ignore[arg-type]


def test_unknown_satellite_raises():
    with pytest.raises(GOESArchiveInputError):
        fetch_goes_archive_animation(bbox=_UT_BBOX, satellite="himawari-9")


def test_unknown_band_raises():
    with pytest.raises(GOESArchiveInputError):
        fetch_goes_archive_animation(bbox=_UT_BBOX, band="geocolor")


def test_degenerate_bbox_raises():
    with pytest.raises(GOESArchiveInputError):
        fetch_goes_archive_animation(bbox=(-112.0, 39.0, -112.0, 39.0))


def test_empty_window_raises_typed_empty(monkeypatch):
    """No archived frames in the window -> honest typed empty (never blank anim)."""
    monkeypatch.setattr(mod, "_list_archive_keys_in_window", lambda *a, **k: [])
    with pytest.raises(GOESArchiveEmptyError):
        fetch_goes_archive_animation(
            bbox=_UT_BBOX,
            satellite="goes-18",
            start_utc="2020-01-01T00:00:00Z",
            end_utc="2020-01-01T01:00:00Z",
        )


# ---- emitted name token: matches the fetch_goes_animation scrubber contract --


class _FakeReadResult:
    def __init__(self, uri):
        self.uri = uri


def test_emitted_name_carries_step_token_iso_and_style(monkeypatch):
    """The frames carry the SAME scrubber-group contract as fetch_goes_animation:
    a "step <N>" monotonic token, the product label stem, the ISO valid-time, and
    the shared "goes_rgb_animation" style preset (so Track A + the web scrubber
    consume the archive frames unchanged)."""
    times = [datetime(2026, 6, 22, 18, m, tzinfo=timezone.utc) for m in (0, 5, 10)]
    pairs = [(t, _mk_key(t)) for t in times]

    monkeypatch.setattr(mod, "_list_archive_keys_in_window", lambda *a, **k: list(pairs))

    def _fake_read_through(metadata, params, ext, fetch_fn):
        return _FakeReadResult(uri=f"s3://fake/{params['ts_start']}.tif")

    monkeypatch.setattr(mod, "read_through", _fake_read_through)

    layers = fetch_goes_archive_animation(
        bbox=_UT_BBOX,
        satellite="goes-18",
        start_utc="2026-06-22T17:30:00Z",
        end_utc="2026-06-22T18:30:00Z",
    )
    assert len(layers) == 3
    for n, (layer, t) in enumerate(zip(layers, times), start=1):
        iso = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert layer.name == f"GOES Fire Temperature (Archive) step {n} {iso} (GOES-18)"
        assert layer.layer_type == "raster"
        assert layer.role == "context"
        assert layer.style_preset == "goes_rgb_animation"
    # Monotonic step values 1..3.
    steps = [int(re.search(r"step (\d+)", lyr.name).group(1)) for lyr in layers]
    assert steps == [1, 2, 3]
    # Shared style preset across the group + distinct "(Archive)" stem.
    assert {lyr.style_preset for lyr in layers} == {"goes_rgb_animation"}
    assert all("(Archive)" in lyr.name for lyr in layers)


def test_run_honesty_floor_all_frames_empty(monkeypatch):
    """Every frame empty/failed -> the run honesty-floors (typed empty, no blank
    animation)."""
    times = [datetime(2026, 6, 22, 18, m, tzinfo=timezone.utc) for m in (0, 5, 10)]
    pairs = [(t, _mk_key(t)) for t in times]
    monkeypatch.setattr(mod, "_list_archive_keys_in_window", lambda *a, **k: list(pairs))

    def _always_empty(metadata, params, ext, fetch_fn):
        raise GOESArchiveEmptyError("AOI crop empty")

    monkeypatch.setattr(mod, "read_through", _always_empty)
    with pytest.raises(GOESArchiveEmptyError):
        fetch_goes_archive_animation(
            bbox=_UT_BBOX,
            satellite="goes-18",
            start_utc="2026-06-22T17:30:00Z",
            end_utc="2026-06-22T18:30:00Z",
        )


def test_start_after_end_raises():
    with pytest.raises(GOESArchiveInputError):
        fetch_goes_archive_animation(
            bbox=_UT_BBOX,
            start_utc="2026-06-22T20:00:00Z",
            end_utc="2026-06-22T13:00:00Z",
        )
