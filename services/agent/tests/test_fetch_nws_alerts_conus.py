"""Unit tests for the ``fetch_nws_alerts_conus`` atomic tool (job-0105).

Coverage:
- Tool is registered in TOOL_REGISTRY with expected metadata.
- Mocked: 50-feature CONUS response → 50-feature FlatGeobuf written through cache.
- event_types filter narrows client-side (e.g. Hurricane Warning only from a
  mixed 50-feature sample).
- Cache miss → fetch_fn invoked; cache hit → fetch_fn skipped (deduplication).
- User-Agent header verified present on every NWS GET (NWS 403s without it).
- Invalid status / non-string event_types raise typed input errors.
- 403 / network failure map to typed NWSConusUpstreamError(retryable=True).
- URL contains status param but NO area/point param (CONUS-wide variant).
- Geographic-correctness gate (job-0086): every alert polygon in the returned
  FGB whose centroid is in CONUS+territories+marine-zones envelope.
- Live (env GRACE2_TEST_LIVE_NWS_CONUS=1): real api.weather.gov returns ≥0
  features; if non-zero, polygon centroids fall inside the US envelope.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.fetch_nws_alerts_conus import (
    NWSConusError,
    NWSConusInputError,
    NWSConusUpstreamError,
    _build_nws_conus_url,
    _fetch_nws_alerts_conus_bytes,
    _filter_features_by_event_types,
    _geojson_to_fgb,
    fetch_nws_alerts_conus,
)


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_PINNED_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)

# Marker for live tests
_LIVE_NWS_CONUS = os.environ.get("GRACE2_TEST_LIVE_NWS_CONUS") == "1"

# Generous US+territories+marine-zones envelope for the geographic-correctness
# gate. Covers CONUS, AK, HI, PR/VI, GU/MP/AS, and the marine zones offshore.
# Centroid of any NWS alert polygon should fall inside this box.
_US_ENVELOPE_LONS = (-180.0, -64.0)   # AK westernmost ~ -179.7 to PR east ~ -64.5
_US_ENVELOPE_LATS = (13.0, 72.0)       # AS ~ -14 but we keep N hemisphere for v0.1
# NOTE: American Samoa is south of the equator (~-14°). We deliberately exclude
# it from the gate envelope — NWS issues very few AS alerts and the gate would
# otherwise be too permissive globally. Surfaced as OQ-0105-AS-LATITUDE-GATE.


def _fake_fgb_bytes(tag: str = "TEST") -> bytes:
    return b"FAKE_NWS_CONUS_FGB_" + tag.encode() + b"\x00" * 16


# ---------------------------------------------------------------------------
# Fake GCS plumbing (mirrors existing test patterns).
# ---------------------------------------------------------------------------


class FakeBlob:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self.custom_time: datetime | None = None
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
    """Patch helper: inject fake GCS into the real read_through."""
    from grace2_agent.tools.cache import read_through as real_rt

    def patched(metadata, params, ext, fetch_fn, **kw):
        return real_rt(
            metadata=metadata,
            params=params,
            ext=ext,
            fetch_fn=fetch_fn,
            storage_client=fake_gcs,
            now=_PINNED_NOW,
        )
    return patched


def _make_feature(
    event: str,
    severity: str,
    lon_min: float,
    lat_min: float,
    *,
    feature_id: str | None = None,
) -> dict:
    """Build one synthetic NWS GeoJSON feature centered roughly at given coords."""
    lon_max = lon_min + 0.5
    lat_max = lat_min + 0.5
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [lon_min, lat_min], [lon_max, lat_min],
                [lon_max, lat_max], [lon_min, lat_max],
                [lon_min, lat_min],
            ]],
        },
        "properties": {
            "id": feature_id or f"alert-{event}-{lon_min}-{lat_min}",
            "event": event,
            "headline": f"{event} for synthetic test feature",
            "description": f"{event} description for testing.",
            "severity": severity,
            "urgency": "Expected",
            "certainty": "Likely",
            "senderName": "NWS Test Office",
            "areaDesc": "Test Area",
            "category": "Met",
            "messageType": "Alert",
            "status": "Actual",
        },
    }


def _sample_conus_geojson(n_features: int = 50) -> dict:
    """Synthetic CONUS-wide NWS response with ``n_features`` mixed alerts.

    Distributes features across the CONUS+AK+HI+PR envelope so the geographic
    gate is exercised. Mixes 5 event types so the client-side filter has
    something to narrow.
    """
    events = [
        ("Hurricane Warning", "Extreme"),
        ("Flood Warning", "Severe"),
        ("Severe Thunderstorm Warning", "Severe"),
        ("Winter Storm Warning", "Moderate"),
        ("Heat Advisory", "Minor"),
    ]
    # Spread anchor points across CONUS + AK + HI + PR.
    anchors = [
        (-122.0, 47.0),  # WA
        (-105.0, 40.0),  # CO
        (-95.0, 35.0),   # OK
        (-87.0, 41.0),   # IL
        (-80.0, 35.0),   # NC
        (-81.0, 26.0),   # FL
        (-115.0, 36.0),  # NV
        (-100.0, 45.0),  # SD
        (-90.0, 30.0),   # LA
        (-75.0, 40.0),   # NJ
        (-150.0, 61.0),  # AK
        (-156.0, 20.0),  # HI
        (-66.0, 18.3),   # PR
    ]
    features: list[dict] = []
    for i in range(n_features):
        event, severity = events[i % len(events)]
        lon, lat = anchors[i % len(anchors)]
        # Stagger so polygons don't all coincide.
        lon += (i // len(anchors)) * 0.3
        lat += (i // len(anchors)) * 0.2
        features.append(_make_feature(event, severity, lon, lat, feature_id=f"alert-{i}"))
    return {"type": "FeatureCollection", "features": features}


def _centroid(polygon_coords: list[list[list[float]]]) -> tuple[float, float]:
    """Naive arithmetic-mean centroid of a polygon's outer ring."""
    ring = polygon_coords[0]
    # Drop the closing vertex (NWS rings always close).
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return (lon, lat)


# ---------------------------------------------------------------------------
# Registration tests (no network needed).
# ---------------------------------------------------------------------------


def test_tool_is_registered_in_registry():
    """fetch_nws_alerts_conus appears in TOOL_REGISTRY with expected metadata."""
    assert "fetch_nws_alerts_conus" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["fetch_nws_alerts_conus"]
    assert entry.metadata.name == "fetch_nws_alerts_conus"
    assert entry.metadata.ttl_class == "dynamic-1h"
    assert entry.metadata.source_class == "nws_alerts_conus"
    assert entry.metadata.cacheable is True


# ---------------------------------------------------------------------------
# URL building tests.
# ---------------------------------------------------------------------------


def test_build_url_has_status_param_no_area():
    """CONUS variant: URL carries status but NO area/point param."""
    url = _build_nws_conus_url("actual")
    assert url.startswith("https://api.weather.gov/alerts/active?")
    assert "status=actual" in url
    # Critical: no area or point param (this is the CONUS-wide variant).
    assert "area=" not in url, f"CONUS URL must not carry area=: {url}"
    assert "point=" not in url, f"CONUS URL must not carry point=: {url}"


def test_build_url_different_status_values():
    """Each valid status produces a distinct URL."""
    urls = {_build_nws_conus_url(s) for s in ("actual", "exercise", "test")}
    assert len(urls) == 3


# ---------------------------------------------------------------------------
# Client-side event_types filter tests.
# ---------------------------------------------------------------------------


def test_filter_none_returns_all():
    """No filter → input passed through unchanged."""
    features = _sample_conus_geojson(10)["features"]
    assert _filter_features_by_event_types(features, None) is features
    assert _filter_features_by_event_types(features, []) is features


def test_filter_narrows_to_hurricane_only():
    """Filter ['Hurricane Warning'] narrows a 50-feature sample to ~10 (50/5 cycle)."""
    features = _sample_conus_geojson(50)["features"]
    narrowed = _filter_features_by_event_types(features, ["Hurricane Warning"])
    # Sample cycles 5 events; 50/5 = 10 Hurricane Warnings.
    assert len(narrowed) == 10
    for f in narrowed:
        assert f["properties"]["event"] == "Hurricane Warning"


def test_filter_with_multiple_event_types():
    """Filter for two event types returns the union."""
    features = _sample_conus_geojson(50)["features"]
    narrowed = _filter_features_by_event_types(
        features, ["Hurricane Warning", "Flood Warning"],
    )
    assert len(narrowed) == 20  # 10 + 10
    events = {f["properties"]["event"] for f in narrowed}
    assert events == {"Hurricane Warning", "Flood Warning"}


# ---------------------------------------------------------------------------
# User-Agent header verification.
# ---------------------------------------------------------------------------


def test_user_agent_header_sent_on_request():
    """Verify the User-Agent header is REQUIRED-AND-PRESENT on every NWS GET.

    NWS returns 403 without a descriptive User-Agent.
    """
    captured_headers = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"type": "FeatureCollection", "features": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            captured_headers.update(headers or {})
            return FakeResponse()

    with patch("grace2_agent.tools.fetch_nws_alerts_conus.httpx.Client", FakeClient):
        from grace2_agent.tools.fetch_nws_alerts_conus import _fetch_nws_conus_geojson
        _fetch_nws_conus_geojson("https://api.weather.gov/alerts/active?status=actual")

    assert "User-Agent" in captured_headers, (
        f"User-Agent header missing! Captured: {captured_headers}"
    )
    ua = captured_headers["User-Agent"]
    assert "grace2-agent" in ua, f"User-Agent should identify grace2-agent: {ua!r}"
    assert "contact" in ua.lower(), (
        f"User-Agent should include a contact per NWS policy: {ua!r}"
    )


# ---------------------------------------------------------------------------
# Mocked end-to-end: 50-alert CONUS response.
# ---------------------------------------------------------------------------


def test_50_alert_conus_response_writes_fgb_with_50_features():
    """Mocked CONUS response with 50 alerts → 50-feature FlatGeobuf written through cache."""
    fake_gcs = FakeStorageClient()
    fake_geojson = _sample_conus_geojson(50)

    with patch(
        "grace2_agent.tools.fetch_nws_alerts_conus._fetch_nws_conus_geojson",
        return_value=fake_geojson,
    ), patch(
        "grace2_agent.tools.fetch_nws_alerts_conus.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        result = fetch_nws_alerts_conus()

    assert result.uri.startswith("gs://")
    assert "nws_alerts_conus" in result.uri
    assert "dynamic-1h" in result.uri
    assert result.layer_type == "vector"
    assert result.role == "primary"
    assert result.units is None
    assert "CONUS" in result.name
    assert len(fake_gcs.store) == 1

    # Read back the FlatGeobuf and confirm 50 features.
    fgb_bytes = next(iter(fake_gcs.store.values()))
    assert len(fgb_bytes) > 0

    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 50, f"Expected 50 features, got {len(gdf)}"
        events = set(gdf["event"].tolist())
        # Should have all 5 distinct event types from the sample.
        assert "Hurricane Warning" in events
        assert "Flood Warning" in events
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_event_types_filter_narrows_in_end_to_end_call():
    """Top-level call with event_types=['Hurricane Warning'] narrows to 10."""
    fake_gcs = FakeStorageClient()
    fake_geojson = _sample_conus_geojson(50)

    with patch(
        "grace2_agent.tools.fetch_nws_alerts_conus._fetch_nws_conus_geojson",
        return_value=fake_geojson,
    ), patch(
        "grace2_agent.tools.fetch_nws_alerts_conus.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        result = fetch_nws_alerts_conus(event_types=["Hurricane Warning"])

    fgb_bytes = next(iter(fake_gcs.store.values()))
    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 10, f"Expected 10 Hurricane Warnings, got {len(gdf)}"
        assert set(gdf["event"].tolist()) == {"Hurricane Warning"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    # Name should reflect the filter.
    assert "Hurricane Warning" in result.name


# ---------------------------------------------------------------------------
# Input validation tests.
# ---------------------------------------------------------------------------


def test_invalid_status_raises_input_error():
    with pytest.raises(NWSConusInputError, match="status="):
        fetch_nws_alerts_conus(status="bogus")


def test_invalid_event_types_type_raises():
    with pytest.raises(NWSConusInputError, match="event_types must be"):
        fetch_nws_alerts_conus(event_types=[123])  # type: ignore[list-item]


def test_input_errors_are_not_retryable():
    """NWSConusInputError carries retryable=False for FR-AS-11."""
    try:
        fetch_nws_alerts_conus(status="bogus")
    except NWSConusInputError as exc:
        assert exc.retryable is False
    else:
        pytest.fail("Expected NWSConusInputError")


# ---------------------------------------------------------------------------
# Upstream-error mapping.
# ---------------------------------------------------------------------------


def test_403_raises_typed_upstream_error_with_useragent_message():
    """403 from NWS surfaces as NWSConusUpstreamError naming the User-Agent."""
    class FakeResponse:
        status_code = 403
        text = "Forbidden"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            return FakeResponse()

    with patch("grace2_agent.tools.fetch_nws_alerts_conus.httpx.Client", FakeClient):
        from grace2_agent.tools.fetch_nws_alerts_conus import _fetch_nws_conus_geojson
        with pytest.raises(NWSConusUpstreamError, match="403"):
            _fetch_nws_conus_geojson("https://api.weather.gov/alerts/active?status=actual")


def test_upstream_error_is_retryable():
    """NWSConusUpstreamError is retryable=True."""
    err = NWSConusUpstreamError("test")
    assert err.retryable is True


def test_network_failure_wraps_to_upstream_error():
    """httpx.HTTPError → NWSConusUpstreamError."""
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise httpx.ConnectError("simulated DNS failure")

    with patch("grace2_agent.tools.fetch_nws_alerts_conus.httpx.Client", FakeClient):
        from grace2_agent.tools.fetch_nws_alerts_conus import _fetch_nws_conus_geojson
        with pytest.raises(NWSConusUpstreamError, match="request failed"):
            _fetch_nws_conus_geojson("https://api.weather.gov/alerts/active?status=actual")


# ---------------------------------------------------------------------------
# Cache-layer tests.
# ---------------------------------------------------------------------------


def test_cache_miss_invokes_fetch_fn_then_hit_skips():
    """First call → cache miss → fetch_fn invoked; second call → cache hit → fetch_fn skipped."""
    fake_gcs = FakeStorageClient()
    fetch_count = {"n": 0}
    fake_bytes = _fake_fgb_bytes("CONUS")

    def patched_fetch_bytes(status, event_types):
        fetch_count["n"] += 1
        return fake_bytes

    with patch(
        "grace2_agent.tools.fetch_nws_alerts_conus._fetch_nws_alerts_conus_bytes",
        side_effect=patched_fetch_bytes,
    ), patch(
        "grace2_agent.tools.fetch_nws_alerts_conus.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        r1 = fetch_nws_alerts_conus()
        r2 = fetch_nws_alerts_conus()

    assert fetch_count["n"] == 1, (
        f"Expected 1 call (hit on second); got {fetch_count['n']}"
    )
    assert r1.uri == r2.uri, "Both calls should resolve to the same cache key"


def test_event_types_filter_changes_cache_key():
    """A different event_types filter produces a different cache key.

    (See OQ-0105-CACHE-RAW-VS-FILTERED for a possible future optimization
    that would cache the RAW CONUS sweep and re-filter on hit.)
    """
    fake_gcs = FakeStorageClient()

    def patched_fetch_bytes(status, event_types):
        return _fake_fgb_bytes(str(event_types))

    with patch(
        "grace2_agent.tools.fetch_nws_alerts_conus._fetch_nws_alerts_conus_bytes",
        side_effect=patched_fetch_bytes,
    ), patch(
        "grace2_agent.tools.fetch_nws_alerts_conus.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        r_all = fetch_nws_alerts_conus()
        r_hurricane = fetch_nws_alerts_conus(event_types=["Hurricane Warning"])

    assert r_all.uri != r_hurricane.uri


def test_event_types_order_does_not_affect_cache_key():
    """Sorting event_types before keying: ['A','B'] and ['B','A'] hit same cache."""
    fake_gcs = FakeStorageClient()
    fetch_count = {"n": 0}

    def patched_fetch_bytes(status, event_types):
        fetch_count["n"] += 1
        return _fake_fgb_bytes("X")

    with patch(
        "grace2_agent.tools.fetch_nws_alerts_conus._fetch_nws_alerts_conus_bytes",
        side_effect=patched_fetch_bytes,
    ), patch(
        "grace2_agent.tools.fetch_nws_alerts_conus.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        r1 = fetch_nws_alerts_conus(
            event_types=["Hurricane Warning", "Flood Warning"],
        )
        r2 = fetch_nws_alerts_conus(
            event_types=["Flood Warning", "Hurricane Warning"],
        )

    assert r1.uri == r2.uri
    assert fetch_count["n"] == 1


# ---------------------------------------------------------------------------
# LayerURI shape.
# ---------------------------------------------------------------------------


def test_layer_uri_shape_for_unfiltered():
    fake_gcs = FakeStorageClient()
    with patch(
        "grace2_agent.tools.fetch_nws_alerts_conus._fetch_nws_alerts_conus_bytes",
        return_value=_fake_fgb_bytes("CONUS"),
    ), patch(
        "grace2_agent.tools.fetch_nws_alerts_conus.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        result = fetch_nws_alerts_conus()

    assert result.layer_type == "vector"
    assert result.role == "primary"  # CONUS-wide variant is a primary content layer
    assert result.units is None
    assert result.uri.startswith("gs://")
    assert "NWS Active Alerts" in result.name
    assert "CONUS" in result.name
    assert "all events" in result.name


# ---------------------------------------------------------------------------
# Geographic-correctness gate (job-0086 lesson).
# ---------------------------------------------------------------------------


def test_geographic_gate_all_polygons_fall_inside_us_envelope():
    """job-0086 codified lesson: every alert polygon centroid is inside the US envelope.

    Uses the synthetic 50-feature CONUS+AK+HI+PR sample. Any feature whose
    centroid falls outside the (-180, 13, -64, 72) envelope would surface a
    sign-flip / axis-swap bug in the GeoJSON→FGB conversion (a regression
    where coordinates get swapped or signed wrong would put centroids on the
    wrong continent).
    """
    fake_geojson = _sample_conus_geojson(50)

    lon_min, lon_max = _US_ENVELOPE_LONS
    lat_min, lat_max = _US_ENVELOPE_LATS

    # First verify INPUT features already fall in the envelope (sanity).
    for feat in fake_geojson["features"]:
        cx, cy = _centroid(feat["geometry"]["coordinates"])
        assert lon_min <= cx <= lon_max, (
            f"Input feature centroid lon={cx} outside [-180, -64]; bad sample"
        )
        assert lat_min <= cy <= lat_max, (
            f"Input feature centroid lat={cy} outside [13, 72]; bad sample"
        )

    # Now run through the converter and verify the geometries survive intact.
    fgb_bytes = _geojson_to_fgb(fake_geojson)
    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 50

        # Geographic gate: every feature's centroid must fall inside the US envelope.
        # If lat/lon were swapped, centroids would land at (lat, lon) → e.g. (35, -95)
        # would become (95, -35) which is OUTSIDE the envelope.
        for idx, geom in enumerate(gdf.geometry):
            c = geom.centroid
            assert lon_min <= c.x <= lon_max, (
                f"Feature {idx} centroid x={c.x} outside US lon envelope — "
                f"possible axis-swap bug"
            )
            assert lat_min <= c.y <= lat_max, (
                f"Feature {idx} centroid y={c.y} outside US lat envelope — "
                f"possible axis-swap bug"
            )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# GeoJSON → FlatGeobuf conversion edge case.
# ---------------------------------------------------------------------------


def test_geojson_to_fgb_empty_collection_is_valid():
    """Empty NWS FeatureCollection still produces valid FGB bytes."""
    empty_geojson = {"type": "FeatureCollection", "features": []}
    fgb_bytes = _geojson_to_fgb(empty_geojson)
    assert len(fgb_bytes) > 0

    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 0
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Live integration test (GRACE2_TEST_LIVE_NWS_CONUS=1 to run).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LIVE_NWS_CONUS,
    reason="Set GRACE2_TEST_LIVE_NWS_CONUS=1 to run live NWS CONUS tests",
)
def test_live_conus_sweep_returns_valid_response():
    """LIVE: real api.weather.gov CONUS sweep returns valid FGB (≥0 features).

    Empty FeatureCollection is LEGITIMATE (rare CONUS-wide quiet period).
    We assert the FGB round-trips and — if non-empty — that every feature's
    centroid falls inside the US envelope (geographic-correctness gate).
    """
    fgb_bytes = _fetch_nws_alerts_conus_bytes(status="actual", event_types=None)
    assert len(fgb_bytes) > 0

    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) >= 0
        print(f"\n[LIVE NWS CONUS] sweep returned {len(gdf)} active alert(s)")
        if len(gdf) > 0:
            events = gdf["event"].dropna().tolist()
            from collections import Counter
            counts = Counter(events).most_common(10)
            print(f"  top events: {counts}")
            print(f"  columns: {list(gdf.columns)}")

            # Geographic-correctness gate: features WITH polygons must have
            # centroids inside the US envelope. Some NWS alerts have null
            # geometry (zone-only references), which we tolerate.
            lon_min, lon_max = _US_ENVELOPE_LONS
            lat_min, lat_max = _US_ENVELOPE_LATS
            outside = 0
            inside = 0
            for geom in gdf.geometry:
                if geom is None or geom.is_empty:
                    continue
                c = geom.centroid
                if (lon_min <= c.x <= lon_max) and (lat_min <= c.y <= lat_max):
                    inside += 1
                else:
                    outside += 1
                    print(f"  WARN: centroid outside US envelope: ({c.x}, {c.y})")
            print(f"  geographic gate: inside={inside} outside={outside}")
            # Marine zones / Pacific can produce centroids slightly outside
            # the v0.1 envelope (especially the marine zones west of HI).
            # We allow up to 5% outside before failing.
            with_geom = inside + outside
            if with_geom > 0:
                outside_pct = outside / with_geom
                assert outside_pct <= 0.05, (
                    f"More than 5% of CONUS alerts ({outside}/{with_geom}) "
                    f"have centroids outside the US envelope — possible "
                    f"axis-swap regression"
                )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.mark.skipif(
    not _LIVE_NWS_CONUS,
    reason="Set GRACE2_TEST_LIVE_NWS_CONUS=1 to run live NWS CONUS tests",
)
def test_live_conus_with_filter_narrows():
    """LIVE: client-side filter to Flood Warning narrows the CONUS sweep."""
    fgb_bytes = _fetch_nws_alerts_conus_bytes(
        status="actual",
        event_types=["Flood Warning", "Hurricane Warning"],
    )
    assert len(fgb_bytes) > 0

    import geopandas as gpd
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        if len(gdf) > 0:
            events = set(gdf["event"].dropna().tolist())
            allowed = {"Flood Warning", "Hurricane Warning"}
            assert events.issubset(allowed), (
                f"Filter should narrow events; got {events}, allowed {allowed}"
            )
            print(
                f"\n[LIVE NWS CONUS] filtered to {len(gdf)} Flood/Hurricane Warning(s)"
            )
        else:
            print("\n[LIVE NWS CONUS] filter → 0 alerts (steady state)")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
