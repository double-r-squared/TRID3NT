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

import os
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


# ---------------------------------------------------------------------------
# job-0324 follow-up — STALE-CACHE fix: landcover cache key MUST change so a
# post-fix fetch MISSES the pre-fix (palette-less) COG and regenerates a
# colored, palette-preserving one. The bake-NLCD-into-hillshade demo rendered
# grey because the static-30d cache served a palette-LESS COG written before
# deploy #3's palette-preservation fix; bumping a landcover-only cache-version
# salt evicts those entries on the next fetch.
# ---------------------------------------------------------------------------


def test_landcover_cache_version_salt_present_and_folded_into_params():
    """The landcover-only cache-version salt exists and is part of the params.

    The salt is what makes the post-fix key differ from the stale pre-fix key.
    It must live ONLY in the landcover params dict (not in the shared
    ``compute_cache_key`` salt) so no other tool's cache key changes.
    """
    assert hasattr(data_fetch, "_LANDCOVER_CACHE_VERSION")
    # v2 = post-job-0324 palette-preserving COGs (v1 was the stale palette-less
    # generation). Any bump > 1 forces a clean regenerate.
    assert data_fetch._LANDCOVER_CACHE_VERSION >= 2


def test_landcover_cache_key_changed_after_palette_fix():
    """A fetch with the SAME bbox now computes a DIFFERENT cache key than the
    pre-fix entry — i.e. it would MISS the stale palette-less COG.

    Reconstructs the OLD params dict (no cache_version salt) and the NEW params
    dict (with the salt) exactly as ``fetch_landcover`` builds them, hashes both
    via ``compute_cache_key`` (the same function the cache shim uses), and
    asserts the keys differ. This is the load-bearing assertion that post-fix
    fetches no longer hit the grey, palette-less cached COG.
    """
    from grace2_agent.tools.cache import compute_cache_key

    quantized = data_fetch._round_bbox_to_30m_nlcd(FORT_MYERS_BBOX)

    # OLD (pre-fix) params — what the tool wrote before the salt was added.
    old_params = {
        "bbox": list(quantized),
        "dataset": "nlcd_2021",
        "source": "mrlc-wcs",
    }
    # NEW (post-fix) params — exactly what fetch_landcover now builds.
    new_params = {
        "bbox": list(quantized),
        "dataset": "nlcd_2021",
        "source": "mrlc-wcs",
        "cache_version": data_fetch._LANDCOVER_CACHE_VERSION,
    }

    source_id = data_fetch._FETCH_LANDCOVER_METADATA.source_class
    ttl_class = data_fetch._FETCH_LANDCOVER_METADATA.ttl_class
    old_key = compute_cache_key(source_id, old_params, ttl_class, now=PINNED_NOW)
    new_key = compute_cache_key(source_id, new_params, ttl_class, now=PINNED_NOW)

    assert old_key != new_key, (
        "landcover cache key must change after the palette-fix salt bump so "
        "post-fix fetches miss the stale palette-less COG"
    )


def test_fetch_landcover_writes_cache_at_new_salted_key(monkeypatch):
    """End-to-end: ``fetch_landcover`` writes the COG at the NEW salted key.

    Drives the real tool through the (mocked) cache shim and confirms the cache
    object it lands at matches the salted-params key — NOT the old un-salted key
    that the stale palette-less COG occupies.
    """
    from grace2_agent.tools.cache import (
        cache_path,
        compute_cache_key,
    )
    from grace2_agent.tools import cache as cache_mod

    fake_storage = FakeStorageClient()
    monkeypatch.setattr(
        data_fetch,
        "_fetch_nlcd_landcover_bytes",
        lambda bbox, year: b"FAKE_NLCD_GEOTIFF_BYTES_PALETTE_PRESERVED",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    fetch_landcover(FORT_MYERS_BBOX, dataset="nlcd_2021")

    quantized = data_fetch._round_bbox_to_30m_nlcd(FORT_MYERS_BBOX)
    new_params = {
        "bbox": list(quantized),
        "dataset": "nlcd_2021",
        "source": "mrlc-wcs",
        "cache_version": data_fetch._LANDCOVER_CACHE_VERSION,
    }
    expected_key = compute_cache_key(
        "landcover", new_params, "static-30d", now=PINNED_NOW
    )
    expected_path = cache_path("landcover", "static-30d", expected_key, "tif")

    assert expected_path in fake_storage.store, (
        "COG must land at the NEW salted key, not the stale un-salted key"
    )
    # And NOT at the old un-salted key (which holds the grey palette-less COG).
    old_params = {
        "bbox": list(quantized),
        "dataset": "nlcd_2021",
        "source": "mrlc-wcs",
    }
    old_key = compute_cache_key(
        "landcover", old_params, "static-30d", now=PINNED_NOW
    )
    old_path = cache_path("landcover", "static-30d", old_key, "tif")
    assert old_path not in fake_storage.store


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
# F33/F39 fix — fetch_landcover COG with overviews + exact-bbox clip.
# ---------------------------------------------------------------------------


def _make_flat_nlcd_geotiff_bytes(bbox, width=900, height=900, pad=False):
    """Build a flat (no-overview) single-band uint8 GeoTIFF for ``bbox``.

    Mimics the MRLC WCS GetCoverage output: strip-organized, NO overviews. If
    ``pad`` is True the raster covers a bbox slightly LARGER than ``bbox`` so
    the clip step has a fringe to trim (proves the exact-bbox clip works).
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    src_bbox = bbox
    if pad:
        min_lon, min_lat, max_lon, max_lat = bbox
        dx = (max_lon - min_lon) * 0.1
        dy = (max_lat - min_lat) * 0.1
        src_bbox = (min_lon - dx, min_lat - dy, max_lon + dx, max_lat + dy)

    data = (np.random.randint(11, 95, size=(height, width))).astype("uint8")
    transform = from_bounds(*src_bbox, width, height)
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        path = f.name
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=transform,
        nodata=255,
    ) as dst:
        dst.write(data, 1)
    with open(path, "rb") as f:
        out = f.read()
    os.unlink(path)
    return out


def test_landcover_bytes_to_cog_adds_overviews_and_clips_bbox():
    """The new COG helper emits overviews AND clips to the EXACT requested bbox."""
    import rasterio

    quantized = data_fetch.round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    # Flat raster that OVERHANGS the bbox so the clip has something to trim.
    flat = _make_flat_nlcd_geotiff_bytes(quantized, pad=True)

    # Sanity: the flat input has NO overviews.
    assert not data_fetch._has_overviews(flat)

    cog = data_fetch._landcover_bytes_to_cog(flat, quantized)
    assert isinstance(cog, bytes) and len(cog) > 0

    # (1) Overviews present (the TiTiler zoomed-out-tile fix).
    assert data_fetch._has_overviews(cog), "COG output must carry internal overviews"

    # (2) Output extent clipped to the requested bbox (~1 px tolerance at 30 m).
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        f.write(cog)
        cog_path = f.name
    try:
        with rasterio.open(cog_path) as src:
            b = src.bounds
            # 30 m ~ 0.0003 deg; allow 2 px slack for pixel snapping.
            tol = 0.0006
            assert abs(b.left - quantized[0]) < tol, (b.left, quantized[0])
            assert abs(b.bottom - quantized[1]) < tol, (b.bottom, quantized[1])
            assert abs(b.right - quantized[2]) < tol, (b.right, quantized[2])
            assert abs(b.top - quantized[3]) < tol, (b.top, quantized[3])
            # Tiled (COG driver default 512x512), not strip-organized.
            assert src.profile.get("blockxsize") is not None
    finally:
        os.unlink(cog_path)


def test_fetch_nlcd_landcover_bytes_output_has_overviews(monkeypatch):
    """End-to-end internal fetcher: NLCD bytes come back as a COG with overviews.

    Mocks the OGC adapter so the WCS GeoTIFF is a flat (no-overview) raster,
    then asserts the fetcher's returned bytes (what gets cached) carry
    overviews — the F33/F39 spotty-render root-cause fix.
    """
    quantized = data_fetch.round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    flat = _make_flat_nlcd_geotiff_bytes(quantized)
    assert not data_fetch._has_overviews(flat)

    class _FakeOGCResp:
        content = flat
        content_type = "image/tiff"

    import grace2_agent.tools.ogc_adapter as ogc_mod

    monkeypatch.setattr(
        ogc_mod, "fetch_ogc_layer", lambda *a, **kw: _FakeOGCResp()
    )

    out = data_fetch._fetch_nlcd_landcover_bytes(FORT_MYERS_BBOX, 2021)
    assert isinstance(out, bytes) and len(out) > 0
    assert data_fetch._has_overviews(out), (
        "cached NLCD bytes must be a COG with overviews (TiTiler zoom fix)"
    )


# ---------------------------------------------------------------------------
# job-0324 — colormap preservation across the COG re-write paths.
#
# REGRESSION: NLCD land cover is a single-band palette-index COG with an
# EMBEDDED GDAL color table; TiTiler colorizes from it. job-0316's
# overviews/clip re-writes dropped the table → land cover renders solid GREY.
# Every re-write path (_clip_raster_bytes_to_bbox, _rasterio_translate_to_cog,
# and the full _landcover_bytes_to_cog pipeline) must carry the table forward.
# Continuous rasters (no color table) must pass through UNCHANGED — never
# fabricate a colormap.
# ---------------------------------------------------------------------------


# A representative NLCD-style palette: a handful of class indices → RGBA.
_NLCD_COLORMAP = {
    0: (0, 0, 0, 0),
    11: (72, 109, 162, 255),  # open water
    21: (222, 197, 197, 255),  # developed, open space
    41: (56, 129, 78, 255),  # deciduous forest
    81: (220, 217, 57, 255),  # pasture/hay
    90: (186, 217, 235, 255),  # woody wetlands
    255: (0, 0, 0, 0),  # nodata
}


def _make_paletted_nlcd_geotiff_bytes(bbox, width=900, height=900, pad=False):
    """Build a flat single-band uint8 GeoTIFF WITH an embedded color table.

    Mirrors the real MRLC WCS NLCD product: strip-organized, NO overviews,
    palette-index band carrying an embedded GDAL color table. ``pad`` overhangs
    the bbox so the clip step has a fringe to trim.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    src_bbox = bbox
    if pad:
        min_lon, min_lat, max_lon, max_lat = bbox
        dx = (max_lon - min_lon) * 0.1
        dy = (max_lat - min_lat) * 0.1
        src_bbox = (min_lon - dx, min_lat - dy, max_lon + dx, max_lat + dy)

    # Only use class indices that exist in the colormap.
    classes = np.array([11, 21, 41, 81, 90], dtype="uint8")
    data = classes[np.random.randint(0, len(classes), size=(height, width))]
    transform = from_bounds(*src_bbox, width, height)
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        path = f.name
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=transform,
        nodata=255,
    ) as dst:
        dst.write(data, 1)
        dst.write_colormap(1, _NLCD_COLORMAP)
    with open(path, "rb") as f:
        out = f.read()
    os.unlink(path)
    return out


def _colormap_of_bytes(tif_bytes):
    """Return the band-1 colormap of a GeoTIFF (bytes) or ``None`` if absent."""
    import tempfile

    import rasterio

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        f.write(tif_bytes)
        path = f.name
    try:
        with rasterio.open(path) as src:
            try:
                return src.colormap(1)
            except ValueError:
                return None
    finally:
        os.unlink(path)


def _colorinterp0_of_bytes(tif_bytes):
    """Return band-1 ColorInterp name of a GeoTIFF (bytes)."""
    import tempfile

    import rasterio

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        f.write(tif_bytes)
        path = f.name
    try:
        with rasterio.open(path) as src:
            return src.colorinterp[0].name
    finally:
        os.unlink(path)


def _assert_colormap_round_trip_equal(src_bytes, out_bytes):
    """Output band-1 color table must equal the SOURCE's round-tripped table.

    Comparing against the source's own ``colormap(1)`` (not the pre-write dict)
    is the apples-to-apples check: GDAL's GTiff palette writer normalizes the
    alpha component on write (opaque entries come back with a=255), so the
    contract is "the table survives the re-write intact", not "matches my
    hand-written RGBA". A per-index mismatch here means the re-write CHANGED the
    table (the grey-land-cover regression).
    """
    src_cmap = _colormap_of_bytes(src_bytes)
    assert src_cmap is not None, "test fixture lost its colormap"
    out_cmap = _colormap_of_bytes(out_bytes)
    assert out_cmap is not None, "re-write dropped the colormap (job-0324 regression)"
    for idx in _NLCD_COLORMAP:
        assert out_cmap.get(idx) == src_cmap.get(idx), (
            idx,
            out_cmap.get(idx),
            src_cmap.get(idx),
        )


def test_clip_raster_bytes_preserves_colormap():
    """``_clip_raster_bytes_to_bbox`` carries the embedded color table forward."""
    quantized = data_fetch.round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    paletted = _make_paletted_nlcd_geotiff_bytes(quantized, pad=True)
    assert _colormap_of_bytes(paletted) is not None  # sanity: source has one

    clipped = data_fetch._clip_raster_bytes_to_bbox(paletted, quantized)
    _assert_colormap_round_trip_equal(paletted, clipped)
    # Band marked palette so TiTiler treats pixels as indices.
    assert _colorinterp0_of_bytes(clipped) == "palette"


def test_rasterio_translate_to_cog_preserves_colormap_and_overviews():
    """``_rasterio_translate_to_cog`` keeps the colormap AND builds overviews."""
    quantized = data_fetch.round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    paletted = _make_paletted_nlcd_geotiff_bytes(quantized)
    assert not data_fetch._has_overviews(paletted)

    cog = data_fetch._rasterio_translate_to_cog(paletted)
    assert isinstance(cog, bytes) and len(cog) > 0
    # Colormap preserved (vs the source's round-tripped table).
    _assert_colormap_round_trip_equal(paletted, cog)
    # Overviews still present (the F33 fix must not regress either).
    assert data_fetch._has_overviews(cog), "COG translate must keep overviews"


def test_landcover_bytes_to_cog_preserves_colormap_overviews_and_clip():
    """Full NLCD pipeline: colormap + overviews + exact-bbox clip TOGETHER."""
    import rasterio
    import tempfile

    quantized = data_fetch.round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    paletted = _make_paletted_nlcd_geotiff_bytes(quantized, pad=True)

    cog = data_fetch._landcover_bytes_to_cog(paletted, quantized)
    assert isinstance(cog, bytes) and len(cog) > 0

    # (1) Colormap preserved end-to-end.
    _assert_colormap_round_trip_equal(paletted, cog)

    # (2) Overviews present.
    assert data_fetch._has_overviews(cog)

    # (3) Clipped to the requested bbox (~2 px slack at 30 m).
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        f.write(cog)
        cog_path = f.name
    try:
        with rasterio.open(cog_path) as src:
            b = src.bounds
            tol = 0.0006
            assert abs(b.left - quantized[0]) < tol
            assert abs(b.bottom - quantized[1]) < tol
            assert abs(b.right - quantized[2]) < tol
            assert abs(b.top - quantized[3]) < tol
    finally:
        os.unlink(cog_path)


def test_clip_raster_bytes_no_colormap_passes_through_unchanged():
    """A continuous raster (NO color table) is NOT given a fabricated colormap."""
    quantized = data_fetch.round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    flat = _make_flat_nlcd_geotiff_bytes(quantized, pad=True)
    assert _colormap_of_bytes(flat) is None  # sanity: no table to begin with

    clipped = data_fetch._clip_raster_bytes_to_bbox(flat, quantized)
    assert _colormap_of_bytes(clipped) is None, "must NOT fabricate a colormap"
    # colorinterp must remain gray (not flipped to palette).
    assert _colorinterp0_of_bytes(clipped) != "palette"


def test_rasterio_translate_to_cog_no_colormap_passes_through_unchanged():
    """COG translate of a non-paletted raster: overviews built, NO colormap added."""
    quantized = data_fetch.round_bbox_to_resolution(FORT_MYERS_BBOX, 30)
    flat = _make_flat_nlcd_geotiff_bytes(quantized)

    cog = data_fetch._rasterio_translate_to_cog(flat)
    assert isinstance(cog, bytes) and len(cog) > 0
    assert _colormap_of_bytes(cog) is None, "must NOT fabricate a colormap on DEM-like"
    assert data_fetch._has_overviews(cog), "overviews still build for non-paletted"


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
    """OSM-primary fetcher (mocked) + mocked GCS → vector LayerURI on the .fgb path.

    Job: OSM Overpass is the PRIMARY river-geometry source. The happy path
    mocks the primary fetcher and asserts the LayerURI shape (vector / .fgb /
    river_geometry cache prefix). layer_id is now provider-agnostic
    (``rivers-<lon>-<lat>``), no longer HUC4-coded, because the source is
    decided by the internal fallback chain at fetch time.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_osm_waterway_geometry_bytes",
        lambda bbox: b"FAKE_FLATGEOBUF_BYTES",
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
    # Provider-agnostic layer_id; renders inline (NOT published via publish_layer).
    assert layer.layer_id.startswith("rivers-")
    assert layer.name == "Rivers & Streams"


def test_fetch_river_geometry_cache_key_distinct_per_bbox(monkeypatch):
    """Two disjoint regions must NOT collide on the cache key.

    The cache key is keyed on the quantized bbox (+ best-effort HUC4), so two
    small boxes in different regions (Fort Myers vs LA basin) produce
    different cache paths even though both flow through the OSM-primary chain.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_osm_waterway_geometry_bytes",
        lambda bbox: b"FAKE_FLATGEOBUF_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    fl_layer = fetch_river_geometry(FORT_MYERS_BBOX)
    # CA south coast — small bbox in the LA basin.
    ca_bbox = (-118.4, 33.8, -118.2, 34.0)
    ca_layer = fetch_river_geometry(ca_bbox)
    assert fl_layer.uri != ca_layer.uri, "different bboxes must hit different cache keys"


def test_fetch_river_geometry_rejects_unknown_source():
    with pytest.raises(BboxInvalidError):
        fetch_river_geometry(FORT_MYERS_BBOX, source="merit_hydro")


def test_fetch_river_geometry_works_outside_huc4_envelope_via_osm(monkeypatch):
    """A bbox outside every v0.1 HUC4 envelope still succeeds via OSM-primary.

    Root-cause fix: previously a bbox center outside the hardcoded HUC4
    envelopes dead-ended with "could not route bbox to a HUC4 region". Now OSM
    Overpass is the primary path, so an out-of-HUC4 bbox returns a valid vector
    LayerURI (the OSM fetcher is mocked here so no network is touched).
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    monkeypatch.setattr(
        data_fetch,
        "_fetch_osm_waterway_geometry_bytes",
        lambda bbox: b"FAKE_FLATGEOBUF_BYTES",
    )
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    # Kansas — a CONUS bbox not in any v0.1 HUC4 envelope (the old failure case).
    kansas_bbox = (-97.4, 37.6, -97.2, 37.8)
    assert data_fetch._huc4_for_bbox(kansas_bbox) is None
    layer = fetch_river_geometry(kansas_bbox)
    assert layer.layer_type == "vector"
    assert layer.uri.endswith(".fgb")


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
# F30 fix — fetch_river_geometry OSM Overpass PRIMARY path + fallback ordering.
# ---------------------------------------------------------------------------


# A small bbox over a couple of synthetic "rivers". KANSAS_BBOX is NOT in any
# v0.1 HUC4 envelope — the exact case that used to dead-end.
KANSAS_BBOX = (-97.4, 37.6, -97.2, 37.8)


def _fake_overpass_waterway_payload(bbox):
    """Build a fake Overpass JSON response with waterways spanning the bbox.

    Two ways: one river crossing the bbox left-to-right (spans the full
    width), one stream that extends OUTSIDE the bbox on the right edge so the
    clip step has something to trim.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = 0.5 * (min_lat + max_lat)
    return {
        "elements": [
            {
                "type": "way",
                "id": 1001,
                "tags": {"waterway": "river", "name": "Big River"},
                # spans the full bbox width along mid-latitude
                "geometry": [
                    {"lat": mid_lat, "lon": min_lon},
                    {"lat": mid_lat, "lon": 0.5 * (min_lon + max_lon)},
                    {"lat": mid_lat, "lon": max_lon},
                ],
            },
            {
                "type": "way",
                "id": 1002,
                "tags": {"waterway": "stream", "name": "Edge Creek"},
                # starts inside, extends well outside the right edge
                "geometry": [
                    {"lat": min_lat + 0.01, "lon": max_lon - 0.01},
                    {"lat": min_lat + 0.01, "lon": max_lon + 0.5},
                ],
            },
        ]
    }


def test_fetch_river_geometry_osm_returns_bbox_filling_geometry(monkeypatch):
    """PRIMARY OSM path: waterways fill the whole bbox and are clipped to it.

    Mocks the Overpass POST, runs the real FGB-serialization + clip path, then
    decodes the FlatGeobuf and asserts (a) features are present, (b) the
    union's x-extent spans most of the bbox width (fills the bbox, unlike the
    NLDI seed-trace), and (c) NO geometry spills outside the requested bbox
    (the clip trimmed the stream that ran off the right edge).
    """
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    captured = {}

    def _fake_post(url, data=None, headers=None, timeout=None, **_kw):
        captured["url"] = url
        captured["ql"] = (data or {}).get("data")

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self_inner):
                return _fake_overpass_waterway_payload(KANSAS_BBOX)

        return _Resp()

    monkeypatch.setattr(data_fetch.requests, "post", _fake_post)

    quantized = data_fetch.round_bbox_to_resolution(KANSAS_BBOX, 10)
    fgb_bytes = data_fetch._fetch_osm_waterway_geometry_bytes(quantized)
    assert isinstance(fgb_bytes, bytes) and len(fgb_bytes) > 0

    # The Overpass QL targets waterways, not highways, and uses (s,w,n,e).
    assert "waterway" in captured["ql"]
    assert "river|stream|canal" in captured["ql"]
    assert captured["url"].endswith("/api/interpreter")

    # Decode the FlatGeobuf and verify geometry fills + is clipped to the bbox.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        f.write(fgb_bytes)
        fgb_path = f.name
    try:
        gdf = gpd.read_file(fgb_path)
    finally:
        os.unlink(fgb_path)

    assert len(gdf) >= 1, "OSM path must return at least one waterway feature"
    minx, miny, maxx, maxy = gdf.total_bounds
    bbox_w = quantized[2] - quantized[0]
    # Fills the bbox: union x-extent spans most of the width (the NLDI seed
    # trace would only cover a connected sub-network, not the full bbox).
    assert (maxx - minx) >= 0.5 * bbox_w
    # Clipped: nothing spills outside the requested bbox (small float epsilon).
    eps = 1e-6
    assert minx >= quantized[0] - eps
    assert maxx <= quantized[2] + eps
    assert miny >= quantized[1] - eps
    assert maxy <= quantized[3] + eps


def test_fetch_river_geometry_falls_back_to_nhdplus_when_osm_fails(monkeypatch):
    """Fallback ordering: OSM primary fails → NHDPlus HR (when HUC4 resolves) is used."""
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    calls = []

    def _osm_boom(bbox):
        calls.append("osm")
        raise UpstreamAPIError("simulated Overpass outage")

    def _nhd_ok(bbox, huc4):
        calls.append(("nhd", huc4))
        return b"FAKE_NHDPLUS_FLATGEOBUF"

    monkeypatch.setattr(data_fetch, "_fetch_osm_waterway_geometry_bytes", _osm_boom)
    monkeypatch.setattr(data_fetch, "_fetch_nhdplushr_geometry_bytes", _nhd_ok)
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    # Fort Myers routes to HUC4 0309, so the NHDPlus fallback is available.
    layer = fetch_river_geometry(FORT_MYERS_BBOX)
    assert layer.layer_type == "vector"
    assert layer.uri.endswith(".fgb")
    # OSM was tried FIRST, then NHDPlus HR with the resolved HUC4.
    assert calls[0] == "osm"
    assert calls[1] == ("nhd", "0309")


def test_fetch_river_geometry_typed_error_when_all_sources_fail(monkeypatch):
    """Both OSM (primary) and NHDPlus HR (fallback) fail → typed UpstreamAPIError.

    Data-source-fallback norm: never a silent dead-end or hallucinated success.
    """
    fake_storage = FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    def _osm_boom(bbox):
        raise UpstreamAPIError("simulated Overpass outage")

    def _nhd_boom(bbox, huc4):
        raise UpstreamAPIError("simulated NHDPlus 404")

    monkeypatch.setattr(data_fetch, "_fetch_osm_waterway_geometry_bytes", _osm_boom)
    monkeypatch.setattr(data_fetch, "_fetch_nhdplushr_geometry_bytes", _nhd_boom)
    monkeypatch.setattr(
        data_fetch,
        "read_through",
        lambda *a, **kw: cache_mod.read_through(
            *a, storage_client=fake_storage, now=PINNED_NOW, **kw
        ),
    )

    with pytest.raises(UpstreamAPIError):
        fetch_river_geometry(FORT_MYERS_BBOX)


def test_fetch_river_geometry_osm_only_when_no_huc4_and_osm_fails(monkeypatch):
    """OSM fails AND no HUC4 fallback available → typed UpstreamAPIError (no dead-end)."""
    def _osm_boom(bbox):
        raise UpstreamAPIError("simulated Overpass outage")

    monkeypatch.setattr(data_fetch, "_fetch_osm_waterway_geometry_bytes", _osm_boom)
    # Kansas is outside every HUC4 envelope, so there is no NHDPlus fallback.
    assert data_fetch._huc4_for_bbox(KANSAS_BBOX) is None
    with pytest.raises(UpstreamAPIError):
        data_fetch._fetch_river_geometry_bytes(
            data_fetch.round_bbox_to_resolution(KANSAS_BBOX, 10), None
        )


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
