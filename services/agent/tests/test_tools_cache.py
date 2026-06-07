"""Unit + integration tests for the cache shim (job-0032, FR-DC-3, FR-DC-6).

Coverage:
- Cache-key determinism: identical inputs at the same vintage produce the
  same key.
- Cache-key vintage separation: different TTL bucket vintages produce
  different keys.
- TTL-bucket vintage strings for each of the four classes.
- ``cache_path`` matches the job-0031 live layout
  (``cache/<ttl-class>/<source-class>/<hash>.<ext>``).
- ``is_cacheable`` for each of the four TTL classes (parametrized).
- Read-through-on-hit: pre-seeded GCS blob is returned verbatim and
  ``fetch_fn`` is NOT invoked.
- Write-on-miss: ``fetch_fn`` is invoked, the blob lands with
  ``custom_time`` set, and the URI is returned.
- ``live-no-cache`` short-circuit: ``fetch_fn`` invoked, no GCS write.
- ``force_refresh=True``: lookup skipped, fetcher invoked, write executed.
- ``fetch_fn`` failure re-raises without writing a sentinel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from grace2_contracts.tool_registry import AtomicToolMetadata

from grace2_agent.tools.cache import (
    CACHE_KEY_HEX_LEN,
    cache_path,
    compute_cache_key,
    is_cacheable,
    read_through,
    ttl_bucket_vintage,
)


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_cache_key_is_deterministic_for_same_inputs():
    """Same source_id + params + vintage produce byte-identical keys."""
    pinned = datetime(2026, 6, 7, 3, 30, 0, tzinfo=timezone.utc)
    k1 = compute_cache_key(
        "dem", {"bbox": [-90.5, 32.0, -90.0, 32.5]}, "static-30d", now=pinned
    )
    k2 = compute_cache_key(
        "dem", {"bbox": [-90.5, 32.0, -90.0, 32.5]}, "static-30d", now=pinned
    )
    assert k1 == k2
    assert len(k1) == CACHE_KEY_HEX_LEN
    # Hex chars only.
    int(k1, 16)


def test_cache_key_separates_across_ttl_bucket_vintages():
    """Different TTL-bucket vintages produce different keys for same inputs.

    Acceptance criterion: ``dynamic-1h`` keys for the SAME params 90 minutes
    apart produce DIFFERENT keys; ``static-30d`` keys 5 days apart produce
    the SAME key.
    """
    params = {"bbox": [0.0, 0.0, 1.0, 1.0]}

    # dynamic-1h: 90 minutes apart -> different vintage strings -> different keys
    t1 = datetime(2026, 6, 7, 3, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 7, 4, 40, 0, tzinfo=timezone.utc)  # +1h30m -> next bucket
    k1 = compute_cache_key("nwis_iv", params, "dynamic-1h", now=t1)
    k2 = compute_cache_key("nwis_iv", params, "dynamic-1h", now=t2)
    assert k1 != k2

    # static-30d: 5 days apart, same calendar month -> same vintage -> same key
    t3 = datetime(2026, 6, 7, 3, 10, 0, tzinfo=timezone.utc)
    t4 = datetime(2026, 6, 12, 3, 10, 0, tzinfo=timezone.utc)
    k3 = compute_cache_key("dem", params, "static-30d", now=t3)
    k4 = compute_cache_key("dem", params, "static-30d", now=t4)
    assert k3 == k4


def test_cache_key_separates_across_source_ids_and_params():
    pinned = datetime(2026, 6, 7, 3, 30, 0, tzinfo=timezone.utc)
    base = compute_cache_key("dem", {"bbox": [0, 0, 1, 1]}, "static-30d", now=pinned)
    other_source = compute_cache_key(
        "buildings", {"bbox": [0, 0, 1, 1]}, "static-30d", now=pinned
    )
    other_params = compute_cache_key(
        "dem", {"bbox": [0, 0, 1, 2]}, "static-30d", now=pinned
    )
    assert base != other_source
    assert base != other_params


def test_cache_key_canonicalization_ignores_none_and_key_order():
    """Canonicalization drops None values and sorts keys.

    Two calls that differ only in dict-key ordering or in including/omitting
    a None value should map to the same key.
    """
    pinned = datetime(2026, 6, 7, 3, 30, 0, tzinfo=timezone.utc)
    a = compute_cache_key("x", {"a": 1, "b": 2}, "static-30d", now=pinned)
    b = compute_cache_key("x", {"b": 2, "a": 1}, "static-30d", now=pinned)
    c = compute_cache_key(
        "x", {"a": 1, "b": 2, "optional": None}, "static-30d", now=pinned
    )
    assert a == b == c


def test_ttl_bucket_vintage_per_class():
    pinned = datetime(2026, 6, 7, 3, 30, 45, tzinfo=timezone.utc)
    assert ttl_bucket_vintage("static-30d", now=pinned) == "2026-06"
    assert ttl_bucket_vintage("semi-static-7d", now=pinned) == "2026-W23"
    assert ttl_bucket_vintage("dynamic-1h", now=pinned) == "2026-06-07T03:00:00Z"
    assert ttl_bucket_vintage("live-no-cache", now=pinned) == "live"


def test_cache_path_matches_job_0031_layout():
    """cache_path produces cache/<ttl-class>/<source-class>/<hash>.<ext>."""
    p = cache_path("dem", "static-30d", "abc123", "tif")
    assert p == "cache/static-30d/dem/abc123.tif"

    # Accepts ext with or without leading dot.
    p2 = cache_path("buildings", "semi-static-7d", "deadbeef", ".fgb")
    assert p2 == "cache/semi-static-7d/buildings/deadbeef.fgb"


@pytest.mark.parametrize(
    "ttl_class, cacheable, expected",
    [
        ("static-30d", True, True),
        ("semi-static-7d", True, True),
        ("dynamic-1h", True, True),
        ("live-no-cache", False, False),
    ],
)
def test_is_cacheable_per_ttl_class(ttl_class, cacheable, expected):
    md = AtomicToolMetadata(
        name="t",
        ttl_class=ttl_class,
        source_class="x" if cacheable else None,
        cacheable=cacheable,
    )
    assert is_cacheable(md) is expected


# ---------------------------------------------------------------------------
# read_through integration tests (with a fake GCS client)
# ---------------------------------------------------------------------------


class FakeBlob:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self.custom_time: datetime | None = None  # google-cloud-storage SDK requires datetime, NOT str (OQ-33 hotfix)
        self.cache_control: str | None = None
        self.content_type: str | None = None

    def exists(self) -> bool:
        return self._path in self._store

    def download_as_bytes(self) -> bytes:
        return self._store[self._path]

    def upload_from_string(
        self, data: bytes | str, content_type: str | None = None
    ) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._store[self._path] = data
        self.content_type = content_type


class FakeBucket:
    def __init__(self, name: str, store: dict[str, bytes]) -> None:
        self.name = name
        self._store = store
        # Track the most recent blob per path so the test can inspect
        # custom_time / cache_control set during a write.
        self.last_blob: FakeBlob | None = None

    def blob(self, path: str) -> FakeBlob:
        b = FakeBlob(self._store, path)
        self.last_blob = b
        return b


class FakeStorageClient:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self._buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        if name not in self._buckets:
            self._buckets[name] = FakeBucket(name, self.store)
        return self._buckets[name]


@pytest.fixture()
def fake_gcs() -> FakeStorageClient:
    return FakeStorageClient()


def _cacheable_md() -> AtomicToolMetadata:
    return AtomicToolMetadata(
        name="fetch_demo",
        ttl_class="static-30d",
        source_class="demo",
        cacheable=True,
    )


def test_read_through_hit_returns_bytes_and_skips_fetch_fn(fake_gcs):
    md = _cacheable_md()
    pinned = datetime(2026, 6, 7, 3, 0, 0, tzinfo=timezone.utc)

    # Pre-seed the cache at the path the shim will look up.
    key = compute_cache_key(
        md.source_class, {"bbox": [0, 0, 1, 1]}, md.ttl_class, now=pinned
    )
    path = cache_path(md.source_class, md.ttl_class, key, "tif")
    fake_gcs.store[path] = b"cached-payload"

    invoked = {"n": 0}

    def fetch_fn() -> bytes:
        invoked["n"] += 1
        return b"FRESH"

    result = read_through(
        metadata=md,
        params={"bbox": [0, 0, 1, 1]},
        ext="tif",
        fetch_fn=fetch_fn,
        storage_client=fake_gcs,
        now=pinned,
    )

    assert result.hit is True
    assert result.data == b"cached-payload"
    assert result.uri == f"gs://grace-2-hazard-prod-cache/{path}"
    assert invoked["n"] == 0  # fetch_fn not invoked on hit


def test_read_through_miss_writes_with_custom_time_and_cache_control(fake_gcs):
    md = _cacheable_md()
    pinned = datetime(2026, 6, 7, 3, 0, 0, tzinfo=timezone.utc)

    def fetch_fn() -> bytes:
        return b"freshly-fetched"

    result = read_through(
        metadata=md,
        params={"bbox": [0, 0, 1, 1]},
        ext="tif",
        fetch_fn=fetch_fn,
        storage_client=fake_gcs,
        now=pinned,
    )

    key = compute_cache_key(
        md.source_class, {"bbox": [0, 0, 1, 1]}, md.ttl_class, now=pinned
    )
    expected_path = cache_path(md.source_class, md.ttl_class, key, "tif")

    assert result.hit is False
    assert result.data == b"freshly-fetched"
    assert result.uri == f"gs://grace-2-hazard-prod-cache/{expected_path}"
    # FR-DC-3: customTime set on write so the lifecycle policy can evict.
    bucket = fake_gcs.bucket("grace-2-hazard-prod-cache")
    assert bucket.last_blob is not None
    assert bucket.last_blob.custom_time == pinned  # datetime, not isoformat string (OQ-33 hotfix)
    # Cache-Control reflects the TTL class.
    assert bucket.last_blob.cache_control == "public, max-age=2592000"
    # Persisted in the store at the expected path.
    assert fake_gcs.store[expected_path] == b"freshly-fetched"


def test_read_through_live_no_cache_skips_gcs(fake_gcs):
    """FR-DC-6: live-no-cache tools never touch the bucket."""
    md = AtomicToolMetadata(
        name="mongo_query",
        ttl_class="live-no-cache",
        source_class=None,
        cacheable=False,
    )
    invoked = {"n": 0}

    def fetch_fn() -> bytes:
        invoked["n"] += 1
        return b"live-data"

    result = read_through(
        metadata=md,
        params={"x": 1},
        ext="json",
        fetch_fn=fetch_fn,
        storage_client=fake_gcs,
    )

    assert result.hit is False
    assert result.data == b"live-data"
    assert result.uri is None
    assert invoked["n"] == 1
    # Nothing written to the bucket.
    assert fake_gcs.store == {}


def test_read_through_force_refresh_bypasses_hit(fake_gcs):
    """force_refresh=True invokes fetch_fn even when cache is populated."""
    md = _cacheable_md()
    pinned = datetime(2026, 6, 7, 3, 0, 0, tzinfo=timezone.utc)
    key = compute_cache_key(
        md.source_class, {"bbox": [0, 0, 1, 1]}, md.ttl_class, now=pinned
    )
    path = cache_path(md.source_class, md.ttl_class, key, "tif")
    fake_gcs.store[path] = b"old-cached-payload"

    invoked = {"n": 0}

    def fetch_fn() -> bytes:
        invoked["n"] += 1
        return b"fresh-payload"

    result = read_through(
        metadata=md,
        params={"bbox": [0, 0, 1, 1]},
        ext="tif",
        fetch_fn=fetch_fn,
        storage_client=fake_gcs,
        now=pinned,
        force_refresh=True,
    )
    assert result.hit is False
    assert result.data == b"fresh-payload"
    assert invoked["n"] == 1
    # Fresh data has overwritten the old entry.
    assert fake_gcs.store[path] == b"fresh-payload"


def test_read_through_fetch_failure_reraises_without_sentinel(fake_gcs):
    """On fetch_fn failure: no sentinel written; exception bubbles."""
    md = _cacheable_md()

    class UpstreamUnavailable(RuntimeError):
        pass

    def fetch_fn() -> bytes:
        raise UpstreamUnavailable("3dep returned 503")

    with pytest.raises(UpstreamUnavailable):
        read_through(
            metadata=md,
            params={"bbox": [0, 0, 1, 1]},
            ext="tif",
            fetch_fn=fetch_fn,
            storage_client=fake_gcs,
        )
    # Nothing was written.
    assert fake_gcs.store == {}


# ---------------------------------------------------------------------------
# OQ-33-CACHE-CUSTOMTIME-TYPE-BUG regression (job-0036)
# ---------------------------------------------------------------------------
#
# Bug class: "type-fidelity of cache-side blob attributes"
#
# Until the orchestrator hotfix (commit ca48256), the cache shim assigned
# ``blob.custom_time = fetched_at.isoformat()`` — a STRING. The unit-suite
# FakeStorageClient defined above happily accepted that string (its FakeBlob
# is just attribute assignment). But the REAL google-cloud-storage SDK's
# ``Blob.custom_time`` setter calls ``_datetime_to_rfc3339(value)`` which in
# turn calls ``value.strftime(...)`` — raising
# ``AttributeError: 'str' object has no attribute 'strftime'`` against the
# live bucket.
#
# job-0033's live-evidence runs surfaced this by monkey-patching the setter
# to parse strings back to datetimes; the orchestrator's hotfix in
# ``cache.py:337-338`` dropped the ``.isoformat()`` call so the assignment
# now passes a ``datetime`` instance directly.
#
# The regression test below uses a HIGHER-FIDELITY fake that mirrors the
# real SDK's setter contract: the assignment immediately runs ``strftime``
# on the value, raising on anything but a real ``datetime``. This guards
# against the FakeStorageClient-accepts-anything failure mode of the
# previous fake — the original tests would have stayed green even if the
# bug had reappeared.
# ---------------------------------------------------------------------------


class StrictCustomTimeBlob:
    """FakeBlob that type-checks ``custom_time`` like the real SDK does.

    Mirrors ``google.cloud.storage.Blob.custom_time`` setter behavior:
    ``_datetime_to_rfc3339(value)`` -> ``value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")``.
    Anything that doesn't have ``strftime`` raises ``AttributeError`` at
    assignment time, exactly as the live SDK does against the real bucket.
    """

    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self._custom_time_rfc3339: str | None = None
        self._custom_time_value: Any = None
        self.cache_control: str | None = None
        self.content_type: str | None = None

    @property
    def custom_time(self) -> Any:
        return self._custom_time_value

    @custom_time.setter
    def custom_time(self, value: Any) -> None:
        if value is None:
            self._custom_time_value = None
            self._custom_time_rfc3339 = None
            return
        # This mirrors google.cloud._helpers._datetime_to_rfc3339:
        #   stamp = value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        # A string lacks ``strftime`` and raises here — exactly like the SDK.
        self._custom_time_rfc3339 = value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self._custom_time_value = value

    def exists(self) -> bool:
        return self._path in self._store

    def download_as_bytes(self) -> bytes:
        return self._store[self._path]

    def upload_from_string(
        self, data: bytes | str, content_type: str | None = None
    ) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._store[self._path] = data
        self.content_type = content_type


class StrictCustomTimeBucket:
    def __init__(self, name: str, store: dict[str, bytes]) -> None:
        self.name = name
        self._store = store
        self.last_blob: StrictCustomTimeBlob | None = None

    def blob(self, path: str) -> StrictCustomTimeBlob:
        b = StrictCustomTimeBlob(self._store, path)
        self.last_blob = b
        return b


class StrictCustomTimeStorageClient:
    """Higher-fidelity GCS fake for the OQ-33 regression test.

    The standard ``FakeStorageClient`` above lets arbitrary values land on
    ``blob.custom_time``; this client mirrors the live SDK's strict
    ``datetime``-only contract so the test fails if anyone reverts the
    hotfix and passes a string again.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self._buckets: dict[str, StrictCustomTimeBucket] = {}

    def bucket(self, name: str) -> StrictCustomTimeBucket:
        if name not in self._buckets:
            self._buckets[name] = StrictCustomTimeBucket(name, self.store)
        return self._buckets[name]


def test_oq33_customtime_is_datetime_not_isoformat_string_regression():
    """Regression test for OQ-33-CACHE-CUSTOMTIME-TYPE-BUG.

    The bug: ``cache.py`` previously assigned
    ``blob.custom_time = fetched_at.isoformat()`` (a str). The real
    ``google.cloud.storage`` SDK rejects this with
    ``AttributeError: 'str' object has no attribute 'strftime'`` because the
    setter pipes the value through ``strftime`` to format it for the JSON
    API. The orchestrator hotfix (commit ca48256) drops the ``.isoformat()``
    call so the assignment receives a ``datetime`` instance directly.

    Layer attribution on failure: cache shim (services/agent/src/grace2_agent/
    tools/cache.py:337-338). If this test fails after a future cache.py
    change, the offending line is the ``blob.custom_time = ...`` assignment.

    The higher-fidelity GCS fake (``StrictCustomTimeBlob``) mirrors the real
    SDK's setter contract — assignment calls ``strftime`` immediately. This
    catches the regression class the original ``FakeStorageClient`` missed:
    a fake that accepts anything tests the fake, not the system.
    """
    strict_gcs = StrictCustomTimeStorageClient()
    md = _cacheable_md()
    pinned = datetime(2026, 6, 7, 3, 0, 0, tzinfo=timezone.utc)

    def fetch_fn() -> bytes:
        return b"regression-payload"

    # Pre-condition: this SDK fake would reject a string at assignment time.
    # Verify the fake itself is strict so the test is meaningful.
    probe = strict_gcs.bucket("probe").blob("p")
    with pytest.raises(AttributeError, match="strftime"):
        probe.custom_time = "2026-06-07T03:00:00+00:00"  # the bug's value

    # Now: run through the cache shim against the strict fake. If
    # ``cache.py`` ever reverts to ``.isoformat()`` (or assigns any non-
    # datetime to ``blob.custom_time``), this call raises ``AttributeError``.
    result = read_through(
        metadata=md,
        params={"bbox": [0, 0, 1, 1]},
        ext="tif",
        fetch_fn=fetch_fn,
        storage_client=strict_gcs,
        now=pinned,
    )

    assert result.hit is False, (
        "layer=cache shim: write-on-miss path did not execute; "
        "OQ-33 regression test cannot exercise blob.custom_time setter."
    )
    bucket = strict_gcs.bucket("grace-2-hazard-prod-cache")
    assert bucket.last_blob is not None, (
        "layer=test fake: no blob recorded; cache shim did not call "
        "bucket_obj.blob(path)."
    )
    # The hotfix preserved the assigned VALUE (a datetime), not its string
    # form. Assert both the type AND the value here so a partial reversion
    # (e.g. switching to a `date` instead of a `datetime`) also fails.
    assigned = bucket.last_blob._custom_time_value
    assert isinstance(assigned, datetime), (
        f"layer=cache shim (cache.py:337-338, OQ-33 regression): "
        f"blob.custom_time must be assigned a datetime instance, not "
        f"{type(assigned).__name__}. Real google.cloud.storage rejects "
        f"non-datetime values with AttributeError when piping through "
        f"strftime. Got: {assigned!r}"
    )
    assert assigned == pinned, (
        f"layer=cache shim: blob.custom_time should equal the now= pin "
        f"({pinned!r}); got {assigned!r}."
    )
    # And the RFC3339 form materialized through strftime — proves the value
    # is real-SDK-shaped, not just any object with a strftime method.
    assert bucket.last_blob._custom_time_rfc3339 == "2026-06-07T03:00:00.000000Z", (
        f"layer=cache shim: rfc3339 materialization of assigned customTime "
        f"diverged from expected; got "
        f"{bucket.last_blob._custom_time_rfc3339!r}."
    )
