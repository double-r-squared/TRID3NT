"""Unit tests for ``grace2_agent.gemini_cache`` (Wave 4.10 job-B6).

Coverage:
    1. ``test_create_cache_success`` — first call invokes ``client.caches.create``
       once, stashes the cache ref, and returns the resolved name.
    2. ``test_cache_hit_skips_create`` — second call for the same session
       returns the cached name without re-creating.
    3. ``test_concurrent_calls_collapse`` — concurrent first calls for the
       same session_id collapse to ONE Vertex create round-trip.
    4. ``test_expired_cache_refreshes`` — when the cached ref has expired the
       helper creates a fresh one.
    5. ``test_create_failure_returns_none`` — a Vertex / SDK error during
       create returns ``None`` (caller falls back to the non-cached path),
       and the helper never raises.
    6. ``test_disabled_env_short_circuits`` — ``GRACE2_GEMINI_CACHE_DISABLED=1``
       returns ``None`` immediately; no ``caches.create`` is attempted.
    7. ``test_empty_catalog_returns_none`` — when the declarations factory
       yields an empty list (catalog too small for the cache minimum), the
       helper returns ``None``.

These tests fully mock the google-genai Client; no Vertex calls are issued.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from grace2_agent import gemini_cache
from grace2_agent.gemini_cache import (
    DEFAULT_CACHE_TTL_SECONDS,
    clear_session_cache,
    get_or_create_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache_state():
    """Reset the module-level session map between every test."""
    gemini_cache._reset_for_tests()
    yield
    gemini_cache._reset_for_tests()
    # Make sure no test env-var leaks across runs.
    for k in (
        "GRACE2_GEMINI_CACHE_DISABLED",
        "GRACE2_GEMINI_CACHE_TTL_S",
    ):
        os.environ.pop(k, None)


def _mock_client_create(
    *, name: str = "projects/p/locations/us-central1/cachedContents/test-cache",
    expire_time: datetime | None = None,
    raises: BaseException | None = None,
) -> MagicMock:
    """Build a MagicMock google-genai Client whose ``caches.create`` either
    returns a stub ``CachedContent`` or raises ``raises``."""
    client = MagicMock()
    if raises is not None:
        client.caches.create.side_effect = raises
    else:
        cached = MagicMock()
        cached.name = name
        cached.expire_time = expire_time
        cached.usage_metadata = MagicMock(total_token_count=11000)
        client.caches.create.return_value = cached
    return client


def _decls_factory_one_tool():
    """Build a single dummy FunctionDeclaration so the catalog is non-empty."""
    from google.genai import types as gt
    return [gt.FunctionDeclaration(name="dummy", description="d")]


def _decls_factory_empty():
    return []


@pytest.mark.asyncio
async def test_create_cache_success() -> None:
    client = _mock_client_create()
    name = await get_or_create_cache(
        client,
        "session-A",
        declarations_factory=_decls_factory_one_tool,
        system_prompt="prompt",
        ttl_seconds=600,
    )
    assert name == "projects/p/locations/us-central1/cachedContents/test-cache"
    assert client.caches.create.call_count == 1
    # The session map should now carry the entry.
    assert "session-A" in gemini_cache._SESSION_CACHE


@pytest.mark.asyncio
async def test_cache_hit_skips_create() -> None:
    client = _mock_client_create()
    n1 = await get_or_create_cache(
        client, "session-B", declarations_factory=_decls_factory_one_tool
    )
    n2 = await get_or_create_cache(
        client, "session-B", declarations_factory=_decls_factory_one_tool
    )
    assert n1 == n2
    # Only ONE Vertex create round-trip.
    assert client.caches.create.call_count == 1


@pytest.mark.asyncio
async def test_concurrent_calls_collapse() -> None:
    """Concurrent first calls for the same session collapse via the lock."""
    client = _mock_client_create()
    # Make caches.create slow so the second waiter actually races into the lock.
    real_create = client.caches.create
    import time

    def _slow_create(*args, **kwargs):
        time.sleep(0.05)
        return real_create.return_value
    client.caches.create.side_effect = _slow_create

    sid = "session-C"
    a, b, c = await asyncio.gather(
        get_or_create_cache(
            client, sid, declarations_factory=_decls_factory_one_tool
        ),
        get_or_create_cache(
            client, sid, declarations_factory=_decls_factory_one_tool
        ),
        get_or_create_cache(
            client, sid, declarations_factory=_decls_factory_one_tool
        ),
    )
    assert a == b == c
    # Exactly one Vertex create round-trip across all three callers.
    assert client.caches.create.call_count == 1


@pytest.mark.asyncio
async def test_expired_cache_refreshes() -> None:
    """A cached ref past its refresh-cushion expiry triggers a recreate."""
    client = _mock_client_create()
    n1 = await get_or_create_cache(
        client, "session-D", declarations_factory=_decls_factory_one_tool
    )
    assert n1 is not None
    # Forcibly age the ref's expiry to just-past now.
    ref = gemini_cache._SESSION_CACHE["session-D"]
    ref.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    # Next call should recreate.
    client.caches.create.reset_mock()
    new_cached = MagicMock()
    new_cached.name = "projects/p/locations/us-central1/cachedContents/refreshed"
    new_cached.expire_time = None
    new_cached.usage_metadata = MagicMock(total_token_count=11000)
    client.caches.create.return_value = new_cached
    n2 = await get_or_create_cache(
        client, "session-D", declarations_factory=_decls_factory_one_tool
    )
    assert n2 == "projects/p/locations/us-central1/cachedContents/refreshed"
    assert client.caches.create.call_count == 1


@pytest.mark.asyncio
async def test_create_failure_returns_none() -> None:
    """A raised exception from caches.create becomes a clean None return."""
    client = _mock_client_create(raises=RuntimeError("vertex 500"))
    name = await get_or_create_cache(
        client,
        "session-E",
        declarations_factory=_decls_factory_one_tool,
    )
    assert name is None
    # The helper must NOT have cached anything on failure.
    assert "session-E" not in gemini_cache._SESSION_CACHE


@pytest.mark.asyncio
async def test_disabled_env_short_circuits() -> None:
    """GRACE2_GEMINI_CACHE_DISABLED=1 short-circuits before any client call."""
    client = _mock_client_create()
    with patch.dict(os.environ, {"GRACE2_GEMINI_CACHE_DISABLED": "1"}):
        name = await get_or_create_cache(
            client,
            "session-F",
            declarations_factory=_decls_factory_one_tool,
        )
    assert name is None
    assert client.caches.create.call_count == 0


@pytest.mark.asyncio
async def test_empty_catalog_returns_none() -> None:
    """An empty declarations factory yields a None return (no create)."""
    client = _mock_client_create()
    name = await get_or_create_cache(
        client,
        "session-G",
        declarations_factory=_decls_factory_empty,
    )
    assert name is None
    assert client.caches.create.call_count == 0


def test_clear_session_cache_idempotent() -> None:
    """Clearing an absent session does not raise; clears existing entries."""
    clear_session_cache("does-not-exist")  # no-op
    gemini_cache._SESSION_CACHE["session-H"] = gemini_cache._CacheRef(
        name="x",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    clear_session_cache("session-H")
    assert "session-H" not in gemini_cache._SESSION_CACHE


def test_ttl_env_override_invalid_falls_back_to_default() -> None:
    with patch.dict(os.environ, {"GRACE2_GEMINI_CACHE_TTL_S": "not-an-int"}):
        assert gemini_cache._ttl_seconds_env() == DEFAULT_CACHE_TTL_SECONDS


def test_ttl_env_override_zero_falls_back_to_default() -> None:
    with patch.dict(os.environ, {"GRACE2_GEMINI_CACHE_TTL_S": "0"}):
        assert gemini_cache._ttl_seconds_env() == DEFAULT_CACHE_TTL_SECONDS


def test_ttl_env_override_valid_int() -> None:
    with patch.dict(os.environ, {"GRACE2_GEMINI_CACHE_TTL_S": "120"}):
        assert gemini_cache._ttl_seconds_env() == 120
