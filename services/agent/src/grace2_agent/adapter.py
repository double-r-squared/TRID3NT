"""Gemini-only containment layer (Domain Discipline: agent.md).

Every Gemini- / google-genai-specific construct lives here. The server.py and
mcp.py modules call into this module with Gemini-naive shapes (strings,
async-iterators of strings). This is **containment, not abstraction** — no
``LLMProvider`` protocol, no provider branches, no Bedrock/Strands shapes.
FR-AS-1: Gemini-only. The deferred multi-provider future (§5) is not
foreclosed cheaply because the seam exists, but no abstraction is paid for now.

Model selection (job-0015):
  GRACE2_GEMINI_MODEL env override, defaulting to ``GEMINI_DEFAULT_MODEL``
  below. As of 2026-06-05 Gemini 3 (``gemini-3-pro*``) is not yet GA on Vertex
  for this project (verified 404 from generate_content); ``gemini-2.5-pro`` is
  the current best stable. When Gemini 3 lands on Vertex this constant — and
  the env override path — flips with no other code change.

Auth: ADC via ``GOOGLE_GENAI_USE_VERTEXAI=True`` + ``GOOGLE_CLOUD_PROJECT`` +
``GOOGLE_CLOUD_LOCATION``. No API key path. (job-0014 substrate.)
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

from google import genai
from google.genai import types as genai_types

# Default Gemini model id. See module docstring for the Gemini-3-on-Vertex
# availability note. Override at runtime via ``GRACE2_GEMINI_MODEL``.
GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"


@dataclass(frozen=True)
class GeminiSettings:
    """Resolved Gemini configuration (env-derived; no implicit fallbacks)."""

    model: str
    project: str
    location: str
    use_vertex: bool


def load_settings() -> GeminiSettings:
    """Resolve Gemini settings from the environment.

    Required env (job-0014 substrate):
    - ``GOOGLE_GENAI_USE_VERTEXAI=True``
    - ``GOOGLE_CLOUD_PROJECT`` (default: ``grace-2-hazard-prod``)
    - ``GOOGLE_CLOUD_LOCATION`` (default: ``us-central1``)

    Optional:
    - ``GRACE2_GEMINI_MODEL`` (default: ``GEMINI_DEFAULT_MODEL``)
    """
    return GeminiSettings(
        model=os.environ.get("GRACE2_GEMINI_MODEL", GEMINI_DEFAULT_MODEL),
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        use_vertex=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "True").lower()
        in ("true", "1", "yes"),
    )


def build_client(settings: GeminiSettings) -> genai.Client:
    """Build a google-genai Client configured for Vertex AI.

    Containment: nothing outside this module imports ``genai`` or
    ``genai_types``. The server consumes the async-iterator of deltas this
    module returns.
    """
    if not settings.use_vertex:
        # Pre-MVP: Vertex-only path. Surface the misconfiguration loudly
        # rather than silently falling back to API-key mode.
        raise RuntimeError(
            "GRACE2 agent runs Vertex-only (FR-AS-1). "
            "Set GOOGLE_GENAI_USE_VERTEXAI=True."
        )
    return genai.Client(
        vertexai=True, project=settings.project, location=settings.location
    )


async def stream_reply(
    client: genai.Client, model: str, user_text: str
) -> AsyncIterator[str]:
    """Stream Gemini's reply as a sequence of delta strings.

    The async iterator yields delta strings as Gemini emits them. The caller
    wraps each delta in an ``agent-message-chunk`` envelope. When the underlying
    stream ends, the iterator stops; the caller emits the terminal ``done=True``
    frame.

    Cancellation: ``asyncio.CancelledError`` is the cancel path. The caller
    cancels this task on a WebSocket ``cancel`` message; we propagate the
    cancellation up so the server can emit ``pipeline-state(cancelled)`` within
    NFR-R-3's 30s window (Invariant 8).
    """
    # google-genai's streaming API is sync-iterable; run it on a thread so the
    # event loop stays responsive and cancellation propagates cleanly.
    loop = asyncio.get_running_loop()

    def _open_stream():
        return client.models.generate_content_stream(
            model=model,
            contents=user_text,
            config=genai_types.GenerateContentConfig(
                # Defaults are fine for hello-world. Tool config / function
                # declarations land when the ADK tool registry comes online.
                temperature=0.7,
            ),
        )

    queue: asyncio.Queue[str | None | BaseException] = asyncio.Queue()

    def _producer() -> None:
        try:
            for chunk in _open_stream():
                # chunk.text is the delta in google-genai 2.x streaming.
                delta = getattr(chunk, "text", None)
                if delta:
                    loop.call_soon_threadsafe(queue.put_nowait, delta)
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except BaseException as exc:  # noqa: BLE001 — surface any error to caller
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    producer_task = loop.run_in_executor(None, _producer)

    try:
        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    except asyncio.CancelledError:
        # Best-effort: producer thread may not stop instantly (google-genai
        # streaming is sync); the asyncio task ownership ensures the server
        # never blocks on it past cancel, and the future is discarded.
        producer_task.cancel()
        raise


__all__ = [
    "GEMINI_DEFAULT_MODEL",
    "GeminiSettings",
    "build_client",
    "load_settings",
    "stream_reply",
]
