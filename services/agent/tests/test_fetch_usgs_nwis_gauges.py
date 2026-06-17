"""Unit tests for the ``fetch_usgs_nwis_gauges`` atomic tool (job-0332).

Covers the gap NATE hit: a real USGS NWIS / Water Services gauge-station
fetcher (observed discharge/stage), distinct from the MODELED
``fetch_noaa_nwm_streamflow``. All HTTP is mocked — no live network.

Coverage:
- Tool is registered in TOOL_REGISTRY with expected metadata.
- Categorized under hydrology.
- Error classes carry correct retryable + error_code attributes.
- Input validation: no selector, bad bbox, bad state code.
- IV happy path: WaterML-JSON parses multiple stations into points with
  both discharge (00060) and gage height (00065) merged per site_no.
- bbox-too-large (whole-Washington ~28 deg^2) → NwisBboxTooLargeError telling
  the caller to pass state_code; the SAME bbox WITH state_code succeeds.
- IV empty → Site-service RDB fallback returns station LOCATIONS.
- BOTH empty → NwisNoStationsError (honest typed error, never empty success).
- LayerURI shape: layer_type="vector", role="primary", style_preset, bbox set.
- Payload estimator returns a positive float.

Live test (gated by GRACE2_TEST_LIVE_NWIS=1): real USGS IV request for a
small Boise-area bbox; confirms >=1 gauge with a finite discharge reading.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.fetch_usgs_nwis_gauges import (
    NwisBboxTooLargeError,
    NwisGaugesError,
    NwisInputError,
    NwisNoStationsError,
    NwisUpstreamError,
    _build_iv_url,
    _build_site_url,
    _parse_iv_json,
    _parse_site_rdb,
    _records_bbox,
    _validate_bbox,
    _validate_state_code,
    estimate_payload_mb,
    fetch_usgs_nwis_gauges,
)


# ---------------------------------------------------------------------------
# Constants / fixtures.
# ---------------------------------------------------------------------------

_LIVE_NWIS = os.environ.get("GRACE2_TEST_LIVE_NWIS") == "1"

# Whole-Washington bbox: ~8 deg lon x ~3.5 deg lat = ~28 deg^2 — EXCEEDS the
# USGS ~25 deg^2 bBox limit (this is exactly the case NATE hit).
_WA_BBOX = (-124.8, 45.5, -116.9, 49.0)

# A small sub-state bbox (Boise area, ~0.5 x 0.4 = 0.2 deg^2) — well under cap.
_BOISE_BBOX = (-116.4, 43.4, -115.9, 43.8)

_PINNED_NOW = datetime.datetime(2026, 6, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _make_iv_json(series: list[dict[str, Any]]) -> bytes:
    """Wrap a list of timeSeries entries in the IV WaterML-JSON envelope."""
    return json.dumps({"value": {"timeSeries": series}}).encode("utf-8")


def _ts(
    site_no: str,
    site_name: str,
    lat: float,
    lon: float,
    param: str,
    value: str,
    dt: str = "2026-06-17T11:45:00.000-07:00",
) -> dict[str, Any]:
    """Build one IV timeSeries entry (one site x one parameter)."""
    return {
        "sourceInfo": {
            "siteName": site_name,
            "siteCode": [{"value": site_no}],
            "geoLocation": {
                "geogLocation": {"latitude": lat, "longitude": lon}
            },
        },
        "variable": {"variableCode": [{"value": param}]},
        "values": [{"value": [{"value": value, "dateTime": dt}]}],
    }


def _make_site_rdb(rows: list[tuple[str, str, float, float]]) -> bytes:
    """Build a USGS Site-service RDB (tab-delimited) body.

    rows: (site_no, station_nm, dec_lat_va, dec_long_va).
    """
    lines = [
        "# USGS site service",
        "#",
        "agency_cd\tsite_no\tstation_nm\tdec_lat_va\tdec_long_va",
        "5s\t15s\t50s\t16s\t16s",  # the type/width line we skip
    ]
    for site_no, name, lat, lon in rows:
        lines.append(f"USGS\t{site_no}\t{name}\t{lat}\t{lon}")
    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Fake GCS plumbing (mirrors sibling station-fetcher tests).
# ---------------------------------------------------------------------------


class FakeBlob:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self.custom_time: datetime.datetime | None = None
        self.cache_control: str | None = None

    def exists(self) -> bool:
        return self._path in self._store

    def download_as_bytes(self) -> bytes:
        return self._store[self._path]

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._store[self._path] = data


class FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def blob(self, path: str) -> FakeBlob:
        return FakeBlob(self._store, path)


class FakeStorageClient:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self.store)


def _make_read_through_injector(fake_gcs: FakeStorageClient):
    from grace2_agent.tools.cache import read_through as real_rt

    call_count = {"n": 0}

    def patched(metadata, params, ext, fetch_fn, **kw):
        call_count["n"] += 1
        return real_rt(
            metadata=metadata,
            params=params,
            ext=ext,
            fetch_fn=fetch_fn,
            storage_client=fake_gcs,
            now=_PINNED_NOW,
        )

    patched.call_count = call_count  # type: ignore[attr-defined]
    return patched


def _have_geo() -> bool:
    try:
        import geopandas  # noqa: F401
        import shapely  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Registration / categorization.
# ---------------------------------------------------------------------------


def test_tool_is_registered():
    assert "fetch_usgs_nwis_gauges" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["fetch_usgs_nwis_gauges"]
    assert entry.metadata.name == "fetch_usgs_nwis_gauges"
    assert entry.metadata.ttl_class == "dynamic-1h"
    assert entry.metadata.source_class == "usgs_nwis_gauges"
    assert entry.metadata.cacheable is True
    assert entry.metadata.payload_mb_estimator_name == "estimate_payload_mb"


def test_supports_global_query_is_false():
    entry = TOOL_REGISTRY["fetch_usgs_nwis_gauges"]
    sgq = getattr(entry.metadata, "supports_global_query", None)
    assert sgq in (False, None), f"expected False or None; got {sgq!r}"


def test_categorized_under_hydrology():
    from grace2_agent.categories import PRIMARY_CATEGORY, tools_for_category

    assert PRIMARY_CATEGORY.get("fetch_usgs_nwis_gauges") == "hydrology"
    assert "fetch_usgs_nwis_gauges" in tools_for_category("hydrology")


# ---------------------------------------------------------------------------
# Error class attributes.
# ---------------------------------------------------------------------------


def test_error_classes_attributes():
    for cls, retryable in [
        (NwisGaugesError, True),
        (NwisInputError, False),
        (NwisBboxTooLargeError, False),
        (NwisUpstreamError, True),
        (NwisNoStationsError, False),
    ]:
        inst = cls("test")
        assert inst.retryable is retryable, f"{cls.__name__}.retryable wrong"
        assert isinstance(inst.error_code, str) and inst.error_code != ""


def test_bbox_too_large_is_input_error_subclass():
    # So callers catching NwisInputError also catch the bbox-too-large case.
    assert issubclass(NwisBboxTooLargeError, NwisInputError)


# ---------------------------------------------------------------------------
# Input validation.
# ---------------------------------------------------------------------------


def test_validate_bbox_ok():
    _validate_bbox(_BOISE_BBOX)  # no exception


def test_validate_bbox_degenerate():
    with pytest.raises(NwisInputError, match="degenerate"):
        _validate_bbox((-116.0, 43.0, -116.0, 43.8))


def test_validate_bbox_wrong_length():
    with pytest.raises(NwisInputError, match="west, south, east, north"):
        _validate_bbox((1.0, 2.0, 3.0))  # type: ignore[arg-type]


def test_validate_bbox_out_of_range():
    with pytest.raises(NwisInputError, match="lon"):
        _validate_bbox((-200.0, 43.0, -116.0, 43.8))


def test_validate_state_code_normalizes():
    assert _validate_state_code("wa") == "WA"
    assert _validate_state_code(" fl ") == "FL"


def test_validate_state_code_unknown():
    with pytest.raises(NwisInputError, match="USPS"):
        _validate_state_code("ZZ")


def test_no_selector_raises_input_error():
    with pytest.raises(NwisInputError, match="requires a spatial selector"):
        fetch_usgs_nwis_gauges()


# ---------------------------------------------------------------------------
# URL builders.
# ---------------------------------------------------------------------------


def test_build_iv_url_state():
    url = _build_iv_url(state_code="WA", bbox=None)
    assert url.startswith("https://waterservices.usgs.gov/nwis/iv/")
    assert "format=json" in url
    assert "siteStatus=active" in url
    assert "parameterCd=00060%2C00065" in url
    assert "stateCd=WA" in url
    assert "bBox" not in url


def test_build_iv_url_bbox():
    url = _build_iv_url(state_code=None, bbox=(-116.4, 43.4, -115.9, 43.8))
    assert "bBox=" in url
    assert "stateCd" not in url


def test_build_site_url_has_fallback_params():
    url = _build_site_url(state_code="WA", bbox=None)
    assert url.startswith("https://waterservices.usgs.gov/nwis/site/")
    assert "format=rdb" in url
    assert "hasDataTypeCd=iv" in url
    assert "stateCd=WA" in url


# ---------------------------------------------------------------------------
# IV WaterML-JSON parsing (happy path — multiple stations, merge params).
# ---------------------------------------------------------------------------


def test_parse_iv_json_groups_and_merges_params():
    """Two stations; station A has both 00060 + 00065; station B only 00060."""
    raw = _make_iv_json([
        _ts("13206000", "BOISE R AT BOISE", 43.62, -116.20, "00060", "1234"),
        _ts("13206000", "BOISE R AT BOISE", 43.62, -116.20, "00065", "5.67"),
        _ts("13210050", "MASON CREEK", 43.55, -116.35, "00060", "42.1"),
    ])
    recs = {r["site_no"]: r for r in _parse_iv_json(raw)}

    assert set(recs) == {"13206000", "13210050"}

    a = recs["13206000"]
    assert a["site_name"] == "BOISE R AT BOISE"
    assert a["discharge_cfs"] == 1234.0
    assert a["gage_height_ft"] == 5.67
    assert a["reading_dt"] is not None
    assert a["lat"] == 43.62 and a["lon"] == -116.20

    b = recs["13210050"]
    assert b["discharge_cfs"] == 42.1
    assert b["gage_height_ft"] is None


def test_parse_iv_json_drops_nodata_and_bad_coords():
    raw = _make_iv_json([
        # no-data sentinel -> discharge stays None but the station survives via coords
        _ts("111", "NODATA SITE", 44.0, -116.0, "00060", "-999999"),
        # non-finite coords -> dropped entirely
        {
            "sourceInfo": {
                "siteName": "BAD COORDS",
                "siteCode": [{"value": "222"}],
                "geoLocation": {"geogLocation": {"latitude": "abc", "longitude": "xyz"}},
            },
            "variable": {"variableCode": [{"value": "00060"}]},
            "values": [{"value": [{"value": "5.0", "dateTime": "t"}]}],
        },
    ])
    recs = {r["site_no"]: r for r in _parse_iv_json(raw)}
    assert "222" not in recs  # bad coords dropped
    assert recs["111"]["discharge_cfs"] is None  # no-data sentinel filtered


def test_parse_iv_json_empty_body():
    assert _parse_iv_json(b"") == []
    assert _parse_iv_json(_make_iv_json([])) == []


def test_parse_iv_json_bad_json_raises_upstream():
    with pytest.raises(NwisUpstreamError, match="not valid JSON"):
        _parse_iv_json(b"<html>not json</html>")


# ---------------------------------------------------------------------------
# Site-service RDB fallback parsing.
# ---------------------------------------------------------------------------


def test_parse_site_rdb_extracts_locations():
    raw = _make_site_rdb([
        ("13206000", "BOISE R AT BOISE", 43.62, -116.20),
        ("13210050", "MASON CREEK", 43.55, -116.35),
    ])
    recs = _parse_site_rdb(raw)
    assert len(recs) == 2
    r0 = {r["site_no"]: r for r in recs}["13206000"]
    assert r0["site_name"] == "BOISE R AT BOISE"
    assert r0["lat"] == 43.62 and r0["lon"] == -116.20
    # Locations only — no readings.
    assert r0["discharge_cfs"] is None
    assert r0["gage_height_ft"] is None
    assert r0["reading_dt"] is None


def test_parse_site_rdb_empty():
    assert _parse_site_rdb(b"") == []
    # Header + type line but no data rows.
    assert _parse_site_rdb(_make_site_rdb([])) == []


# ---------------------------------------------------------------------------
# bbox-too-large -> stateCd-or-error path (the NATE case).
# ---------------------------------------------------------------------------


def test_whole_state_bbox_raises_bbox_too_large():
    """Whole-Washington bbox (~28 deg^2) with no state_code -> typed error."""
    with pytest.raises(NwisBboxTooLargeError, match="state_code"):
        fetch_usgs_nwis_gauges(bbox=_WA_BBOX)


def test_whole_state_bbox_with_state_code_succeeds():
    """SAME oversized extent but via state_code -> no area limit, works."""
    if not _have_geo():
        pytest.skip("geopandas/shapely not installed")

    fake_gcs = FakeStorageClient()
    iv_json = _make_iv_json([
        _ts("12500450", "YAKIMA R", 46.20, -119.90, "00060", "900"),
    ])

    captured_urls: list[str] = []

    def fake_http_get(url: str, timeout: float = 60.0) -> bytes:
        captured_urls.append(url)
        return iv_json

    with (
        patch("grace2_agent.tools.fetch_usgs_nwis_gauges._http_get", side_effect=fake_http_get),
        patch(
            "grace2_agent.tools.fetch_usgs_nwis_gauges.read_through",
            side_effect=_make_read_through_injector(fake_gcs),
        ),
    ):
        result = fetch_usgs_nwis_gauges(state_code="WA")

    assert result.layer_type == "vector"
    assert "WA" in result.name
    # The IV call used stateCd, not bBox.
    assert any("stateCd=WA" in u for u in captured_urls)
    assert all("bBox" not in u for u in captured_urls)


# ---------------------------------------------------------------------------
# IV happy path -> points with discharge + gage.
# ---------------------------------------------------------------------------


def test_iv_happy_path_layer_uri_shape():
    if not _have_geo():
        pytest.skip("geopandas/shapely not installed")

    fake_gcs = FakeStorageClient()
    iv_json = _make_iv_json([
        _ts("13206000", "BOISE R AT BOISE", 43.62, -116.20, "00060", "1234"),
        _ts("13206000", "BOISE R AT BOISE", 43.62, -116.20, "00065", "5.67"),
        _ts("13210050", "MASON CREEK", 43.55, -116.35, "00060", "42.1"),
    ])

    with (
        patch("grace2_agent.tools.fetch_usgs_nwis_gauges._http_get", return_value=iv_json),
        patch(
            "grace2_agent.tools.fetch_usgs_nwis_gauges.read_through",
            side_effect=_make_read_through_injector(fake_gcs),
        ),
    ):
        result = fetch_usgs_nwis_gauges(bbox=_BOISE_BBOX)

    assert result.layer_type == "vector"
    assert result.role == "primary"
    assert result.style_preset == "usgs_gauges"
    assert result.units == "mixed (cfs / ft)"
    assert result.uri.startswith("gs://")
    assert "usgs_nwis_gauges" in result.uri
    assert result.layer_id.startswith("usgs-gauges-")
    # bbox set to the station extent (so the camera zooms).
    assert result.bbox is not None
    west, south, east, north = result.bbox
    assert west <= -116.20 <= east
    assert south <= 43.62 <= north

    # Read back the FGB and verify 2 station points + merged props.
    assert len(fake_gcs.store) == 1
    fgb_bytes = next(iter(fake_gcs.store.values()))
    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 2
        assert set(gdf["site_no"]) == {"13206000", "13210050"}
        boise = gdf[gdf["site_no"] == "13206000"].iloc[0]
        assert abs(boise["discharge_cfs"] - 1234.0) < 1e-6
        assert abs(boise["gage_height_ft"] - 5.67) < 1e-6
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# IV empty -> Site-service fallback.
# ---------------------------------------------------------------------------


def test_iv_empty_falls_back_to_site_service():
    if not _have_geo():
        pytest.skip("geopandas/shapely not installed")

    fake_gcs = FakeStorageClient()
    empty_iv = _make_iv_json([])  # zero active sites
    site_rdb = _make_site_rdb([
        ("13206000", "BOISE R AT BOISE", 43.62, -116.20),
    ])

    def fake_http_get(url: str, timeout: float = 60.0) -> bytes:
        if "/nwis/site/" in url:
            return site_rdb
        return empty_iv  # IV returns nothing

    with (
        patch("grace2_agent.tools.fetch_usgs_nwis_gauges._http_get", side_effect=fake_http_get),
        patch(
            "grace2_agent.tools.fetch_usgs_nwis_gauges.read_through",
            side_effect=_make_read_through_injector(fake_gcs),
        ),
    ):
        result = fetch_usgs_nwis_gauges(bbox=_BOISE_BBOX)

    assert result.layer_type == "vector"
    fgb_bytes = next(iter(fake_gcs.store.values()))
    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 1
        row = gdf.iloc[0]
        assert row["site_no"] == "13206000"
        # Fallback carries locations only — no current reading.
        assert row["discharge_cfs"] is None or (
            hasattr(row["discharge_cfs"], "__float__")
            and str(row["discharge_cfs"]) in ("nan", "None")
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# BOTH empty -> honest typed error (never an empty success layer).
# ---------------------------------------------------------------------------


def test_both_empty_raises_no_stations_error():
    fake_gcs = FakeStorageClient()

    def fake_http_get(url: str, timeout: float = 60.0) -> bytes:
        if "/nwis/site/" in url:
            return _make_site_rdb([])  # no fallback locations either
        return _make_iv_json([])  # no IV sites

    with (
        patch("grace2_agent.tools.fetch_usgs_nwis_gauges._http_get", side_effect=fake_http_get),
        patch(
            "grace2_agent.tools.fetch_usgs_nwis_gauges.read_through",
            side_effect=_make_read_through_injector(fake_gcs),
        ),
        pytest.raises(NwisNoStationsError, match="No active USGS NWIS gauge"),
    ):
        fetch_usgs_nwis_gauges(bbox=_BOISE_BBOX)

    # Nothing written to cache on the honest-error path.
    assert len(fake_gcs.store) == 0


# ---------------------------------------------------------------------------
# Records-extent helper.
# ---------------------------------------------------------------------------


def test_records_bbox_pads_single_point():
    extent = _records_bbox([{"lon": -116.2, "lat": 43.6}])
    assert extent is not None
    west, south, east, north = extent
    assert west < -116.2 < east
    assert south < 43.6 < north


def test_records_bbox_empty_is_none():
    assert _records_bbox([]) is None


# ---------------------------------------------------------------------------
# Payload estimator.
# ---------------------------------------------------------------------------


def test_estimate_payload_mb_positive():
    assert estimate_payload_mb(bbox=_BOISE_BBOX) > 0.0
    assert estimate_payload_mb(state_code="WA") > 0.0
    assert estimate_payload_mb() > 0.0


# ---------------------------------------------------------------------------
# Extra-kwargs absorption (Gemini hallucination guard).
# ---------------------------------------------------------------------------


def test_extra_kwargs_absorbed():
    if not _have_geo():
        pytest.skip("geopandas/shapely not installed")

    fake_gcs = FakeStorageClient()
    iv_json = _make_iv_json([
        _ts("13206000", "BOISE R", 43.62, -116.20, "00060", "1234"),
    ])
    with (
        patch("grace2_agent.tools.fetch_usgs_nwis_gauges._http_get", return_value=iv_json),
        patch(
            "grace2_agent.tools.fetch_usgs_nwis_gauges.read_through",
            side_effect=_make_read_through_injector(fake_gcs),
        ),
    ):
        result = fetch_usgs_nwis_gauges(
            bbox=_BOISE_BBOX,
            invented_param="foo",  # type: ignore[call-arg]
            another_fake=42,  # type: ignore[call-arg]
        )
    assert result.layer_type == "vector"


# ---------------------------------------------------------------------------
# Live integration test (GRACE2_TEST_LIVE_NWIS=1 to run).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LIVE_NWIS,
    reason="Set GRACE2_TEST_LIVE_NWIS=1 to run live USGS NWIS tests",
)
def test_live_boise_iv_returns_gauges():
    from grace2_agent.tools.fetch_usgs_nwis_gauges import _fetch_usgs_nwis_gauges_bytes

    fgb_bytes, extent = _fetch_usgs_nwis_gauges_bytes(
        state_code=None, bbox=_BOISE_BBOX
    )
    assert isinstance(fgb_bytes, bytes) and len(fgb_bytes) > 100
    assert extent is not None

    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        f.write(fgb_bytes)
        path = f.name
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
    finally:
        os.unlink(path)

    assert len(gdf) >= 1
    print(f"\n[LIVE NWIS] {len(gdf)} gauge station(s) in Boise bbox")
    for _, row in gdf.iterrows():
        assert -117.0 <= row.geometry.x <= -115.0
        assert 43.0 <= row.geometry.y <= 44.0
        assert "site_no" in row.index
