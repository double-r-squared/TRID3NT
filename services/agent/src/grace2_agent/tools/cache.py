"""FR-DC-3 cache shim — read-through / write-on-miss with content-addressed keys.

This module owns the agent-side cache shim that mediates every external-API
atomic-tool fetch (FR-CE-8). The shim is the SOLE writer of the ``cache/``
prefix on the production cache bucket provisioned by job-0031:

    gs://grace-2-hazard-prod-cache/cache/<ttl-class>/<source-class>/<hash>.<ext>

Note the layout follows the LIVE substrate from job-0031, NOT the FR-DC-1
literal (``cache/<source-class>/<hash>.<ext>``). job-0031 nested TTL class
above source class so the bucket's GCS Object Lifecycle Management policy
can run on FOUR rules forever instead of one-per-source-class. The
``OQ-INFRA-31-FR-DC-1`` schema-pushback proposes the matching SRS amendment.

Cache-key derivation (FR-DC-3):

    key = sha256(source_id || canonical_params_json || ttl_bucket_vintage)[:32]

- ``canonical_params_json`` sorts keys, omits ``None``/default values, and
  quantizes ranges (bbox to source-native resolution if a hint is passed,
  dates to the TTL bucket boundary).
- ``ttl_bucket_vintage`` is the current TTL-class window boundary:
  - ``static-30d`` -> ``"2026-06"`` (year-month)
  - ``semi-static-7d`` -> ``"2026-W23"`` (ISO year-week)
  - ``dynamic-1h`` -> ``"2026-06-07T03:00:00Z"`` (top-of-hour UTC)
  - ``live-no-cache`` -> ``"live"`` placeholder (read_through short-circuits
    so the key never lands in GCS, but compute_cache_key remains pure).

Deduplication (FR-DC-4):
The content-addressed key guarantees two callers asking for the same input
produce the same path. No explicit lock is needed — last-writer-wins on
simultaneous misses produces byte-identical artifacts because the key
already factored in everything that would differ.

Cancellation (Invariant 8):
``read_through`` is a blocking I/O call. It must be invoked from a context
that the agent's WebSocket cancel chain (server.py M1 handler) can cancel
via ``asyncio.CancelledError``. Do NOT introduce a separate cancel mechanism.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from grace2_contracts.tool_registry import AtomicToolMetadata, TTLClass

__all__ = [
    "CACHE_BUCKET",
    "CACHE_KEY_HEX_LEN",
    "compute_cache_key",
    "cache_path",
    "ttl_bucket_vintage",
    "is_cacheable",
    "read_through",
    "ReadThroughResult",
]

logger = logging.getLogger("grace2_agent.tools.cache")

#: Production cache bucket name, provisioned by job-0031.
#: Override via env var ``GRACE2_CACHE_BUCKET`` for non-prod runs.
CACHE_BUCKET = "grace-2-hazard-prod-cache"

#: Truncation length for the sha256 hex digest. 32 hex chars = 128 bits of
#: collision resistance — birthday-bound probability of collision after 2^64
#: keys is negligible for the workload described in §3.9. TENTATIVE per the
#: kickoff (longer narrows collision probability at the cost of path length).
CACHE_KEY_HEX_LEN = 32


def _canonicalize_params(params: dict[str, Any]) -> str:
    """Deterministic JSON serialization of the params dict.

    Rules (FR-DC-3 canonicalized_params):
    - Sort keys.
    - Omit ``None`` values (treat-as-default).
    - No whitespace ('separators=(",", ":")' for compactness + determinism).
    - ``default=str`` so datetimes / Decimal / etc. serialize stably without
      the caller having to pre-format them. (This is intentionally lenient —
      a caller passing an unhashable object gets a stable string-form rather
      than a TypeError; the shim's contract is determinism, not type purity.)

    NOTE: The kickoff calls out bbox-to-source-native-resolution quantization
    and date-range-to-TTL-bucket-boundary quantization. Those are domain-
    specific transformations the CALLER applies before handing the params
    dict to the shim — the shim only canonicalizes whatever it receives. This
    keeps the shim engine-agnostic; the bbox-resolution table and the date-
    quantization rules belong in the engine-owned fetcher modules (job-0033),
    not in the agent's cache surface.
    """
    pruned = {k: v for k, v in params.items() if v is not None}
    return json.dumps(pruned, sort_keys=True, separators=(",", ":"), default=str)


def ttl_bucket_vintage(ttl_class: TTLClass, now: datetime | None = None) -> str:
    """Return the current TTL-class window-boundary string.

    For each TTL class, two calls inside the same window produce the same
    vintage string and thus the same cache key; a boundary crossing forces a
    refresh. The window boundary is computed in UTC.

    - ``static-30d`` -> ``YYYY-MM`` (year-month — coarse but the lifecycle
      policy evicts after 30 days regardless, so per-month bucketing keeps
      keys stable for the entire month and lets the eviction policy do its
      job. Slightly more reuse than per-day; well under 30-day eviction.)
    - ``semi-static-7d`` -> ``YYYY-Www`` (ISO year-week).
    - ``dynamic-1h`` -> top-of-hour UTC ISO-Z (``YYYY-MM-DDTHH:00:00Z``).
    - ``live-no-cache`` -> the literal ``"live"`` (never lands in GCS; see
      ``read_through`` which short-circuits).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if ttl_class == "static-30d":
        return now.strftime("%Y-%m")
    if ttl_class == "semi-static-7d":
        iso_year, iso_week, _ = now.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if ttl_class == "dynamic-1h":
        top_of_hour = now.replace(minute=0, second=0, microsecond=0)
        return top_of_hour.strftime("%Y-%m-%dT%H:00:00Z")
    if ttl_class == "live-no-cache":
        return "live"
    raise ValueError(f"unknown ttl_class: {ttl_class!r}")


def compute_cache_key(
    source_id: str,
    params: dict[str, Any],
    ttl_class: TTLClass,
    *,
    now: datetime | None = None,
) -> str:
    """Compute the content-addressed cache key per FR-DC-3.

    Args:
        source_id: stable identifier for the upstream data source (often the
            ``source_class`` from the tool's ``AtomicToolMetadata``, possibly
            with sub-source detail like ``"atcf:IAN"``).
        params: the call parameters affecting the response. Caller is
            expected to have pre-quantized bbox / date ranges per the
            domain-specific rules in §3.9 / FR-DC-3.
        ttl_class: one of the four FR-DC-2 classes.
        now: time of fetch (default: now UTC). Tests pin this for determinism
            across runs.

    Returns:
        A 32-hex-char prefix of the SHA-256 digest. Same inputs (including
        TTL-bucket vintage) ALWAYS produce the same key; a TTL-bucket-boundary
        crossing changes the vintage and therefore the key.
    """
    vintage = ttl_bucket_vintage(ttl_class, now=now)
    canonical = _canonicalize_params(params)
    raw = f"{source_id}||{canonical}||{vintage}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest[:CACHE_KEY_HEX_LEN]


def cache_path(source_class: str, ttl_class: TTLClass, key: str, ext: str) -> str:
    """Construct the object path under the cache bucket.

    Matches the job-0031 LIVE bucket layout:
        ``cache/<ttl-class>/<source-class>/<key>.<ext>``

    NOT the FR-DC-1 literal (``cache/<source-class>/<hash>.<ext>``); see
    module docstring for the rationale (4-rule lifecycle policy at scale).
    """
    ext_clean = ext.lstrip(".")
    return f"cache/{ttl_class}/{source_class}/{key}.{ext_clean}"


def is_cacheable(metadata: AtomicToolMetadata) -> bool:
    """Wrap the FR-DC-6 enumeration check.

    A tool is cacheable iff ``metadata.cacheable`` is True AND its TTL class
    is not ``"live-no-cache"``. The ``AtomicToolMetadata`` model_validator
    enforces the consistency of these fields at construction time; this
    helper exists for call sites that prefer a positive boolean over an
    inline expression.
    """
    return metadata.cacheable and metadata.ttl_class != "live-no-cache"


# ---------------------------------------------------------------------------
# read_through — the read-through / write-on-miss entry point.
# ---------------------------------------------------------------------------


class ReadThroughResult:
    """Result of a ``read_through`` call.

    Attributes:
        uri: ``gs://bucket/path`` of the cached artifact, or ``None`` for
            ``live-no-cache`` reads which deliberately do not persist.
        data: the artifact bytes (from the cache hit or freshly fetched).
        hit: True if the response came from the cache, False if fetched.
    """

    __slots__ = ("uri", "data", "hit")

    def __init__(self, uri: str | None, data: bytes, hit: bool) -> None:
        self.uri = uri
        self.data = data
        self.hit = hit

    def __repr__(self) -> str:  # pragma: no cover — diagnostic
        return f"ReadThroughResult(uri={self.uri!r}, hit={self.hit}, bytes={len(self.data)})"


def _gs_uri(bucket: str, path: str) -> str:
    return f"gs://{bucket}/{path}"


def _ttl_to_cache_control(ttl_class: TTLClass) -> str:
    """Object-level Cache-Control header reflecting the TTL class.

    Per FR-DC-3 step 3 the shim attaches a ``Cache-Control`` header to every
    write. We pick object-metadata over bucket-level so per-object visibility
    is preserved (one of the kickoff's surfaced choices).
    """
    seconds = {
        "static-30d": 30 * 24 * 3600,
        "semi-static-7d": 7 * 24 * 3600,
        "dynamic-1h": 3600,
        "live-no-cache": 0,
    }[ttl_class]
    return f"public, max-age={seconds}"


def read_through(
    metadata: AtomicToolMetadata,
    params: dict[str, Any],
    ext: str,
    fetch_fn: Callable[[], bytes],
    *,
    bucket: str | None = None,
    source_id: str | None = None,
    force_refresh: bool = False,
    storage_client: Any | None = None,
    now: datetime | None = None,
) -> ReadThroughResult:
    """Read-through / write-on-miss shim for one atomic-tool fetch.

    Flow per FR-DC-3:

    1. If ``metadata.cacheable`` is False / ``ttl_class == "live-no-cache"``:
       always miss; invoke ``fetch_fn``; do NOT write; return with
       ``uri=None``, ``hit=False``. This honors FR-DC-6.
    2. Otherwise: compute cache key + path. Look up
       ``gs://<bucket>/<cache_path>``. If present, return the URI + bytes.
       Lifecycle policy handles eviction so presence == valid.
    3. On miss (or ``force_refresh=True``): invoke ``fetch_fn()``; write to
       GCS with ``customTime = now`` (FR-DC-3 / job-0031 verified pattern)
       and a ``Cache-Control`` header reflecting the TTL class; return URI +
       bytes.
    4. On ``fetch_fn`` failure: do NOT write a sentinel; re-raise so the
       agent surface (FR-AS-11) can decide whether to retry, clarify, or
       fall back.

    Args:
        metadata: the tool's registered ``AtomicToolMetadata``.
        params: the call parameters (already domain-quantized).
        ext: artifact extension (e.g. ``"tif"``, ``"fgb"``, ``"json"``).
        fetch_fn: a zero-arg callable that produces the fresh bytes. The
            shim is sync because GCS uploads via ``google-cloud-storage`` are
            sync; long-running fetches must be invoked from a context that
            the agent's cancel chain can interrupt.
        bucket: cache bucket name (default ``CACHE_BUCKET``).
        source_id: identifier for the upstream source, defaults to
            ``metadata.source_class``. Pass an override for sub-source detail
            like ``"atcf:IAN"``.
        force_refresh: if True, bypass the cache lookup and always invoke
            ``fetch_fn`` (FR-DC-6 ``cache=false`` per-call opt-in). The
            fresh response is still written through. TENTATIVE per the
            kickoff Open Questions.
        storage_client: optional injected ``google.cloud.storage.Client`` for
            tests. Production callers leave this None; the shim builds the
            default client via ADC.
        now: optional timestamp pin for tests / TTL-bucket determinism.

    Returns:
        ``ReadThroughResult(uri, data, hit)``.
    """
    bucket = bucket or CACHE_BUCKET
    source_id = source_id or (metadata.source_class or metadata.name)

    # FR-DC-6 short-circuit: uncacheable tools never touch the bucket.
    if not is_cacheable(metadata):
        data = fetch_fn()
        logger.info(
            "read_through live-no-cache tool=%s bytes=%d", metadata.name, len(data)
        )
        return ReadThroughResult(uri=None, data=data, hit=False)

    # source_class is guaranteed non-empty for cacheable tools by the
    # AtomicToolMetadata cross-field validator; assert defensively.
    if not metadata.source_class:
        raise ValueError(
            f"cacheable tool {metadata.name!r} has no source_class — model_validator "
            "should have caught this; refusing to write under cache/<None>/."
        )

    key = compute_cache_key(source_id, params, metadata.ttl_class, now=now)
    path = cache_path(metadata.source_class, metadata.ttl_class, key, ext)
    uri = _gs_uri(bucket, path)

    # Lazy import so test environments that don't have google-cloud-storage
    # (or have it stubbed) don't pay the import cost at module load.
    # sprint-14-aws (job-0288c): the object-store cache is BEST-EFFORT. On AWS /
    # run-local there are no GCP credentials, so ``storage.Client()`` raises
    # DefaultCredentialsError (and any read/write may fail on transient I/O).
    # Degrade gracefully: treat a storage failure as a cache miss, fetch fresh
    # from the source API, and return the data UNcached (uri stays the computed
    # gs:// string so callers' ``assert result.uri is not None`` still holds).
    # The Gemini/GCP happy path is unchanged. Full GCS->S3 swap is job-0289.
    try:
        if storage_client is None:
            from google.cloud import storage  # type: ignore[import-not-found]

            storage_client = storage.Client(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod")
            )
        bucket_obj = storage_client.bucket(bucket)
        blob = bucket_obj.blob(path)

        if not force_refresh and blob.exists():
            # Presence == valid per FR-DC-5: the lifecycle policy evicts based
            # on customTime, so anything still present is within its TTL window.
            data = blob.download_as_bytes()
            logger.info(
                "read_through hit tool=%s key=%s bytes=%d",
                metadata.name,
                key,
                len(data),
            )
            return ReadThroughResult(uri=uri, data=data, hit=True)
    except Exception as exc:  # noqa: BLE001 — object store unavailable -> degrade
        logger.warning(
            "read_through cache degraded (object store unavailable) tool=%s: %s; "
            "fetching fresh, uncached",
            metadata.name,
            exc,
        )
        return ReadThroughResult(uri=uri, data=fetch_fn(), hit=False)

    # Miss (or forced refresh). Invoke the fetcher; on failure re-raise so
    # the agent's FR-AS-11 surface decides next steps. Do NOT write a
    # sentinel — a sentinel would poison future reads.
    data = fetch_fn()

    try:
        fetched_at = now or datetime.now(timezone.utc)
        blob.custom_time = fetched_at  # google.cloud.storage requires datetime, not str (OQ-33-CACHE-CUSTOMTIME-TYPE-BUG)
        blob.cache_control = _ttl_to_cache_control(metadata.ttl_class)
        # Best-effort content-type for HTTP-style consumers; the lifecycle
        # policy doesn't care, but downstream readers might.
        content_type = {
            "json": "application/json",
            "tif": "image/tiff",
            "fgb": "application/octet-stream",
            "nc": "application/x-netcdf",
            "grib2": "application/x-grib2",
        }.get(ext.lstrip("."), "application/octet-stream")
        blob.upload_from_string(data, content_type=content_type)
        logger.info(
            "read_through miss-write tool=%s key=%s bytes=%d customTime=%s",
            metadata.name,
            key,
            len(data),
            fetched_at.isoformat(),
        )
    except Exception as exc:  # noqa: BLE001 — write is best-effort
        logger.warning(
            "read_through cache write degraded tool=%s: %s; returning uncached",
            metadata.name,
            exc,
        )
    return ReadThroughResult(uri=uri, data=data, hit=False)
