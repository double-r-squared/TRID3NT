"""Live Vertex smoke test for Gemini CachedContent integration (job-B6).

This test is the empirical resolution of the original
``project_wave_4_10_research_findings.md`` blocker #1
("Gemini CachedContent cache-preservation is unverified") — it
exercises the full ``gemini_cache.get_or_create_cache`` →
``stream_events_with_contents(cached_content_name=...)`` round-trip
against the real Vertex Gemini endpoint and verifies that
``usage_metadata.cached_content_token_count`` is positive AND stable
across multiple sequential turns.

Env-gated to keep CI fast and offline. Set ``GRACE2_TEST_LIVE_GEMINI_CACHE=1``
(and the standard Vertex env: ``GOOGLE_GENAI_USE_VERTEXAI=True``,
``GOOGLE_CLOUD_PROJECT``, ``GOOGLE_CLOUD_LOCATION``, plus ADC) to run.

Why "stable, not invalidated"?  Vertex caches are immutable once written —
sending an identical follow-up request must reuse the same cache. If the
``cached_content_token_count`` falls to 0 on the second call, that
means the cache was silently invalidated (a regression) and the 90% discount
projections fall apart. The test fails LOUD on that path.

What this test does NOT do:
    - It does not exercise the full ``server.py`` multi-turn dispatch loop
      (that is the job of the live-evidence script).
    - It does not assert specific token counts (those depend on the
      registered tool catalog size which varies sprint-over-sprint); it only
      asserts ``> 0`` AND ``stable``.
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("GRACE2_TEST_LIVE_GEMINI_CACHE", "0") != "1",
    reason=(
        "Live Vertex Gemini cache smoke test — gated on "
        "GRACE2_TEST_LIVE_GEMINI_CACHE=1. Set it (and Vertex env / ADC) "
        "to verify the 90% discount empirically."
    ),
)


@pytest.mark.asyncio
async def test_cached_content_token_count_positive_and_stable() -> None:
    """Three sequential requests reuse the same cache; cached_token_count > 0 stable."""
    from grace2_agent.adapter import (
        UsageMetadataEvent,
        build_client,
        build_contents_from_history,
        load_settings,
        stream_events_with_contents,
    )
    from grace2_agent.gemini_cache import (
        clear_session_cache,
        get_or_create_cache,
    )

    settings = load_settings()
    client = build_client(settings)
    session_id = "smoke-session-cache-01"
    # Start clean.
    clear_session_cache(session_id)
    cache_name = await get_or_create_cache(client, session_id)
    assert cache_name is not None, (
        "get_or_create_cache returned None — cache creation failed; cannot "
        "verify discount empirically. Check Vertex quota, model id, and "
        "ADC."
    )
    cached_counts: list[int] = []

    for turn in range(3):
        contents = build_contents_from_history(
            f"Turn {turn}: greet me and call no tools. Reply in one sentence.",
            chat_history=None,
        )
        seen_usage: UsageMetadataEvent | None = None
        async for event in stream_events_with_contents(
            client,
            settings.model,
            contents,
            cached_content_name=cache_name,
        ):
            if isinstance(event, UsageMetadataEvent):
                seen_usage = event
        assert seen_usage is not None, (
            f"Turn {turn}: no UsageMetadataEvent surfaced — SDK regression "
            "or empty response."
        )
        cached = seen_usage.cached_content_token_count or 0
        assert cached > 0, (
            f"Turn {turn}: cached_content_token_count={cached} (expected > 0). "
            "The cache was either not used or silently invalidated; the 90% "
            "discount is NOT landing. This is the original Wave 4.10 blocker."
        )
        cached_counts.append(cached)

    # All three turns saw the same cached-token count (the cache is stable
    # — Vertex did not silently invalidate it between calls).
    assert len(set(cached_counts)) == 1, (
        f"cached_content_token_count varied across turns: {cached_counts}. "
        "Vertex is rotating / invalidating the cache mid-session — the 90% "
        "discount cannot be relied upon at this stability."
    )
