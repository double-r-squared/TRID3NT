"""Unit tests for the ``fetch_hrrr_forecast`` atomic tool (Wave 4.10 job-A2).

Coverage:
- Tool is registered in TOOL_REGISTRY with expected metadata (Wave 1.5 flags:
  ``supports_global_query=False`` + payload-MB estimator).
- Validation: bad bbox / non-CONUS bbox / bad variable / out-of-range
  forecast_hour raise typed errors with ``retryable=False``.
- FR-DC-6 cross-field: cacheable + dynamic-1h + non-empty source_class.
- Payload-MB estimator returns sensible numbers across bbox scales.
- _build_zarr_paths produces the expected outer/inner S3 paths.
- _cycle_key matches the documented mirror layout.

Live tests (env-gated ``GRACE2_TEST_LIVE_HRRR=1``):
- Live fetch of a small Fort Myers bbox via the real S3 mirror. Confirms
  the published cycle resolves, the slice clips inside the bbox, and the
  returned values are physically plausible (e.g. 2 m temp between 220 K
  and 320 K for any CONUS site at any time of year).
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.fetch_hrrr_forecast import (
    HRRRForecastEmptyError,
    HRRRForecastInputError,
    HRRRForecastUpstreamError,
    _build_zarr_paths,
    _cycle_key,
    _round_bbox_to_6dp,
    _validate_bbox,
    _validate_forecast_hour,
    _validate_variable,
    estimate_payload_mb,
    fetch_hrrr_forecast,
)

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# Fort Myers / Lee County, FL — small CONUS bbox used by mocked + live tests.
_FORT_MYERS_BBOX = (-82.0, 26.4, -81.6, 26.8)

# Non-CONUS bbox (Hawaii) — used to verify the CONUS gate.
_HAWAII_BBOX = (-158.0, 21.0, -157.5, 21.5)

_LIVE_HRRR = os.environ.get("GRACE2_TEST_LIVE_HRRR") == "1"


# ---------------------------------------------------------------------------
# Registration tests.
# ---------------------------------------------------------------------------


def test_tool_is_registered_in_registry():
    """fetch_hrrr_forecast appears in TOOL_REGISTRY with expected metadata."""
    assert "fetch_hrrr_forecast" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["fetch_hrrr_forecast"]
    assert entry.metadata.name == "fetch_hrrr_forecast"
    assert entry.metadata.ttl_class == "dynamic-1h"
    assert entry.metadata.source_class == "hrrr"
    assert entry.metadata.cacheable is True
    # Wave 1.5 flags. supports_global_query=False because HRRR is CONUS-only.
    assert entry.metadata.supports_global_query is False
    assert entry.metadata.payload_mb_estimator_name == "estimate_payload_mb"


def test_fr_dc_6_cross_field_consistency():
    """Registered metadata satisfies FR-DC-6: cacheable ⇒ ttl != live, src non-empty."""
    md = TOOL_REGISTRY["fetch_hrrr_forecast"].metadata
    assert md.cacheable is True
    assert md.ttl_class != "live-no-cache"
    assert md.source_class


def test_description_contains_when_to_use_clauses():
    """Description audit gate: docstring carries the 6-point audit shape."""
    doc = fetch_hrrr_forecast.__doc__ or ""
    # Audit-pattern markers required by the description-audit-pattern protocol.
    assert "What it does" in doc
    assert "When to use" in doc
    assert "When NOT to use" in doc
    assert "Parameters" in doc
    assert "Returns" in doc
    assert "Cross-tool dependencies" in doc
    # Target word count 150-300; we don't enforce a strict cap but check
    # the description is substantive.
    words = doc.split()
    assert len(words) > 150, f"docstring too short: {len(words)} words"


# ---------------------------------------------------------------------------
# Validation tests.
# ---------------------------------------------------------------------------


def test_degenerate_bbox_raises_input_error():
    with pytest.raises(HRRRForecastInputError):
        _validate_bbox((-82.0, 26.0, -82.0, 26.0))


def test_lon_out_of_range_raises_input_error():
    with pytest.raises(HRRRForecastInputError):
        _validate_bbox((-181.0, 26.0, -81.0, 27.0))


def test_lat_out_of_range_raises_input_error():
    with pytest.raises(HRRRForecastInputError):
        _validate_bbox((-82.0, 26.0, -81.0, 91.0))


def test_hawaii_bbox_raises_input_error_conus_only():
    """HRRR is CONUS-only; non-CONUS bbox raises HRRRForecastInputError."""
    with pytest.raises(HRRRForecastInputError, match="CONUS"):
        _validate_bbox(_HAWAII_BBOX)


def test_fort_myers_bbox_passes_validation():
    """The Fort Myers bbox is solidly inside CONUS coverage."""
    # Should not raise.
    _validate_bbox(_FORT_MYERS_BBOX)


def test_invalid_variable_raises_input_error():
    with pytest.raises(HRRRForecastInputError, match="unsupported HRRR variable"):
        _validate_variable("specific_humidity_2m")


def test_known_variables_pass_validation():
    """All four supported variables validate cleanly."""
    for v in (
        "2m_temperature",
        "10m_u_wind",
        "10m_v_wind",
        "surface_precip_1hr",
    ):
        _validate_variable(v)


def test_forecast_hour_below_zero_raises_input_error():
    with pytest.raises(HRRRForecastInputError):
        _validate_forecast_hour(-1, cycle_hour=0)


def test_forecast_hour_exceeds_standard_cycle_raises_input_error():
    """Non-00/06/12/18 cycles cap at 18 h."""
    with pytest.raises(HRRRForecastInputError, match="exceeds"):
        _validate_forecast_hour(24, cycle_hour=1)


def test_forecast_hour_48_ok_on_extended_cycle():
    """00z cycle accepts up to 48 h forecast lead."""
    _validate_forecast_hour(48, cycle_hour=0)
    _validate_forecast_hour(48, cycle_hour=6)
    _validate_forecast_hour(48, cycle_hour=12)
    _validate_forecast_hour(48, cycle_hour=18)


def test_forecast_hour_36_blocked_on_standard_cycle():
    with pytest.raises(HRRRForecastInputError):
        _validate_forecast_hour(36, cycle_hour=5)


def test_input_error_is_not_retryable():
    """HRRRForecastInputError carries retryable=False for FR-AS-11 mapping."""
    try:
        fetch_hrrr_forecast(
            bbox=_FORT_MYERS_BBOX,
            variable="not_a_real_var",
            forecast_hour=1,
        )
    except HRRRForecastInputError as exc:
        assert exc.retryable is False
        assert exc.error_code == "HRRR_FORECAST_INPUT_ERROR"
    else:
        pytest.fail("Expected HRRRForecastInputError")


def test_bad_cycle_iso_raises_input_error():
    with pytest.raises(HRRRForecastInputError, match="ISO-8601"):
        fetch_hrrr_forecast(
            bbox=_FORT_MYERS_BBOX,
            variable="2m_temperature",
            forecast_hour=1,
            cycle="not-a-date",
        )


def test_extra_kwargs_swallowed():
    """LLM-invented kwargs are absorbed by **_extra_ignored without TypeError."""
    # Should raise because of bbox validation (we don't get past it), NOT
    # because of an unknown kwarg.
    with pytest.raises(HRRRForecastInputError):
        fetch_hrrr_forecast(
            bbox=_HAWAII_BBOX,
            variable="2m_temperature",
            forecast_hour=1,
            hallucinated_param="oh_no",  # type: ignore[call-arg]
            another_fake="yes",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Helper tests.
# ---------------------------------------------------------------------------


def test_round_bbox_to_6dp():
    raw = (-82.123456789, 26.123456789, -81.987654321, 26.987654321)
    rounded = _round_bbox_to_6dp(raw)
    assert rounded == (-82.123457, 26.123457, -81.987654, 26.987654)


def test_cycle_key_format():
    d = _dt.date(2026, 6, 9)
    assert _cycle_key(d, 0) == "20260609_00z_fcst.zarr"
    assert _cycle_key(d, 12) == "20260609_12z_fcst.zarr"


def test_build_zarr_paths_for_temperature():
    """Outer + inner zarr S3 paths follow the doubly-nested mirror layout.

    Inner group is one level deep (``<outer>/<level>``) — the leaf array
    lives inside the inner group and surfaces as a data variable when both
    are opened + merged.
    """
    outer, inner = _build_zarr_paths(
        _dt.date(2026, 6, 9), 0, "2m_above_ground", "TMP"
    )
    assert outer == (
        "s3://hrrrzarr/sfc/20260609/20260609_00z_fcst.zarr/2m_above_ground/TMP"
    )
    assert inner == (
        "s3://hrrrzarr/sfc/20260609/20260609_00z_fcst.zarr/2m_above_ground/TMP/"
        "2m_above_ground"
    )


def test_build_zarr_paths_for_wind():
    outer, inner = _build_zarr_paths(
        _dt.date(2026, 6, 9), 12, "10m_above_ground", "UGRD"
    )
    assert outer.endswith("/10m_above_ground/UGRD")
    assert inner.endswith("/10m_above_ground/UGRD/10m_above_ground")


# ---------------------------------------------------------------------------
# Payload-MB estimator.
# ---------------------------------------------------------------------------


def test_estimate_payload_mb_small_bbox_returns_small_number():
    """A ~0.4° × ~0.4° bbox produces a fraction-of-a-MB payload estimate."""
    mb = estimate_payload_mb(
        bbox=_FORT_MYERS_BBOX,
        variable="2m_temperature",
        forecast_hour=1,
    )
    # Fort Myers bbox is tiny; expect well under 1 MB but at least the floor.
    assert mb >= 0.05
    assert mb < 1.0


def test_estimate_payload_mb_full_conus_in_meaningful_range():
    """A CONUS-sized bbox lands in the ~3-7 MB range."""
    full = estimate_payload_mb(
        bbox=(-130.0, 22.0, -65.0, 50.0),
        variable="surface_precip_1hr",
        forecast_hour=1,
    )
    assert 3.0 <= full <= 8.0


def test_estimate_payload_mb_none_bbox_returns_default():
    """``bbox=None`` is illegal for the tool but estimator should not raise."""
    mb = estimate_payload_mb(bbox=None)
    assert mb > 0.0


def test_estimate_payload_mb_bad_bbox_returns_default():
    """Malformed bbox arg returns the safe default rather than raising."""
    mb = estimate_payload_mb(bbox="not a bbox")  # type: ignore[arg-type]
    assert mb > 0.0


# ---------------------------------------------------------------------------
# Live test (env-gated). Requires network access to AWS S3 (anonymous).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LIVE_HRRR,
    reason="set GRACE2_TEST_LIVE_HRRR=1 to enable the live HRRR-Zarr smoke",
)
def test_live_fetch_fort_myers_2m_temperature(tmp_path, monkeypatch):
    """Live smoke: fetch 2 m temperature over Fort Myers, confirm shape + sanity.

    Bypasses the GCS cache shim by patching read_through so the test does
    not require Application Default Credentials. We exercise the live S3
    Zarr read, the LCC → EPSG:4326 reprojection, the bbox clip, and the
    COG write — that's the whole upstream-facing surface.
    """
    import tempfile

    from grace2_agent.tools import fetch_hrrr_forecast as mod

    captured: dict[str, bytes] = {}

    def fake_read_through(metadata, params, ext, fetch_fn, **_kw):  # noqa: ANN001
        # Invoke the real fetch — that's the part we want to live-test.
        data = fetch_fn()
        captured["bytes"] = data
        # Write to a tmp file and return a file:// uri so the LayerURI is well-formed.
        out = tmp_path / "live.tif"
        out.write_bytes(data)
        from grace2_agent.tools.cache import ReadThroughResult

        return ReadThroughResult(
            uri=f"file://{out}", data=data, hit=False
        )

    monkeypatch.setattr(mod, "read_through", fake_read_through)

    result = fetch_hrrr_forecast(
        bbox=_FORT_MYERS_BBOX,
        variable="2m_temperature",
        forecast_hour=1,
    )

    assert result.layer_type == "raster"
    assert result.units == "K"
    assert result.uri and result.uri.startswith("file://")
    assert "bytes" in captured and len(captured["bytes"]) > 1000

    # Verify physical-plausibility of the recovered raster.
    import rasterio
    out_path = result.uri.replace("file://", "")
    with rasterio.open(out_path) as ds:
        arr = ds.read(1)
        bounds = ds.bounds
        crs = ds.crs

    import numpy as np
    # CRS should be EPSG:4326.
    assert crs.to_epsg() == 4326
    # Bounds should be inside our requested bbox (modulo pixel snapping).
    west, south, east, north = _FORT_MYERS_BBOX
    assert bounds.left >= west - 0.1
    assert bounds.right <= east + 0.1
    assert bounds.bottom >= south - 0.1
    assert bounds.top <= north + 0.1
    # 2 m temperature for any CONUS site, any season: 220 K to 320 K is safe.
    finite = arr[np.isfinite(arr)]
    assert finite.size > 0
    assert 220.0 <= float(np.nanmin(finite)) <= 320.0
    assert 220.0 <= float(np.nanmax(finite)) <= 320.0

    # Write evidence for the live capture (sprint convention).
    evidence_dir = os.path.join(
        os.path.dirname(__file__), "..", "evidence"
    )
    os.makedirs(evidence_dir, exist_ok=True)
    evidence_file = os.path.join(evidence_dir, "hrrr_live.txt")
    with open(evidence_file, "w") as f:
        f.write(
            f"fetch_hrrr_forecast live smoke\n"
            f"  bbox={_FORT_MYERS_BBOX}\n"
            f"  variable=2m_temperature\n"
            f"  forecast_hour=1\n"
            f"  cog_bytes={len(captured['bytes'])}\n"
            f"  shape={arr.shape}\n"
            f"  bounds={bounds}\n"
            f"  min={float(np.nanmin(finite)):.2f} K\n"
            f"  max={float(np.nanmax(finite)):.2f} K\n"
            f"  mean={float(np.nanmean(finite)):.2f} K\n"
        )
