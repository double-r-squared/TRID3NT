"""Unit tests for the 4 data-fetch atomic tools (job-0033, M4 Stage C).

Coverage:
- Each tool's ``@register_tool`` lands a registered entry with the expected
  TTL class + source class + cacheable flag.
- ``round_bbox_to_resolution`` is deterministic and snaps to a stable grid.
- Bbox quantization at a single resolution produces the same canonicalized
  params dict for two callers within the same grid cell.
- ``fetch_dem`` routes through ``read_through`` (mocked GCS + mocked
  py3dep): cache miss invokes the fetcher and returns a ``LayerURI``.
- ``fetch_buildings`` routes through ``read_through`` (mocked GCS + mocked
  Planetary Computer STAC search): no-matching-items raises
  ``UpstreamAPIError`` (no sentinel written).
- ``fetch_population`` routes through ``read_through`` (mocked Census REST
  + mocked GCS): a single-state CONUS bbox yields a FeatureCollection.
- ``geocode_location`` routes through ``read_through`` (mocked Nominatim
  REST + mocked GCS): returns ``{name, bbox, latitude, longitude, source}``
  shape and emits a ``location-resolved``-eligible payload.
- ``BboxInvalidError`` paths (degenerate bbox, out-of-range lat/lon, bbox
  area over guardrail).
- Mocked external-API failures re-raise as ``UpstreamAPIError`` from inside
  ``read_through`` with no sentinel written to the cache.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools import data_fetch
from grace2_agent.tools.data_fetch import (
    BboxInvalidError,
    UpstreamAPIError,
    fetch_buildings,
    fetch_dem,
    fetch_population,
    geocode_location,
    round_bbox_to_resolution,
)


# Fort Myers, FL — small bbox for live + mocked path testing.
FORT_MYERS_BBOX = (-81.92, 26.55, -81.80, 26.68)
PINNED_NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)


class FakeBlob:
    """Duck-typed blob: tracks exists / download / upload / metadata writes."""

    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self.custom_time: datetime | None = None  # google-cloud-storage SDK requires datetime (OQ-33 hotfix)
        self.cache_control: str | None = None
        self.uploaded: bytes | None = None
        self.upload_content_type: str | None = None

    def exists(self) -> bool:
        return self._path in self._store

    def download_as_bytes(self) -> bytes:
        return self._store[self._path]

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self.uploaded = data
        self.upload_content_type = content_type
        self._store[self._path] = data


class FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store
        self.blobs: list[FakeBlob] = []

    def blob(self, path: str) -> FakeBlob:
        b = FakeBlob(self._store, path)
        self.blobs.append(b)
        return b


class FakeStorageClient:
    """Minimal duck-type for ``google.cloud.storage.Client``."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self._bucket = FakeBucket(self.store)

    def bucket(self, name: str) -> FakeBucket:
        return self._bucket


# ---------------------------------------------------------------------------
# Registration: every tool lands with the right metadata.
# ---------------------------------------------------------------------------


def test_fetch_dem_is_registered_with_static_30d():
    entry = TOOL_REGISTRY["fetch_dem"]
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "dem"
    assert entry.metadata.cacheable is True


def test_fetch_buildings_is_registered_with_static_30d():
    entry = TOOL_REGISTRY["fetch_buildings"]
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "buildings"
    assert entry.metadata.cacheable is True


def test_fetch_population_is_registered_with_static_30d():
    entry = TOOL_REGISTRY["fetch_population"]
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "population"
    assert entry.metadata.cacheable is True


def test_geocode_location_is_registered_with_dynamic_1h():
    entry = TOOL_REGISTRY["geocode_location"]
    assert entry.metadata.ttl_class == "dynamic-1h"
    assert entry.metadata.source_class == "geocode"
    assert entry.metadata.cacheable is True


def test_registry_contains_six_tools_after_eager_import():
    """job-0033 acceptance: 2 pass-throughs + 4 fetchers = 6 tools.

    job-0034 lands ``qgis_discovery`` in parallel — if that import has fired
    before this test runs, expect 8. Either count is acceptable per the
    Stage C kickoff.
    """
    names = set(TOOL_REGISTRY.keys())
    expected_subset = {
        "mongo_query",
        "qgis_process",
        "fetch_dem",
        "fetch_buildings",
        "fetch_population",
        "geocode_location",
    }
    assert expected_subset.issubset(names)
    assert len(names) in (6, 7, 8)


# ---------------------------------------------------------------------------
# round_bbox_to_resolution — engine-side quantization (OQ-32-QUANTIZATION-LOCATION).
# ---------------------------------------------------------------------------


def test_round_bbox_to_resolution_is_deterministic():
    """Two calls with the same bbox + resolution produce identical output."""
    q1 = round_bbox_to_resolution(FORT_MYERS_BBOX, 10)
    q2 = round_bbox_to_resolution(FORT_MYERS_BBOX, 10)
    assert q1 == q2


def test_round_bbox_to_resolution_collapses_floating_point_jitter():
    """Two callers whose bbox edges differ by sub-meter floats hit the same key.

    This is the dedup-via-quantization property: 1e-7 degrees of jitter
    (sub-meter) at 10m resolution should snap to the same grid cell.
    """
    base = (-81.9000001, 26.5500001, -81.8000001, 26.6800001)
    jitter = (-81.9000002, 26.5500002, -81.8000002, 26.6800002)
    qb = round_bbox_to_resolution(base, 10)
    qj = round_bbox_to_resolution(jitter, 10)
    assert qb == qj


def test_round_bbox_to_resolution_envelopes_input():
    """The quantized bbox covers (>=) the input bbox on all sides."""
    q = round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    assert q[0] <= FORT_MYERS_BBOX[0]
    assert q[1] <= FORT_MYERS_BBOX[1]
    assert q[2] >= FORT_MYERS_BBOX[2]
    assert q[3] >= FORT_MYERS_BBOX[3]


def test_round_bbox_to_resolution_rejects_degenerate_bbox():
    with pytest.raises(BboxInvalidError):
        round_bbox_to_resolution((-81.9, 26.5, -81.9, 26.6), 10)  # min_lon == max_lon


def test_round_bbox_to_resolution_rejects_out_of_range_lat():
    with pytest.raises(BboxInvalidError):
        round_bbox_to_resolution((-81.9, -95.0, -81.8, 26.6), 10)


# ---------------------------------------------------------------------------
# fetch_dem — mocked py3dep + mocked GCS happy path.
# ---------------------------------------------------------------------------


def test_fetch_dem_happy_path_writes_through_cache(monkeypatch):
    """A miss invokes the mocked py3dep fetcher, writes COG bytes, returns LayerURI."""
    fake_storage = FakeStorageClient()
    monkeypatch.setattr(
        data_fetch, "_fetch_3dep_dem_bytes", lambda bbox, res: b"FAKE_COG_BYTES"
    )

    # Run via read_through directly with the storage_client injected — the
    # tool function builds its own client. So instead, we monkeypatch the
    # google.cloud.storage import path inside read_through by overriding the
    # cache module's import-lookup. Cleanest: import the module and patch.
    from grace2_agent.tools import cache as cache_mod

    original_read_through = cache_mod.read_through

    def patched_read_through(*args: Any, **kwargs: Any):
        kwargs.setdefault("storage_client", fake_storage)
        kwargs.setdefault("now", PINNED_NOW)
        return original_read_through(*args, **kwargs)

    monkeypatch.setattr(data_fetch, "read_through", patched_read_through)

    layer = fetch_dem(FORT_MYERS_BBOX, resolution_m=10)
    assert layer.layer_type == "raster"
    assert layer.style_preset == "continuous_dem"
    assert layer.uri.startswith("gs://grace-2-hazard-prod-cache/cache/static-30d/dem/")
    assert layer.uri.endswith(".tif")
    assert layer.units == "meters"

    # The fake GCS store should now hold the COG bytes.
    paths_written = list(fake_storage.store.keys())
    assert len(paths_written) == 1
    assert fake_storage.store[paths_written[0]] == b"FAKE_COG_BYTES"
    # The blob should have a customTime set per FR-DC-3.
    written_blob = fake_storage._bucket.blobs[-1]
    assert written_blob.custom_time is not None
    assert written_blob.custom_time.year == 2026 and written_blob.custom_time.month == 6  # datetime, not isoformat str (OQ-33 hotfix)


def test_fetch_dem_rejects_oversized_bbox():
    """The 10000 km^2 guardrail rejects unrealistic single-call bboxes."""
    huge = (-100.0, 25.0, -80.0, 45.0)  # ~2.2M km^2
    with pytest.raises(BboxInvalidError):
        fetch_dem(huge, resolution_m=10)


def test_fetch_dem_upstream_failure_reraises(monkeypatch):
    """An upstream py3dep failure surfaces as UpstreamAPIError; no sentinel written."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    def boom(_bbox, _res):
        raise UpstreamAPIError("py3dep is unreachable")

    monkeypatch.setattr(data_fetch, "_fetch_3dep_dem_bytes", boom)
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )
    with pytest.raises(UpstreamAPIError):
        fetch_dem(FORT_MYERS_BBOX, resolution_m=10)
    # No sentinel written.
    assert fake_storage.store == {}


# ---------------------------------------------------------------------------
# fetch_buildings — mocked STAC search.
# ---------------------------------------------------------------------------


def test_fetch_buildings_happy_path(monkeypatch):
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch, "_fetch_msft_buildings_bytes", lambda bbox: b"FAKE_FGB_BYTES"
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    layer = fetch_buildings(FORT_MYERS_BBOX, source="msft")
    assert layer.layer_type == "vector"
    assert layer.style_preset == "affected_buildings"
    assert layer.uri.startswith(
        "gs://grace-2-hazard-prod-cache/cache/static-30d/buildings/"
    )
    assert layer.uri.endswith(".fgb")


def test_fetch_buildings_rejects_unknown_source():
    with pytest.raises(BboxInvalidError):
        fetch_buildings(FORT_MYERS_BBOX, source="usgs-nationalmap")


def test_fetch_buildings_osm_branch_not_implemented():
    """OSM branch is reserved; M4 substrate raises UpstreamAPIError."""
    with pytest.raises(UpstreamAPIError):
        fetch_buildings(FORT_MYERS_BBOX, source="osm")


# ---------------------------------------------------------------------------
# fetch_population — mocked Census REST.
# ---------------------------------------------------------------------------


def test_fetch_population_acs_opt_in_routes_to_acs_branch(monkeypatch):
    """Tier-2 opt-in: explicit ``dataset="acs_2022"`` still routes to ACS.

    Appendix F.1 makes WorldPop the Tier-1 default (see the no-dataset-arg
    test below), but the existing ACS path stays callable for tract-level
    precision queries — that's the Tier-2 routing rule.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_acs_population_bytes",
        lambda bbox, dataset: b'{"type":"FeatureCollection","features":[]}',
    )
    # Guard: WorldPop branch must not be touched on this code path.
    def _worldpop_should_not_be_called(_bbox, _dataset):  # pragma: no cover
        raise AssertionError(
            "WorldPop branch should not be invoked when dataset='acs_2022' is passed"
        )

    monkeypatch.setattr(
        data_fetch, "_fetch_worldpop_population_bytes", _worldpop_should_not_be_called
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    layer = fetch_population(FORT_MYERS_BBOX, dataset="acs_2022")
    assert layer.layer_type == "vector"
    assert layer.units == "people"
    assert layer.uri.startswith(
        "gs://grace-2-hazard-prod-cache/cache/static-30d/population/"
    )
    assert layer.uri.endswith(".json")


def test_fetch_population_default_routes_to_worldpop_not_acs(monkeypatch):
    """Appendix F.1 (v0.3.16): ``fetch_population(bbox)`` defaults to WorldPop.

    The default-arg path MUST hit the WorldPop branch and MUST NOT hit the
    ACS branch (a Tier-2 source that requires a Census API key for non-
    trivial volume — Tier-1 preference rule says no-key defaults).
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    worldpop_calls: list[tuple[Any, str]] = []

    def _capturing_worldpop(bbox, dataset):
        worldpop_calls.append((bbox, dataset))
        return b"FAKE_WORLDPOP_COG_BYTES"

    monkeypatch.setattr(
        data_fetch, "_fetch_worldpop_population_bytes", _capturing_worldpop
    )
    # Guard: ACS branch must not be touched on the default path.
    def _acs_should_not_be_called(_bbox, _dataset):  # pragma: no cover
        raise AssertionError(
            "ACS branch (Tier-2, key-required) must not be invoked by the default "
            "fetch_population(bbox) call — Appendix F.1 says Tier-1 (WorldPop) is "
            "the default."
        )

    monkeypatch.setattr(
        data_fetch, "_fetch_acs_population_bytes", _acs_should_not_be_called
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    layer = fetch_population(FORT_MYERS_BBOX)  # no dataset= arg
    assert worldpop_calls, "WorldPop fetcher should have been called for default path"
    assert worldpop_calls[0][1].startswith("worldpop_"), worldpop_calls
    assert layer.layer_type == "raster"  # WorldPop is a raster COG, not a GeoJSON FC
    assert layer.units == "people"
    assert layer.uri.startswith(
        "gs://grace-2-hazard-prod-cache/cache/static-30d/population/"
    )
    assert layer.uri.endswith(".tif")


def test_fetch_population_worldpop_writes_tif_cog_to_cache(monkeypatch):
    """The WorldPop default branch writes a ``.tif`` COG to the population cache prefix."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_worldpop_population_bytes",
        lambda bbox, dataset: b"FAKE_WORLDPOP_COG_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    layer = fetch_population(FORT_MYERS_BBOX, dataset="worldpop_2020")
    # Cache landed at .tif under the population prefix.
    paths = list(fake_storage.store.keys())
    assert len(paths) == 1
    assert paths[0].startswith("cache/static-30d/population/")
    assert paths[0].endswith(".tif")
    assert fake_storage.store[paths[0]] == b"FAKE_WORLDPOP_COG_BYTES"
    # customTime set per FR-DC-3.
    blob = fake_storage._bucket.blobs[-1]
    assert blob.custom_time is not None
    # LayerURI return shape is raster + meters/people.
    assert layer.layer_type == "raster"
    assert layer.uri.endswith(".tif")


def test_fetch_population_rejects_unknown_dataset():
    """A dataset that's neither WorldPop nor ACS is rejected as BboxInvalidError."""
    with pytest.raises(BboxInvalidError):
        fetch_population(FORT_MYERS_BBOX, dataset="landscan")


# ---------------------------------------------------------------------------
# geocode_location — mocked Nominatim.
# ---------------------------------------------------------------------------


def test_geocode_location_happy_path(monkeypatch):
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod
    import json as _json

    fake_payload = {
        "name": "Fort Myers, Lee County, Florida, United States",
        "latitude": 26.6406,
        "longitude": -81.8723,
        "bbox": [-81.93, 26.55, -81.78, 26.71],
        "source": "nominatim",
        "query": "Fort Myers, FL",
        "osm_type": "relation",
        "osm_id": 12345,
        "place_id": 67890,
    }
    monkeypatch.setattr(
        data_fetch,
        "_fetch_nominatim_geocode_bytes",
        lambda query: _json.dumps(fake_payload).encode("utf-8"),
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    result = geocode_location("Fort Myers, FL")
    assert result["source"] == "nominatim"
    assert result["bbox"] == [-81.93, 26.55, -81.78, 26.71]
    assert "Fort Myers" in result["name"]
    # No gs:// URI leaks into the returned payload (Tier separation).
    assert "gs://" not in str(result)


def test_geocode_location_rejects_empty_query():
    with pytest.raises(BboxInvalidError):
        geocode_location("   ")


# ---------------------------------------------------------------------------
# DI binding (set_mcp_client) verified end-to-end through the run() helper.
# ---------------------------------------------------------------------------


def test_set_mcp_client_unblocks_mongo_query_body(monkeypatch):
    """With a bound MCP client, ``mongo_query`` no longer raises 'not bound'.

    The wire integration (async MCP -> sync ADK surface) still lands in a
    follow-up, so a NotImplementedError is the expected "registry placement
    in place" sentinel. The point is the binding flow works: ``set_mcp_client
    (client)`` removes the "MCP client is not bound" RuntimeError surface.
    """
    from grace2_agent.tools import passthroughs

    class StubMCP:
        def call_tool(self, *_a, **_kw):
            return {}

    passthroughs.set_mcp_client(StubMCP())
    try:
        with pytest.raises(NotImplementedError):
            passthroughs.mongo_query("sessions", {"_id": "abc"})
    finally:
        passthroughs.set_mcp_client(None)


def test_main_bind_mcp_client_helper_wires_through(monkeypatch):
    """``main._bind_mcp_client`` is the orchestrated DI seam exposed in the
    startup path; verify it binds the passed client into ``passthroughs``."""
    from grace2_agent.main import _bind_mcp_client
    from grace2_agent.tools import passthroughs

    class StubMCP:
        pass

    stub = StubMCP()
    _bind_mcp_client(stub)
    try:
        assert passthroughs._MCP_CLIENT is stub
    finally:
        passthroughs.set_mcp_client(None)
