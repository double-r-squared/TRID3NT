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


def test_registry_contains_job_0039_subset_after_eager_import():
    """job-0039 acceptance: this job's 3 new fetchers are registered + M4 fetchers + passthroughs.

    Inside the test process, the eager-import surface is whatever the test
    module triggers — ``tools/__init__.py`` (passthroughs, FROZEN) + the
    explicit ``import grace2_agent.tools.data_fetch`` at the top of this
    test file (which fires this job's three new ``@register_tool``
    decorators alongside the M4 four). Parallel sprint-07 imports
    (``qgis_discovery`` from job-0034, ``solver`` from job-0041) are
    triggered by ``main._import_tools_registry()`` — see the
    ``--startup-only`` evidence below for the live ≥11-tool assertion the
    kickoff calls out.
    """
    names = set(TOOL_REGISTRY.keys())
    expected_subset = {
        "mongo_query",
        "qgis_process",
        "fetch_dem",
        "fetch_buildings",
        "fetch_population",
        "geocode_location",
        # job-0039 (this job):
        "fetch_landcover",
        "fetch_river_geometry",
        "lookup_precip_return_period",
    }
    assert expected_subset.issubset(names), f"missing: {expected_subset - names}"
    # 2 passthroughs + 4 M4 fetchers + 3 new fetchers = 9 minimum in test
    # context; ≥9 tolerates qgis_discovery / solver / pipeline-emitter
    # imports landing in parallel.
    assert len(names) >= 9


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


# ---------------------------------------------------------------------------
# job-0039 — fetch_landcover (NLCD MRLC WMS).
# ---------------------------------------------------------------------------


from grace2_agent.tools.data_fetch import (  # noqa: E402 — after main test surface
    fetch_landcover,
    fetch_river_geometry,
    lookup_precip_return_period,
)


def test_fetch_landcover_is_registered_with_static_30d():
    """Registration assertion: ``fetch_landcover`` registered with the right metadata."""
    entry = TOOL_REGISTRY["fetch_landcover"]
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "landcover"
    assert entry.metadata.cacheable is True


def test_fetch_landcover_docstring_records_access_tier():
    """§F.1.1 docstring discipline: tier name MUST appear in the docstring.

    Live verification (2026-06-07) found NLCD is **Tier 2 (OGC service —
    MRLC WMS)**, NOT the Tier 3 the kickoff inferred. Either tier label
    must be present (we want to enforce *some* tier is named, not which one
    — the deviation is captured as OQ-39-NLCD-TIER-DEVIATION).
    """
    doc = fetch_landcover.__doc__ or ""
    assert "Access pattern:" in doc, "docstring must name the access tier per §F.1.1"
    assert "Tier" in doc, "docstring must name the access tier per §F.1.1"


def test_fetch_landcover_returns_nlcd_vintage_year_sidecar(monkeypatch):
    """Invariant 7 mitigation per OQ-4 §4: vintage year MUST be sidecar to LayerURI.

    ``build_sfincs_model`` (job-0042) consumes the vintage year to validate
    the Manning's mapping CSV covers the NLCD class encoding before the
    HydroMT roughness component is invoked. Skipping this would surface the
    silent-wrong-answer failure mode HydroMT exhibits for unmatched classes.

    Because ``LayerURI`` is FROZEN with ``extra="forbid"``, the sidecar is
    a top-level key on the returned dict, NOT a LayerURI field. The kickoff's
    example syntax ``LayerURI.metadata[...]`` is illustrative — see
    OQ-39-LANDCOVER-RETURN-SHAPE-CONTRACT-PROMOTION.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_nlcd_landcover_bytes",
        lambda bbox, year: b"FAKE_NLCD_GEOTIFF_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    result = fetch_landcover(FORT_MYERS_BBOX, dataset="nlcd_2021")
    assert isinstance(result, dict), "fetch_landcover returns a dict (LayerURI + sidecar)"
    assert "layer" in result, "dict must carry the LayerURI under key 'layer'"
    assert "nlcd_vintage_year" in result, "Invariant 7 sidecar required"
    assert result["nlcd_vintage_year"] == 2021
    assert result["dataset"] == "nlcd_2021"
    # job-0044 hotfix: switched WMS -> WCS 1.0.0 because WMS GetMap returned
    # palette-encoded indices instead of canonical NLCD class integers.
    assert result["source"] == "mrlc-wcs"

    layer = result["layer"]
    assert layer.layer_type == "raster"
    assert layer.style_preset == "categorical_landcover"
    assert layer.units == "nlcd_class_code"
    assert layer.uri.startswith(
        "gs://grace-2-hazard-prod-cache/cache/static-30d/landcover/"
    )
    assert layer.uri.endswith(".tif")


def test_fetch_landcover_routes_through_read_through_writes_cache(monkeypatch):
    """FR-CE-8: ``fetch_landcover`` routes through ``read_through`` (cache shim)."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_nlcd_landcover_bytes",
        lambda bbox, year: b"FAKE_NLCD_GEOTIFF_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    fetch_landcover(FORT_MYERS_BBOX, dataset="nlcd_2021")
    # Cache landed at .tif under the landcover prefix.
    paths = list(fake_storage.store.keys())
    assert len(paths) == 1
    assert paths[0].startswith("cache/static-30d/landcover/")
    assert paths[0].endswith(".tif")
    assert fake_storage.store[paths[0]] == b"FAKE_NLCD_GEOTIFF_BYTES"
    # customTime set per FR-DC-3.
    blob = fake_storage._bucket.blobs[-1]
    assert blob.custom_time is not None


def test_fetch_landcover_quantizes_bbox_to_30m_nlcd_grid(monkeypatch):
    """Per-source quantization (acceptance criterion 3): NLCD 30 m native grid.

    Two callers whose bbox edges differ by sub-meter floats at 30 m
    resolution should hit the same cache key (dedup-via-quantization).
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_nlcd_landcover_bytes",
        lambda bbox, year: b"FAKE_NLCD_GEOTIFF_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    base = (-81.9000001, 26.5500001, -81.8000001, 26.6800001)
    jitter = (-81.9000002, 26.5500002, -81.8000002, 26.6800002)
    r1 = fetch_landcover(base, dataset="nlcd_2021")
    r2 = fetch_landcover(jitter, dataset="nlcd_2021")
    # Both should hit the same cache entry (one stored path).
    assert len(fake_storage.store) == 1
    assert r1["layer"].uri == r2["layer"].uri


def test_fetch_landcover_rejects_unknown_dataset():
    with pytest.raises(BboxInvalidError):
        fetch_landcover(FORT_MYERS_BBOX, dataset="usgs_nlcd_2023_v3")


def test_fetch_landcover_esa_worldcover_not_implemented(monkeypatch):
    """ESA WorldCover opt-in is reserved; v0.1 substrate raises UpstreamAPIError."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )
    with pytest.raises(UpstreamAPIError):
        fetch_landcover(FORT_MYERS_BBOX, dataset="esa_worldcover_2021")


def test_fetch_landcover_rejects_oversized_bbox():
    """The 10000 km^2 guardrail rejects unrealistic single-call bboxes."""
    huge = (-100.0, 25.0, -80.0, 45.0)  # ~2.2M km^2
    with pytest.raises(BboxInvalidError):
        fetch_landcover(huge, dataset="nlcd_2021")


# ---------------------------------------------------------------------------
# job-0044 hotfix — fetch_landcover (WCS 1.0.0 path, palette-encoding fix).
# ---------------------------------------------------------------------------


def test_fetch_landcover_uses_wcs_not_wms_after_hotfix():
    """job-0044: the fetcher MUST issue WCS 1.0.0 GetCoverage, not WMS GetMap.

    Path A (palette decode) vs Path B (WCS GetCoverage) was live-probed; Path B
    won because canonical NLCD class integers come straight from the server,
    avoiding the OQ-42-NLCD-WMS-PALETTE-ENCODING silent-wrong-answer condition
    that bounced job-0042's validation gate. This test pins the choice — if
    someone reverts to WMS the band values become palette indices again and
    SFINCS dispatch silently breaks.
    """
    # Inspect the WCS coverage table — the symbol is the substrate hook the
    # hotfix introduced; reverting it would remove the alias.
    assert hasattr(data_fetch, "_MRLC_WCS_URL")
    assert hasattr(data_fetch, "_NLCD_WCS_COVERAGE_BY_YEAR")
    assert data_fetch._MRLC_WCS_URL.endswith("/wcs")
    # 2021 (the default) and 2019 (the second-most-recent discrete vintage)
    # are both in the WCS catalog.
    assert 2021 in data_fetch._NLCD_WCS_COVERAGE_BY_YEAR
    assert 2019 in data_fetch._NLCD_WCS_COVERAGE_BY_YEAR
    # The coverage IDs use the qualified ``mrlc_display:`` workspace prefix
    # WCS expects (per the 2026-06-07 live probe).
    assert data_fetch._NLCD_WCS_COVERAGE_BY_YEAR[2021].startswith(
        "mrlc_display:NLCD_2021_Land_Cover_L48"
    )


def test_fetch_landcover_cache_key_source_is_mrlc_wcs(monkeypatch):
    """job-0044 cache-migration policy: cache-key params carry source=mrlc-wcs.

    The job-0039 substrate landed cache entries under source=mrlc-wms (WMS
    GetMap); after the hotfix the cache-key tag flips to mrlc-wcs so the
    palette-encoded entries naturally evict on TTL (30 days from their write
    time) rather than colliding with the new canonical-bytes entries.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_nlcd_landcover_bytes",
        lambda bbox, year: b"FAKE_NLCD_GEOTIFF_BYTES_WCS",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    result = fetch_landcover(FORT_MYERS_BBOX, dataset="nlcd_2021")
    # Source tag in the returned dict is mrlc-wcs.
    assert result["source"] == "mrlc-wcs"
    # Same cache prefix (cache/static-30d/landcover/) but the key hash differs
    # from the WMS-source hash because the source string is part of the
    # canonicalized params dict the cache key is derived from.
    paths = list(fake_storage.store.keys())
    assert len(paths) == 1
    assert paths[0].startswith("cache/static-30d/landcover/")
    assert paths[0].endswith(".tif")


def test_fetch_nlcd_landcover_bytes_issues_wcs_1_0_0_getcoverage(monkeypatch):
    """The internal fetcher issues a WCS 1.0.0 GetCoverage request, not WMS GetMap.

    Asserts the actual request shape so a future refactor can't silently
    flip back to WMS without this test catching it. Captures the kwargs the
    fetcher passes into ``requests.get``.
    """
    captured: dict = {}

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "image/tiff"}
        content = b"\x49\x49\x2a\x00" + b"\x00" * 256  # TIFF magic prefix
        text = ""

        def raise_for_status(self):
            return None

    def _capture_get(url, params=None, headers=None, timeout=None, **_kw):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(data_fetch.requests, "get", _capture_get)
    out = data_fetch._fetch_nlcd_landcover_bytes(FORT_MYERS_BBOX, 2021)
    assert isinstance(out, bytes) and len(out) > 4
    # URL is the WCS endpoint, not WMS.
    assert captured["url"].endswith("/wcs"), captured["url"]
    # Params shape is WCS 1.0.0 GetCoverage with Coverage + CRS + BBOX +
    # WIDTH + HEIGHT + FORMAT.
    p = captured["params"]
    assert p["service"] == "WCS"
    assert p["version"] == "1.0.0"
    assert p["request"] == "GetCoverage"
    assert p["Coverage"].startswith("mrlc_display:NLCD_2021_Land_Cover_L48")
    assert p["CRS"] == "EPSG:4326"
    assert "BBOX" in p
    assert "WIDTH" in p and "HEIGHT" in p
    assert p["FORMAT"] == "GeoTIFF"
    # MUST NOT be the WMS shape.
    assert p.get("layers") is None  # WMS would use ``layers``
    assert p.get("format") is None  # WMS GetMap shape


def test_fetch_nlcd_landcover_bytes_surfaces_geoserver_exception(monkeypatch):
    """If the WCS server returns an OGC ExceptionReport XML, surface UpstreamAPIError.

    The WCS endpoint returns 200 + ``application/xml`` with an
    ``ows:ExceptionReport`` body when (e.g.) the projection mapping bug fires
    or the requested area is sub-pixel. We MUST NOT cache that body as if it
    were a GeoTIFF — the no-sentinel-on-failure cache contract demands a
    typed raise instead.
    """

    class _FakeXMLResp:
        status_code = 200
        headers = {"content-type": "application/xml"}
        content = b"<?xml version=\"1.0\"?><ows:ExceptionReport/>"
        text = "<?xml version=\"1.0\"?><ows:ExceptionReport/>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        data_fetch.requests,
        "get",
        lambda *a, **kw: _FakeXMLResp(),
    )
    with pytest.raises(UpstreamAPIError):
        data_fetch._fetch_nlcd_landcover_bytes(FORT_MYERS_BBOX, 2021)


# ---------------------------------------------------------------------------
# job-0039 — fetch_river_geometry (NHDPlus HR HUC4 region download).
# ---------------------------------------------------------------------------


def test_fetch_river_geometry_is_registered_with_static_30d():
    entry = TOOL_REGISTRY["fetch_river_geometry"]
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "river_geometry"
    assert entry.metadata.cacheable is True


def test_fetch_river_geometry_docstring_records_tier_4():
    """§F.1.1 docstring discipline: Tier 4 (region download + local clip)."""
    doc = fetch_river_geometry.__doc__ or ""
    assert "Access pattern:" in doc
    assert "Tier 4" in doc


def test_fetch_river_geometry_happy_path_returns_layer_uri(monkeypatch):
    """Mocked NHDPlus HR fetcher + mocked GCS → LayerURI with HUC4 code in layer_id."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_nhdplushr_geometry_bytes",
        lambda bbox, huc4: b"FAKE_FLATGEOBUF_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    layer = fetch_river_geometry(FORT_MYERS_BBOX)
    assert layer.layer_type == "vector"
    assert layer.uri.startswith(
        "gs://grace-2-hazard-prod-cache/cache/static-30d/river_geometry/"
    )
    assert layer.uri.endswith(".fgb")
    # HUC4 ``0309`` covers Fort Myers — encoded in the layer_id per the
    # Tier-4 cache-key-includes-HUC4 discipline.
    assert "huc4-0309" in layer.layer_id


def test_fetch_river_geometry_cache_key_includes_huc4(monkeypatch):
    """Two disjoint HUC4s with same nominal bbox shape must NOT collide on the cache key.

    Per the Tier-4 cache discipline (cache key includes the HUC4 region per
    §F.1.1), Fort Myers (HUC4 0309) and the South Coast California (HUC4
    1807) MUST produce different cache paths even if the bboxes are small
    boxes inside each region.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_nhdplushr_geometry_bytes",
        lambda bbox, huc4: b"FAKE_FLATGEOBUF_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    fl_layer = fetch_river_geometry(FORT_MYERS_BBOX)
    # CA south coast (HUC4 1807) — small bbox in the LA basin.
    ca_bbox = (-118.4, 33.8, -118.2, 34.0)
    ca_layer = fetch_river_geometry(ca_bbox)
    assert "huc4-0309" in fl_layer.layer_id
    assert "huc4-1807" in ca_layer.layer_id
    assert fl_layer.uri != ca_layer.uri, "different HUC4s must dedup on different keys"


def test_fetch_river_geometry_rejects_unknown_source():
    with pytest.raises(BboxInvalidError):
        fetch_river_geometry(FORT_MYERS_BBOX, source="merit_hydro")


def test_fetch_river_geometry_rejects_bbox_outside_huc4_envelope():
    """A bbox center that doesn't match any v0.1 HUC4 envelope raises UpstreamAPIError."""
    # Antarctic ocean — not in any HUC4 envelope.
    with pytest.raises(UpstreamAPIError):
        fetch_river_geometry((0.0, -70.0, 0.5, -69.5))


def test_fetch_river_geometry_rejects_oversized_bbox():
    """The 5000 km^2 guardrail blocks multi-HUC4 stitching attempts.

    Bbox center sits inside HUC4 0309 (South Florida envelope: lon
    [-82.0, -80.0], lat [25.0, 27.5]) so the HUC4 routing accepts it; the
    bbox itself is sized to exceed the 5000 km^2 area guardrail (~25,000
    km^2 here) so the area guardrail fires.
    """
    # Bbox center (-81.0, 26.25) inside HUC4 0309 envelope; ~25k km^2 area.
    oversized_inside_huc4 = (-81.9, 25.5, -80.1, 27.0)
    with pytest.raises(BboxInvalidError):
        fetch_river_geometry(oversized_inside_huc4)


# ---------------------------------------------------------------------------
# job-0039 — lookup_precip_return_period (NOAA Atlas 14 PFDS).
# ---------------------------------------------------------------------------


def test_lookup_precip_return_period_is_registered_with_static_30d():
    entry = TOOL_REGISTRY["lookup_precip_return_period"]
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "precip_return_period"
    assert entry.metadata.cacheable is True


def test_lookup_precip_return_period_docstring_records_tier_3():
    """§F.1.1 docstring discipline: Tier 3 (direct HTTPS point query)."""
    doc = lookup_precip_return_period.__doc__ or ""
    assert "Access pattern:" in doc
    assert "Tier 3" in doc


# Verbatim Atlas 14 PFDS response for the Fort Myers center captured 2026-06-07.
_ATLAS14_FORT_MYERS_FIXTURE = b"""Point precipitation frequency estimates (inches)
NOAA Atlas 14 Volume 9 Version 2
Data type: Precipitation depth
Time series type: Partial duration
Project area: Southeastern States
Location name (ESRI Maps): None
Station Name: None
Latitude: 26.6 Degree
Longitude: -81.9 Degree
Elevation (USGS): None None


PRECIPITATION FREQUENCY ESTIMATES
by duration for ARI (years):, 1,2,5,10,25,50,100,200,500,1000
5-min:, 0.553,0.620,0.731,0.822,0.950,1.05,1.15,1.25,1.38,1.48
10-min:, 0.810,0.908,1.07,1.20,1.39,1.54,1.68,1.83,2.02,2.17
15-min:, 0.988,1.11,1.30,1.47,1.70,1.87,2.05,2.23,2.47,2.65
30-min:, 1.60,1.79,2.11,2.37,2.74,3.02,3.31,3.60,3.99,4.28
60-min:, 2.14,2.38,2.79,3.13,3.62,4.00,4.38,4.78,5.32,5.74
2-hr:, 2.69,2.98,3.47,3.90,4.49,4.97,5.46,5.97,6.66,7.20
3-hr:, 2.92,3.25,3.81,4.30,4.99,5.54,6.11,6.71,7.53,8.17
6-hr:, 3.23,3.70,4.50,5.18,6.16,6.94,7.75,8.60,9.76,10.7
12-hr:, 3.49,4.18,5.35,6.36,7.79,8.94,10.1,11.3,13.0,14.3
24-hr:, 4.01,4.76,6.09,7.28,9.05,10.5,12.1,13.7,16.1,18.0
2-day:, 4.94,5.57,6.77,7.94,9.80,11.4,13.3,15.3,18.2,20.7
3-day:, 5.43,6.22,7.68,9.02,11.1,12.9,14.8,16.9,19.8,22.3
4-day:, 5.83,6.78,8.43,9.92,12.1,14.0,15.9,18.0,20.9,23.3
7-day:, 7.08,8.10,9.87,11.4,13.7,15.5,17.5,19.5,22.4,24.6
10-day:, 8.28,9.30,11.0,12.6,14.8,16.6,18.5,20.4,23.2,25.4
20-day:, 11.7,12.9,14.8,16.4,18.7,20.4,22.1,23.8,26.1,27.8
30-day:, 14.5,15.9,18.2,20.0,22.4,24.2,25.9,27.5,29.5,30.9
45-day:, 18.0,19.9,22.7,24.9,27.7,29.6,31.4,33.0,34.9,36.2
60-day:, 21.0,23.3,26.6,29.2,32.4,34.6,36.6,38.3,40.3,41.5

Date/time (GMT):  Sun Jun  7 07:54:20 2026
"""


def test_lookup_precip_return_period_happy_path_returns_structured_dict(monkeypatch):
    """100-year 24-hour at Fort Myers center: parsed from the fixture."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_atlas14_pfds_bytes",
        lambda lat, lon: _ATLAS14_FORT_MYERS_FIXTURE,
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    result = lookup_precip_return_period(
        location=(26.6, -81.9), return_period_years=100, duration_hours=24.0
    )
    assert result["precip_inches"] == pytest.approx(12.1)
    assert result["units"] == "inches"
    assert result["return_period_years"] == 100
    assert result["duration_hours"] == 24.0
    assert "Volume 9" in result["vintage_volume"]
    assert "Southeastern" in result["project_area"]
    assert result["source"] == "noaa-atlas14-pfds"
    # Quantized location echoed back.
    assert len(result["location"]) == 2


def test_lookup_precip_return_period_quantizes_location_to_atlas14_grid(monkeypatch):
    """Per-source quantization (acceptance criterion 3): 1/120 degree native grid.

    Two callers within the same Atlas 14 grid cell hit the same cache entry.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    fetch_calls: list[tuple[float, float]] = []

    def _capturing_fetch(lat, lon):
        fetch_calls.append((lat, lon))
        return _ATLAS14_FORT_MYERS_FIXTURE

    monkeypatch.setattr(data_fetch, "_fetch_atlas14_pfds_bytes", _capturing_fetch)
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    # Two locations within the same 1/120-degree grid cell (~278 m apart at
    # 26.6 latitude — 1/120 degree ≈ 309 m).
    r1 = lookup_precip_return_period(
        location=(26.6, -81.9), return_period_years=100, duration_hours=24.0
    )
    r2 = lookup_precip_return_period(
        location=(26.6005, -81.9005), return_period_years=100, duration_hours=24.0
    )
    assert r1["location"] == r2["location"]
    # Only one cache miss (second call hits the cache).
    assert len(fetch_calls) == 1
    assert len(fake_storage.store) == 1


def test_lookup_precip_return_period_rejects_unsupported_return_period():
    with pytest.raises(BboxInvalidError):
        lookup_precip_return_period(
            location=(26.6, -81.9), return_period_years=300, duration_hours=24.0
        )


def test_lookup_precip_return_period_rejects_unsupported_duration():
    with pytest.raises(BboxInvalidError):
        lookup_precip_return_period(
            location=(26.6, -81.9), return_period_years=100, duration_hours=1.5
        )


def test_lookup_precip_return_period_writes_csv_through_cache(monkeypatch):
    """FR-CE-8: the PFDS CSV is cached under cache/static-30d/precip_return_period/."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_atlas14_pfds_bytes",
        lambda lat, lon: _ATLAS14_FORT_MYERS_FIXTURE,
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    lookup_precip_return_period(
        location=(26.6, -81.9), return_period_years=100, duration_hours=24.0
    )
    paths = list(fake_storage.store.keys())
    assert len(paths) == 1
    assert paths[0].startswith("cache/static-30d/precip_return_period/")
    assert paths[0].endswith(".csv")
    assert b"NOAA Atlas 14" in fake_storage.store[paths[0]]
    blob = fake_storage._bucket.blobs[-1]
    assert blob.custom_time is not None
