"""Gemini CachedContent helper (Wave 4.10 job-B6).

Implements the Wave 4.10 CachedContent Option A architecture locked in
``project_wave_4_10_research_findings.md`` § Architecture decisions:

- The FULL tool catalog (``TOOL_REGISTRY`` → ``FunctionDeclaration`` list)
  + the system instruction are cached via ``client.caches.create`` ONCE per
  session at session start. Per-turn ``generate_content_stream`` calls then
  set ``GenerateContentConfig.cached_content=<cache.name>`` and OMIT
  ``tools[]`` / ``tool_config`` (Vertex 400s when ``tool_config`` is sent
  alongside ``cached_content`` — that combination is the original
  pre-dispatch blocker the research surfaced).

- The per-turn allowed-set is enforced server-side in our code (see
  ``categories.validate_function_call``); the cache itself carries the
  complete catalog so Gemini sees every tool at all times. The
  ``OutOfAllowedSetError`` path in ``server.py`` routes mis-targeted
  function_calls back to Gemini as structured error envelopes.

Pricing math (locked, Vertex Gemini 2.5 Pro):
    Uncached input:   $1.25 / M tokens
    Cached input:     $0.125 / M tokens     (90% discount)
    Storage:          $4.50 / M-tok-hour
    Cache minimum:    2048 tokens (Gemini 2.5)

The 11K-token catalog comfortably exceeds the cache minimum; the break-even
threshold is ~2.5 turns/hour. The agent reaches break-even after a couple
function calls in a single conversation, so the cache is net-positive for
every multi-turn session.

This module is intentionally narrow: ONE public coroutine
``get_or_create_cache(client, session_id, ...) -> str | None``. The return
value is the cache name (e.g. ``"projects/.../cachedContents/.../"``) that
the multi-turn driver passes into
``stream_events_with_contents(cached_content_name=...)``. On any creation
failure (catalog below the 2048-token minimum, transient Vertex error,
SDK signature mismatch) the helper returns ``None`` and the caller falls
back to the non-cached path — the agent stays operational.

Implementation notes:

- A process-level dict ``_SESSION_CACHE`` maps ``session_id`` → ``_CacheRef``
  (name + creation_time + expires_at). The map is in-memory only; it does
  NOT persist across process restarts. A WebSocket reconnect under the same
  session_id reuses the cache as long as it has not expired.

- TTL defaults to 1 hour (Gemini default). The helper refreshes the cache
  (creates a fresh one + replaces the map entry) when the expiry is within
  a 60-second cushion of "now".

- The helper is process-thread-safe via an ``asyncio.Lock`` keyed on
  ``session_id``. Concurrent ``get_or_create_cache`` calls for the same
  session race exactly once on the actual Vertex creation; later callers
  observe the cached name.

- The wrapped error path NEVER raises. Telemetry is logged at WARNING
  (creation failure) / INFO (cache hit reuse). The caller decides the
  fallback shape.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from google import genai
from google.genai import types as genai_types

from .adapter import GEMINI_DEFAULT_MODEL, SYSTEM_PROMPT, build_tool_declarations
from .tools import TOOL_REGISTRY

logger = logging.getLogger("grace2_agent.gemini_cache")


# Default TTL for the cache (Gemini service default; we re-declare here so
# the renewal cushion has something explicit to compare against).
DEFAULT_CACHE_TTL_SECONDS = 3600  # 1 hour
# Safety cushion: if the cache will expire within this many seconds, refresh
# eagerly so a long-running turn does not lose the cache mid-stream.
_REFRESH_CUSHION_SECONDS = 60
# Minimum cache size for Gemini 2.5 Pro (per Vertex docs). The agent's
# 11K-token catalog clears this comfortably; the constant is here so the
# fallback path can log a clear reason if a future catalog shrinks below it.
_GEMINI_25_MIN_CACHE_TOKENS = 2048


@dataclass
class _CacheRef:
    """In-process bookkeeping for a single session's cache entry."""

    name: str
    created_at: datetime
    expires_at: datetime


# Process-level session → cache map.  Lives only in memory; a process restart
# (or a fresh local-dev session) starts with an empty map and the caller
# pays one creation per session_id.
_SESSION_CACHE: dict[str, _CacheRef] = {}
# Per-session locks so concurrent get_or_create_cache calls for the same
# session_id collapse to ONE Vertex create round-trip.
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    """Return (creating if absent) the per-session lock."""
    lock = _SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[session_id] = lock
    return lock


def _now_utc() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Standalone (not imported from ``grace2_contracts``) so this module stays
    importable from test code that does not pull the wire-contracts package.
    """
    return datetime.now(timezone.utc)


def _ttl_seconds_env() -> int:
    """Resolve cache TTL from env, defaulting to ``DEFAULT_CACHE_TTL_SECONDS``."""
    raw = os.environ.get("GRACE2_GEMINI_CACHE_TTL_S")
    if raw is None:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        v = int(raw)
        if v <= 0:
            raise ValueError
        return v
    except ValueError:
        logger.warning(
            "GRACE2_GEMINI_CACHE_TTL_S=%r is not a positive integer; "
            "using default %d",
            raw,
            DEFAULT_CACHE_TTL_SECONDS,
        )
        return DEFAULT_CACHE_TTL_SECONDS


def _cache_disabled_env() -> bool:
    """If ``GRACE2_GEMINI_CACHE_DISABLED=1`` is set, the helper short-circuits.

    Provides a kill-switch so an operator can fall back to the non-cached
    path without code changes (e.g. for an SDK regression). Tests use it to
    pin the non-cached code path deterministically.
    """
    raw = os.environ.get("GRACE2_GEMINI_CACHE_DISABLED", "")
    return raw.lower() in ("1", "true", "yes", "on")


def _is_expired(ref: _CacheRef) -> bool:
    """Return True when the cache is within the refresh cushion of expiry."""
    return ref.expires_at <= _now_utc() + timedelta(seconds=_REFRESH_CUSHION_SECONDS)


async def _create_cache(
    client: genai.Client,
    model: str,
    declarations_factory: Callable[[], list[genai_types.FunctionDeclaration]]
    | None = None,
    system_prompt: str | None = None,
    ttl_seconds: int | None = None,
) -> _CacheRef | None:
    """Wrap ``client.caches.create`` with structured error handling.

    Returns ``None`` (caller falls back to non-cached path) when:
    - ``caches.create`` raises (SDK regression, Vertex quota, model mismatch),
    - the underlying catalog is below the Gemini cache minimum size, or
    - any other transient failure surfaces during the create round-trip.

    Never raises; all failure modes log at WARNING.
    """
    if declarations_factory is None:
        declarations_factory = lambda: build_tool_declarations(TOOL_REGISTRY)
    if system_prompt is None:
        system_prompt = SYSTEM_PROMPT
    if ttl_seconds is None:
        ttl_seconds = _ttl_seconds_env()

    try:
        decls = declarations_factory()
    except Exception:  # noqa: BLE001 — declaration build failure
        logger.warning(
            "gemini-cache: build_tool_declarations raised; "
            "falling back to non-cached path",
            exc_info=True,
        )
        return None

    if not decls:
        logger.warning(
            "gemini-cache: TOOL_REGISTRY produced 0 declarations; "
            "falling back to non-cached path"
        )
        return None

    gem_tools = [genai_types.Tool(function_declarations=decls)]
    cfg = genai_types.CreateCachedContentConfig(
        ttl=f"{ttl_seconds}s",
        system_instruction=system_prompt,
        tools=gem_tools,
        display_name="grace2-tool-catalog",
    )

    try:
        loop = asyncio.get_running_loop()
        # client.caches.create is a sync method; off-load to executor so we
        # do not block the event loop on the Vertex round-trip.
        cached = await loop.run_in_executor(
            None,
            lambda: client.caches.create(model=model, config=cfg),
        )
    except Exception as exc:  # noqa: BLE001 — Vertex / SDK / quota issues
        logger.warning(
            "gemini-cache: caches.create failed model=%s err=%s; "
            "falling back to non-cached path",
            model,
            exc,
        )
        return None

    name = getattr(cached, "name", None)
    if not isinstance(name, str) or not name:
        logger.warning(
            "gemini-cache: caches.create returned no .name; "
            "falling back to non-cached path"
        )
        return None

    # Pull the server-side expiry if exposed; else fall back to local clock.
    expires = getattr(cached, "expire_time", None)
    if not isinstance(expires, datetime):
        expires = _now_utc() + timedelta(seconds=ttl_seconds)
    # Normalise tz-naive datetimes to UTC for safe comparisons.
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    ref = _CacheRef(
        name=name,
        created_at=_now_utc(),
        expires_at=expires,
    )
    # Telemetry: count cached tokens when the SDK surfaces them.
    usage = getattr(cached, "usage_metadata", None)
    total_tokens = getattr(usage, "total_token_count", None) if usage else None
    logger.info(
        "gemini-cache: created session-scoped cache name=%s "
        "tokens=%s ttl_s=%d expires=%s",
        name,
        total_tokens,
        ttl_seconds,
        expires.isoformat(),
    )
    return ref


async def get_or_create_cache(
    client: genai.Client,
    session_id: str,
    *,
    model: str | None = None,
    declarations_factory: Callable[[], list[genai_types.FunctionDeclaration]]
    | None = None,
    system_prompt: str | None = None,
    ttl_seconds: int | None = None,
) -> str | None:
    """Return the Vertex cache name for ``session_id`` (creating if needed).

    Returns ``None`` when the cache cannot be created (kill-switch set,
    Vertex error, catalog too small, SDK regression, etc.) — the caller
    is expected to fall back to the non-cached ``stream_events_with_contents``
    path. The agent stays operational either way.

    Concurrency: the per-session ``asyncio.Lock`` collapses concurrent
    ``get_or_create_cache`` calls for the same session into a single Vertex
    create round-trip. Subsequent callers observe the cached ref.

    Args:
        client: built ``google.genai.Client`` (Vertex-mode).
        session_id: the WebSocket session id; used as the cache-key.
        model: optional model override (defaults to ``GEMINI_DEFAULT_MODEL``
            or the ``GRACE2_GEMINI_MODEL`` env override).
        declarations_factory: optional override for the tool-declaration
            builder; ``None`` uses ``build_tool_declarations(TOOL_REGISTRY)``.
            Injectable so tests can exercise the create path without the
            full ~57-tool registry.
        system_prompt: optional system-instruction override.
        ttl_seconds: optional TTL override (default 1 hour or
            ``GRACE2_GEMINI_CACHE_TTL_S`` env).
    """
    if _cache_disabled_env():
        logger.debug(
            "gemini-cache: disabled via GRACE2_GEMINI_CACHE_DISABLED=1 "
            "for session=%s",
            session_id,
        )
        return None

    if model is None:
        model = os.environ.get("GRACE2_GEMINI_MODEL", GEMINI_DEFAULT_MODEL)

    lock = _session_lock(session_id)
    async with lock:
        ref = _SESSION_CACHE.get(session_id)
        if ref is not None and not _is_expired(ref):
            logger.debug(
                "gemini-cache: hit session=%s name=%s expires_in_s=%.1f",
                session_id,
                ref.name,
                (ref.expires_at - _now_utc()).total_seconds(),
            )
            return ref.name
        if ref is not None:
            logger.info(
                "gemini-cache: refresh (expired/near-expiry) session=%s "
                "old_name=%s",
                session_id,
                ref.name,
            )
        new_ref = await _create_cache(
            client,
            model=model,
            declarations_factory=declarations_factory,
            system_prompt=system_prompt,
            ttl_seconds=ttl_seconds,
        )
        if new_ref is None:
            # Leave any stale ref in place to be retried on the next call.
            # The caller falls back to the non-cached path for this turn.
            return None
        _SESSION_CACHE[session_id] = new_ref
        return new_ref.name


def clear_session_cache(session_id: str) -> None:
    """Drop the cache entry + lock for ``session_id``.

    Used by tests and (eventually) the WebSocket disconnect handler to
    free per-session bookkeeping. Idempotent; missing keys are a no-op.
    """
    _SESSION_CACHE.pop(session_id, None)
    _SESSION_LOCKS.pop(session_id, None)


def _reset_for_tests() -> None:
    """Clear the process-level map + locks. Test-only.

    The cache map is module-level singleton state; tests that exercise the
    create path need a clean slate. NOT exported in ``__all__`` so it
    stays an implementation detail.
    """
    _SESSION_CACHE.clear()
    _SESSION_LOCKS.clear()


__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "get_or_create_cache",
    "clear_session_cache",
]
