"""Unit tests for the QGIS discovery atomic tools (job-0034, FR-AS-9 Level 1a).

Coverage:

- ``list_qgis_algorithms`` happy path with a stubbed submitter — parses
  representative ``qgis_process list`` output and returns capped/ranked
  summaries.
- ``describe_qgis_algorithm`` happy path with a stubbed submitter — parses
  ``qgis_process help <id>`` into structured parameter + output dicts.
- Cache-hit replay: a second call with the same params returns the same
  result without re-invoking the submitter (FR-DC-3 / FR-DC-4).
- Worker submission failure re-raises (NFR-R-1 / FR-CE-8 fail-fast).
- The ``set_worker_submitter`` DI binding wires the qgis_process body so it
  no longer raises ``RuntimeError("worker submitter is not bound")``.
- Registry presence: both tools appear in ``TOOL_REGISTRY`` after the
  eager-import wiring in ``grace2_agent.main._import_tools_registry``.
"""

from __future__ import annotations

import pytest

from grace2_agent.tools import TOOL_REGISTRY, passthroughs, qgis_discovery
from grace2_agent.tools.qgis_discovery import (
    MAX_LIST_RESULTS,
    SOURCE_CLASS,
    _parse_qgis_help_output,
    _parse_qgis_list_output,
    describe_qgis_algorithm,
    list_qgis_algorithms,
)


# ---------------------------------------------------------------------------
# Representative fixtures from real qgis_process output (QGIS 3.40 local).
# ---------------------------------------------------------------------------


_FAKE_LIST_OUTPUT = """Available algorithms

QGIS (3D)
\t3d:tessellate\tTessellate

GDAL
\tgdal:aspect\tAspect
\tgdal:cliprasterbyextent\tClip raster by extent

QGIS (native c++)
\tnative:zonalstatistics\tZonal statistics (in place)
\tnative:reprojectlayer\tReproject layer
\tnative:reclassifybytable\tReclassify by table
"""


_FAKE_HELP_OUTPUT = """Zonal statistics (in place) (native:zonalstatistics)

----------------
Description
----------------
Calculates statistics for a raster layer's values for each feature of an overlapping polygon vector layer.

----------------
Arguments
----------------

INPUT_RASTER: Raster layer
\tArgument type:\traster
\tAcceptable values:
\t\t- Path to a raster layer
RASTER_BAND: Raster band
\tDefault value:\t1
\tArgument type:\tband
\tAcceptable values:
\t\t- Integer value representing an existing raster band number
INPUT_VECTOR: Vector layer containing zones
\tArgument type:\tvector
\tAcceptable values:
\t\t- Path to a vector layer
COLUMN_PREFIX: Output column prefix
\tDefault value:\t_
\tArgument type:\tstring

----------------
Outputs
----------------

INPUT_VECTOR: Zonal statistics <outputVector>
"""


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


class _FakeBlob:
    """In-memory ``google.cloud.storage`` blob duck-type for cache tests."""

    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self.custom_time = None
        self.cache_control = None

    def exists(self) -> bool:
        return self._path in self._store

    def download_as_bytes(self) -> bytes:
        return self._store[self._path]

    def upload_from_string(self, data: bytes | str, content_type: str | None = None) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._store[self._path] = data


class _FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._store, path)


class _FakeStorageClient:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self._store)


@pytest.fixture()
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> _FakeStorageClient:
    """Patch read_through's GCS lookup to use an in-memory FakeStorageClient.

    Cache shim auto-instantiates ``storage.Client()`` on miss; we intercept
    it by patching ``read_through`` to receive an explicit
    ``storage_client``. The simplest path: monkeypatch ``read_through`` so
    it always gets our fake. We re-export the helper from the module so
    tests can call directly.
    """
    from grace2_agent.tools import cache as cache_mod

    fake = _FakeStorageClient()
    real_read_through = cache_mod.read_through

    def wrapped(metadata, params, ext, fetch_fn, **kwargs):
        kwargs.setdefault("storage_client", fake)
        return real_read_through(metadata, params, ext, fetch_fn, **kwargs)

    monkeypatch.setattr(qgis_discovery, "read_through", wrapped)
    return fake


@pytest.fixture()
def stubbed_submitter():
    """Bind a programmable stub submitter and restore on teardown.

    Each test sets ``stub.responses["list"]`` / ``stub.responses["help:<id>"]``
    to the dict the submitter should return; ``stub.calls`` records invocation
    args for assertions.
    """

    class _Stub:
        def __init__(self) -> None:
            self.responses: dict[str, dict] = {}
            self.calls: list[tuple[tuple, int]] = []

        def __call__(self, args: list[str], timeout_s: int) -> dict:
            self.calls.append((tuple(args), timeout_s))
            if args[0] == "list":
                return self.responses.get(
                    "list",
                    {"stdout": _FAKE_LIST_OUTPUT, "returncode": 0, "duration_s": 0.1},
                )
            if args[0] == "help":
                key = f"help:{args[1]}"
                return self.responses.get(
                    key,
                    {"stdout": _FAKE_HELP_OUTPUT, "returncode": 0, "duration_s": 0.05},
                )
            raise AssertionError(f"unexpected stub call: {args!r}")

    stub = _Stub()
    saved = passthroughs._WORKER_SUBMITTER
    passthroughs.set_worker_submitter(stub)
    try:
        yield stub
    finally:
        passthroughs._WORKER_SUBMITTER = saved  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Registration / metadata.
# ---------------------------------------------------------------------------


def test_discovery_tools_register_with_expected_metadata() -> None:
    """Both tools land in ``TOOL_REGISTRY`` with static-30d/qgis_algorithms_catalog."""
    for tool_name in ("list_qgis_algorithms", "describe_qgis_algorithm"):
        assert tool_name in TOOL_REGISTRY, f"{tool_name} not registered"
        entry = TOOL_REGISTRY[tool_name]
        assert entry.metadata.ttl_class == "static-30d"
        assert entry.metadata.source_class == SOURCE_CLASS
        assert entry.metadata.cacheable is True


# ---------------------------------------------------------------------------
# Parser unit tests (no submitter, no cache — pure functions).
# ---------------------------------------------------------------------------


def test_parse_list_extracts_provider_and_id() -> None:
    summaries = _parse_qgis_list_output(_FAKE_LIST_OUTPUT)
    ids = [s["algorithm_id"] for s in summaries]
    assert "3d:tessellate" in ids
    assert "gdal:aspect" in ids
    assert "native:zonalstatistics" in ids
    # The provider header for `native:zonalstatistics` is "QGIS (native c++)".
    zs = next(s for s in summaries if s["algorithm_id"] == "native:zonalstatistics")
    assert zs["provider"] == "QGIS (native c++)"
    assert zs["name"] == "Zonal statistics (in place)"


def test_parse_help_extracts_parameters_and_outputs() -> None:
    desc = _parse_qgis_help_output(_FAKE_HELP_OUTPUT, "native:zonalstatistics")
    assert desc["algorithm_id"] == "native:zonalstatistics"
    assert desc["name"] == "Zonal statistics (in place)"
    assert "Calculates statistics" in desc["description"]
    param_names = [p["name"] for p in desc["parameters"]]
    assert param_names == [
        "INPUT_RASTER",
        "RASTER_BAND",
        "INPUT_VECTOR",
        "COLUMN_PREFIX",
    ]
    raster_band = next(p for p in desc["parameters"] if p["name"] == "RASTER_BAND")
    assert raster_band["type"] == "band"
    assert raster_band["default"] == "1"
    # Outputs are parsed too.
    out_names = [o["name"] for o in desc["outputs"]]
    assert "INPUT_VECTOR" in out_names
    # Raw help is preserved for tolerant agents.
    assert "Zonal statistics" in desc["raw_help"]


# ---------------------------------------------------------------------------
# Tool happy paths via the stubbed submitter + fake cache.
# ---------------------------------------------------------------------------


def test_list_qgis_algorithms_happy_path(
    stubbed_submitter, fake_storage: _FakeStorageClient
) -> None:
    """Stubbed submitter + fake cache → tool returns parsed summaries."""
    result = list_qgis_algorithms()
    assert isinstance(result, list)
    assert result, "expected at least one summary from the fake list output"
    # Capped to MAX_LIST_RESULTS.
    assert len(result) <= MAX_LIST_RESULTS
    # The fake fixture has 6 algorithms — well under the cap.
    assert len(result) == 6
    # Submitter called exactly once on the first call.
    assert len(stubbed_submitter.calls) == 1
    assert stubbed_submitter.calls[0][0] == ("list",)


def test_describe_qgis_algorithm_happy_path(
    stubbed_submitter, fake_storage: _FakeStorageClient
) -> None:
    desc = describe_qgis_algorithm("native:zonalstatistics")
    assert desc["algorithm_id"] == "native:zonalstatistics"
    assert any(p["name"] == "INPUT_RASTER" for p in desc["parameters"])
    # Submitter called exactly once with the help args.
    assert stubbed_submitter.calls == [(("help", "native:zonalstatistics"), 60)]


def test_list_qgis_algorithms_category_filter(
    stubbed_submitter, fake_storage: _FakeStorageClient
) -> None:
    result = list_qgis_algorithms(category_filter="gdal")
    assert all("gdal" in s["provider"].lower() for s in result)
    assert all(s["algorithm_id"].startswith("gdal:") for s in result)


def test_list_qgis_algorithms_search_terms_ranks_matches_first(
    stubbed_submitter, fake_storage: _FakeStorageClient
) -> None:
    result = list_qgis_algorithms(search_terms="zonal")
    # The matching algorithm is first.
    assert result[0]["algorithm_id"] == "native:zonalstatistics"


# ---------------------------------------------------------------------------
# Cache-hit replay.
# ---------------------------------------------------------------------------


def test_list_qgis_algorithms_cache_hit_replays_without_submitter_call(
    stubbed_submitter, fake_storage: _FakeStorageClient
) -> None:
    """Second call hits the (fake) cache and skips the submitter."""
    first = list_qgis_algorithms()
    assert len(stubbed_submitter.calls) == 1

    # The fake bucket should now hold one blob.
    assert len(fake_storage._store) == 1

    second = list_qgis_algorithms()
    # No additional submitter call.
    assert len(stubbed_submitter.calls) == 1
    # Same parsed result.
    assert [s["algorithm_id"] for s in second] == [s["algorithm_id"] for s in first]


def test_describe_qgis_algorithm_cache_hit_replays_without_submitter_call(
    stubbed_submitter, fake_storage: _FakeStorageClient
) -> None:
    first = describe_qgis_algorithm("native:zonalstatistics")
    assert len(stubbed_submitter.calls) == 1
    second = describe_qgis_algorithm("native:zonalstatistics")
    assert len(stubbed_submitter.calls) == 1
    assert first["parameters"] == second["parameters"]


# ---------------------------------------------------------------------------
# Failure paths.
# ---------------------------------------------------------------------------


def test_submitter_failure_re_raises_no_sentinel_written(
    stubbed_submitter, fake_storage: _FakeStorageClient
) -> None:
    """A failing submitter raises through; the cache stays empty (no poison)."""

    def _boom(args: list[str], timeout_s: int) -> dict:
        raise RuntimeError("simulated worker failure")

    passthroughs.set_worker_submitter(_boom)
    with pytest.raises(RuntimeError, match="simulated worker failure"):
        list_qgis_algorithms()
    # No sentinel persisted on failure.
    assert fake_storage._store == {}


def test_discovery_tool_with_no_submitter_bound_raises(
    fake_storage: _FakeStorageClient,
) -> None:
    """An unbound submitter raises a clear ``RuntimeError`` per FR-CE-8."""
    saved = passthroughs._WORKER_SUBMITTER
    passthroughs._WORKER_SUBMITTER = None  # type: ignore[attr-defined]
    try:
        with pytest.raises(RuntimeError, match="worker submitter is not bound"):
            list_qgis_algorithms()
    finally:
        passthroughs._WORKER_SUBMITTER = saved  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# qgis_process DI binding — the body no longer raises NotImplementedError.
# ---------------------------------------------------------------------------


def test_qgis_process_raises_runtime_error_when_no_backend(monkeypatch) -> None:
    """With no docker image, no docker, and no local qgis_process, the
    qgis_process pass-through raises an actionable RuntimeError.

    job-0308 (Decision Q) rewired qgis_process OFF the old job-0032
    NotImplementedError stub and onto a stage-then-mount docker path
    (``GRACE2_QGIS_DOCKER_IMAGE`` / the ``grace2-qgis`` image present on the
    EC2 box) with a local-``qgis_process``-on-PATH dev fallback. When NO
    backend is reachable the body raises a RuntimeError telling the operator
    how to provide one. This pins that contract deterministically — we
    monkeypatch ``shutil.which`` to None and clear the image env so the
    result does not depend on whether docker / qgis_process happen to be
    installed on the test host. (The ``_WORKER_SUBMITTER`` binding is NOT
    used by qgis_process anymore — it remains live only for the discovery
    tools, covered by the tests above.)
    """
    import shutil

    from grace2_agent.tools.passthroughs import qgis_process

    monkeypatch.delenv("GRACE2_QGIS_DOCKER_IMAGE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="qgis_process unavailable"):
        qgis_process(algorithm="native:zonalstatistics", params={})
