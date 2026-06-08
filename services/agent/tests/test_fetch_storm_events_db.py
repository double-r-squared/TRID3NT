"""Unit + live tests for ``fetch_storm_events_db`` (job-0091).

Coverage (no network needed):
- Tool is registered in TOOL_REGISTRY with expected metadata.
- Invalid year raises StormEventsArgError (not retryable).
- Invalid state code raises StormEventsArgError.
- Invalid event_types raises StormEventsArgError.
- Synthetic 100-row CSV → 100 points (mocked fetch).
- state='FL' filter narrows the synthetic CSV down to FL-only rows.
- event_types=['Hurricane'] further narrows.
- Year 2022 fixture has Hurricane Ian rows (geography correctness — Ian
  begin_lat/begin_lon land inside Florida's bounding box).
- Null-coord rows are dropped without error.
- Cache miss: fetch_fn is invoked and bytes are written.
- Cache hit: second call with same params skips fetch_fn.
- _resolve_csv_url extracts highest-processed-date file from index HTML.

Live tests (network-gated by GRACE2_TEST_LIVE_STORM=1):
- Real fetch for year=2022, state='FL' returns >0 features.
"""

from __future__ import annotations

import gzip
import io
import os
import tempfile
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.fetch_storm_events_db import (
    StormEventsArgError,
    StormEventsEmptyError,
    StormEventsError,
    StormEventsUpstreamError,
    _resolve_csv_url,
    _parse_filter_and_serialize,
    fetch_storm_events_db,
)


# ---------------------------------------------------------------------------
# Constants / helpers.
# ---------------------------------------------------------------------------


_PINNED_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)
_LIVE_STORM = os.environ.get("GRACE2_TEST_LIVE_STORM") == "1"


# ---------------------------------------------------------------------------
# Fake GCS plumbing — mirrors test_fetch_administrative_boundaries.py pattern.
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

    def upload_from_string(
        self, data: bytes, content_type: str | None = None
    ) -> None:
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


# ---------------------------------------------------------------------------
# Synthetic NOAA Storm Events CSV builder.
# ---------------------------------------------------------------------------


# Minimal column set sufficient to exercise the parser/filter path. Real NOAA
# CSV has ~50 columns; we only need lat/lon + required filter columns + a
# couple of retained property columns.
_CSV_HEADER = (
    "EVENT_ID,EVENT_TYPE,STATE,BEGIN_DATE_TIME,END_DATE_TIME,"
    "BEGIN_LAT,BEGIN_LON,INJURIES_DIRECT,DAMAGE_PROPERTY,EPISODE_NARRATIVE"
)


def _make_synth_csv_rows(
    n_fl_hurricane: int = 5,
    n_fl_tornado: int = 10,
    n_tx_hail: int = 80,
    n_null_coords: int = 5,
) -> str:
    """Build a synthetic CSV body with controllable composition.

    Returns the full CSV text (header + rows) ready for gzip.
    """
    rows = [_CSV_HEADER]
    eid = 1000

    # Florida hurricanes — Hurricane Ian-shape coords (around 26.6N, -81.8W).
    for i in range(n_fl_hurricane):
        rows.append(
            f"{eid},Hurricane,FLORIDA,28-SEP-22 14:00:00,30-SEP-22 06:00:00,"
            f"{26.5 + i * 0.05:.4f},{-82.0 + i * 0.05:.4f},"
            f"0,5000000,\"Hurricane Ian made landfall near Cayo Costa\""
        )
        eid += 1

    # Florida tornadoes (different EVENT_TYPE but same STATE).
    for i in range(n_fl_tornado):
        rows.append(
            f"{eid},Tornado,FLORIDA,15-MAR-22 12:00:00,15-MAR-22 12:30:00,"
            f"{27.0 + i * 0.02:.4f},{-81.5 + i * 0.02:.4f},"
            f"0,10000,\"Brief tornado touched down\""
        )
        eid += 1

    # Texas hail (different STATE).
    for i in range(n_tx_hail):
        rows.append(
            f"{eid},Hail,TEXAS,05-MAY-22 16:00:00,05-MAY-22 16:15:00,"
            f"{31.0 + (i % 20) * 0.05:.4f},{-98.0 - (i % 20) * 0.05:.4f},"
            f"0,2500,\"Quarter-size hail reported\""
        )
        eid += 1

    # Null-coord rows (should be dropped silently).
    for _ in range(n_null_coords):
        rows.append(
            f"{eid},Flash Flood,GEORGIA,10-JUN-22 18:00:00,10-JUN-22 22:00:00,"
            ",,0,15000,\"Flash flooding closed roads\""
        )
        eid += 1

    return "\n".join(rows) + "\n"


def _csv_to_gzip_bytes(csv_text: str) -> bytes:
    """Gzip-encode CSV text the same way NCEI ships it."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(csv_text.encode("utf-8"))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Registration tests (no network).
# ---------------------------------------------------------------------------


def test_tool_is_registered_in_registry():
    """fetch_storm_events_db appears in TOOL_REGISTRY with expected metadata."""
    assert "fetch_storm_events_db" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["fetch_storm_events_db"]
    assert entry.metadata.name == "fetch_storm_events_db"
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "storm_events"
    assert entry.metadata.cacheable is True


# ---------------------------------------------------------------------------
# Typed-error tests (no network).
# ---------------------------------------------------------------------------


def test_invalid_year_raises_typed_error():
    with pytest.raises(StormEventsArgError, match="year"):
        fetch_storm_events_db(year=1800)
    with pytest.raises(StormEventsArgError, match="year"):
        fetch_storm_events_db(year=3000)


def test_invalid_state_raises_typed_error():
    with pytest.raises(StormEventsArgError, match="state"):
        fetch_storm_events_db(year=2022, state="Florida")  # not 2-letter
    with pytest.raises(StormEventsArgError, match="state"):
        fetch_storm_events_db(year=2022, state="")  # empty


def test_invalid_event_types_raises_typed_error():
    with pytest.raises(StormEventsArgError, match="event_types"):
        fetch_storm_events_db(
            year=2022, event_types="Hurricane"  # type: ignore[arg-type]
        )
    with pytest.raises(StormEventsArgError, match="event_types"):
        fetch_storm_events_db(year=2022, event_types=[""])


def test_arg_errors_are_not_retryable():
    try:
        fetch_storm_events_db(year=1800)
    except StormEventsArgError as exc:
        assert exc.retryable is False
    else:
        pytest.fail("Expected StormEventsArgError")


# ---------------------------------------------------------------------------
# URL-resolution test (mocked index page).
# ---------------------------------------------------------------------------


def test_resolve_csv_url_picks_highest_processed_date():
    """_resolve_csv_url picks the file with the highest c{YYYYMMDD} for the year."""
    fake_index = """
    <html><body>
    <td><a href="StormEvents_details-ftp_v1.0_d2022_c20230101.csv.gz">old</a></td>
    <td><a href="StormEvents_details-ftp_v1.0_d2022_c20260323.csv.gz">new</a></td>
    <td><a href="StormEvents_details-ftp_v1.0_d2021_c20260323.csv.gz">different year</a></td>
    </body></html>
    """
    mock_resp = MagicMock()
    mock_resp.text = fake_index
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp

    url = _resolve_csv_url(2022, client=mock_client)
    assert url.endswith("StormEvents_details-ftp_v1.0_d2022_c20260323.csv.gz")


def test_resolve_csv_url_no_match_raises():
    """Year missing from index raises StormEventsUpstreamError."""
    fake_index = "no storm files here"
    mock_resp = MagicMock()
    mock_resp.text = fake_index
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp

    with pytest.raises(StormEventsUpstreamError, match="no NOAA Storm Events CSV"):
        _resolve_csv_url(2022, client=mock_client)


# ---------------------------------------------------------------------------
# Parser/filter tests against synthetic CSV (no network).
# ---------------------------------------------------------------------------


def test_synthetic_100_row_csv_yields_100_points():
    """A 100-row synthetic CSV produces a 100-feature FlatGeobuf (no filters,
    no nulls)."""
    csv_text = _make_synth_csv_rows(
        n_fl_hurricane=5,
        n_fl_tornado=10,
        n_tx_hail=80,
        n_null_coords=5,  # these will be dropped → final 95
    )
    gz_bytes = _csv_to_gzip_bytes(csv_text)
    # No filter — all valid-coord rows kept (5 + 10 + 80 = 95 of 100).
    fgb_bytes = _parse_filter_and_serialize(gz_bytes, state=None, event_types=None)
    assert fgb_bytes.startswith(b"fgb")  # FlatGeobuf magic prefix
    # Re-read with geopandas to confirm feature count.
    import geopandas as gpd  # type: ignore[import-not-found]
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        f.write(fgb_bytes)
        path = f.name
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 95  # null-coord rows dropped
    finally:
        os.unlink(path)


def test_state_filter_narrows_to_fl():
    """state='FL' filter retains only Florida rows."""
    csv_text = _make_synth_csv_rows(
        n_fl_hurricane=5, n_fl_tornado=10, n_tx_hail=80, n_null_coords=0
    )
    gz_bytes = _csv_to_gzip_bytes(csv_text)
    fgb_bytes = _parse_filter_and_serialize(gz_bytes, state="FL", event_types=None)
    import geopandas as gpd  # type: ignore[import-not-found]
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        f.write(fgb_bytes)
        path = f.name
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 15  # 5 hurricane + 10 tornado, no TX
        assert (gdf["STATE"].str.upper() == "FLORIDA").all()
    finally:
        os.unlink(path)


def test_event_types_filter_narrows_to_hurricane():
    """event_types=['Hurricane'] additionally narrows the FL slice."""
    csv_text = _make_synth_csv_rows(
        n_fl_hurricane=5, n_fl_tornado=10, n_tx_hail=80, n_null_coords=0
    )
    gz_bytes = _csv_to_gzip_bytes(csv_text)
    fgb_bytes = _parse_filter_and_serialize(
        gz_bytes, state="FL", event_types=["Hurricane"]
    )
    import geopandas as gpd  # type: ignore[import-not-found]
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        f.write(fgb_bytes)
        path = f.name
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 5
        assert (gdf["EVENT_TYPE"] == "Hurricane").all()
    finally:
        os.unlink(path)


def test_null_coord_rows_dropped_without_error():
    """Rows with missing BEGIN_LAT/BEGIN_LON are silently dropped."""
    csv_text = _make_synth_csv_rows(
        n_fl_hurricane=2,
        n_fl_tornado=0,
        n_tx_hail=0,
        n_null_coords=10,  # all should be dropped
    )
    gz_bytes = _csv_to_gzip_bytes(csv_text)
    fgb_bytes = _parse_filter_and_serialize(
        gz_bytes, state=None, event_types=None
    )
    import geopandas as gpd  # type: ignore[import-not-found]
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        f.write(fgb_bytes)
        path = f.name
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert len(gdf) == 2  # null-coord Georgia rows dropped
    finally:
        os.unlink(path)


def test_empty_filter_result_raises_typed_error():
    """When no rows survive filtering, StormEventsEmptyError surfaces."""
    csv_text = _make_synth_csv_rows(
        n_fl_hurricane=0,
        n_fl_tornado=0,
        n_tx_hail=80,
        n_null_coords=0,  # only TX rows
    )
    gz_bytes = _csv_to_gzip_bytes(csv_text)
    with pytest.raises(StormEventsEmptyError):
        _parse_filter_and_serialize(
            gz_bytes, state="FL", event_types=None
        )


def test_geography_correctness_florida_hurricane_points():
    """Synthetic FL hurricane rows produce points inside FL's WGS84 bbox.

    Per the codified job-0086 lesson — verify output against known geography
    of the bbox, not just bytes round-tripping. Florida is roughly
    (-87.6, 24.4, -80.0, 31.0); Hurricane Ian's landfall area is around
    (-82.0, 26.5).
    """
    csv_text = _make_synth_csv_rows(
        n_fl_hurricane=5, n_fl_tornado=0, n_tx_hail=0, n_null_coords=0
    )
    gz_bytes = _csv_to_gzip_bytes(csv_text)
    fgb_bytes = _parse_filter_and_serialize(
        gz_bytes, state="FL", event_types=["Hurricane"]
    )
    import geopandas as gpd  # type: ignore[import-not-found]
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        f.write(fgb_bytes)
        path = f.name
    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        assert gdf.crs.to_epsg() == 4326, f"expected EPSG:4326, got {gdf.crs}"
        # Florida envelope (generous).
        fl_min_lon, fl_min_lat, fl_max_lon, fl_max_lat = -87.6, 24.4, -80.0, 31.0
        for geom in gdf.geometry:
            assert fl_min_lon <= geom.x <= fl_max_lon, (
                f"longitude {geom.x} outside Florida"
            )
            assert fl_min_lat <= geom.y <= fl_max_lat, (
                f"latitude {geom.y} outside Florida"
            )
        # Specifically, Ian-shape coordinates should be near (26.5, -82.0).
        # Centroid should be within 1 degree of Ian's landfall area.
        centroid_lon = gdf.geometry.x.mean()
        centroid_lat = gdf.geometry.y.mean()
        assert abs(centroid_lon - (-82.0)) < 1.0, (
            f"hurricane centroid lon {centroid_lon} not near Ian landfall"
        )
        assert abs(centroid_lat - 26.5) < 1.0, (
            f"hurricane centroid lat {centroid_lat} not near Ian landfall"
        )
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Cache miss/hit tests (mocked GCS + mocked fetch).
# ---------------------------------------------------------------------------


def _make_read_through_injector(fake_gcs: FakeStorageClient):
    """Return a patched read_through that injects fake GCS into the real shim."""
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


def test_cache_miss_invokes_fetch_and_writes():
    """On cache miss, _fetch_storm_events_bytes is invoked and bytes are stored."""
    fake_gcs = FakeStorageClient()
    fetch_count = {"n": 0}
    fake_fgb = b"fgb" + b"\x00" * 32 + b"_FAKE_STORM"

    def fake_fetch() -> bytes:
        fetch_count["n"] += 1
        return fake_fgb

    with patch(
        "grace2_agent.tools.fetch_storm_events_db._fetch_storm_events_bytes",
        side_effect=lambda y, s, e: fake_fetch(),
    ), patch(
        "grace2_agent.tools.fetch_storm_events_db.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        result = fetch_storm_events_db(
            year=2022, state="FL", event_types=["Hurricane"]
        )

    assert fetch_count["n"] == 1, "fetch_fn should fire once on cache miss"
    assert result.layer_type == "vector"
    assert result.role == "context"
    assert result.units is None
    assert result.uri.startswith("gs://")
    assert "storm_events" in result.uri
    assert len(fake_gcs.store) == 1, "one artifact written to fake GCS"


def test_cache_hit_skips_fetch():
    """Second call with same params hits the cache and does not invoke fetch."""
    fake_gcs = FakeStorageClient()
    fetch_count = {"n": 0}
    fake_fgb = b"fgb" + b"\x00" * 32 + b"_FAKE_STORM_CACHED"

    def fake_fetch() -> bytes:
        fetch_count["n"] += 1
        return fake_fgb

    with patch(
        "grace2_agent.tools.fetch_storm_events_db._fetch_storm_events_bytes",
        side_effect=lambda y, s, e: fake_fetch(),
    ), patch(
        "grace2_agent.tools.fetch_storm_events_db.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        r1 = fetch_storm_events_db(
            year=2022, state="FL", event_types=["Hurricane"]
        )
        assert fetch_count["n"] == 1
        # Second call with identical params — must hit cache.
        r2 = fetch_storm_events_db(
            year=2022, state="FL", event_types=["Hurricane"]
        )
        assert fetch_count["n"] == 1, "cache hit must skip fetch_fn"
        assert r1.uri == r2.uri


def test_event_types_order_does_not_split_cache():
    """event_types=['A','B'] and ['B','A'] hit the same cache key (sorted)."""
    fake_gcs = FakeStorageClient()
    fetch_count = {"n": 0}
    fake_fgb = b"fgb" + b"\x00" * 32 + b"_FAKE_STORM_SORTED"

    def fake_fetch() -> bytes:
        fetch_count["n"] += 1
        return fake_fgb

    with patch(
        "grace2_agent.tools.fetch_storm_events_db._fetch_storm_events_bytes",
        side_effect=lambda y, s, e: fake_fetch(),
    ), patch(
        "grace2_agent.tools.fetch_storm_events_db.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        r1 = fetch_storm_events_db(
            year=2022, state="FL", event_types=["Hurricane", "Tornado"]
        )
        r2 = fetch_storm_events_db(
            year=2022, state="FL", event_types=["Tornado", "Hurricane"]
        )

    assert fetch_count["n"] == 1, "reordered list must reuse cache key"
    assert r1.uri == r2.uri


# ---------------------------------------------------------------------------
# Live test — only runs with GRACE2_TEST_LIVE_STORM=1.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LIVE_STORM,
    reason="set GRACE2_TEST_LIVE_STORM=1 to run live NOAA Storm Events test",
)
def test_live_fetch_2022_florida_hurricane(tmp_path):
    """Real NOAA fetch for year=2022, state='FL', event_types=['Hurricane']
    returns a non-empty FlatGeobuf with at least 1 Hurricane Ian-shape point
    inside Florida's bounding box.
    """
    # Inject an in-memory fake GCS so we don't need real cache-bucket creds
    # for the live upstream test. The fetch path is fully real.
    fake_gcs = FakeStorageClient()
    with patch(
        "grace2_agent.tools.fetch_storm_events_db.read_through",
        side_effect=_make_read_through_injector(fake_gcs),
    ):
        result = fetch_storm_events_db(
            year=2022, state="FL", event_types=["Hurricane"]
        )

    # Persist the FGB bytes locally so the test can inspect them.
    assert len(fake_gcs.store) == 1
    fgb_bytes = next(iter(fake_gcs.store.values()))
    fgb_path = tmp_path / "live_storm.fgb"
    fgb_path.write_bytes(fgb_bytes)

    import geopandas as gpd  # type: ignore[import-not-found]
    gdf = gpd.read_file(str(fgb_path), engine="pyogrio")
    assert len(gdf) > 0, "live fetch returned 0 features"
    assert gdf.crs.to_epsg() == 4326
    # Geography correctness: all points inside Florida envelope (generous).
    fl_min_lon, fl_min_lat, fl_max_lon, fl_max_lat = -87.6, 24.0, -79.5, 31.5
    for geom in gdf.geometry:
        assert fl_min_lon <= geom.x <= fl_max_lon, (
            f"live point lon {geom.x} outside Florida"
        )
        assert fl_min_lat <= geom.y <= fl_max_lat, (
            f"live point lat {geom.y} outside Florida"
        )
    print(
        f"\nlive_test: {len(gdf)} Hurricane events in FL 2022; "
        f"first row: {gdf.iloc[0].to_dict()}"
    )
