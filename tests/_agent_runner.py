"""Test subprocess entry point for the agent service.

This shim is the dependency-injection seam between the test harness and the
agent under test. It honors ``GRACE2_TEST_STUB_GEMINI=1`` by monkey-patching
``grace2_agent.adapter.stream_reply`` to yield a deterministic delta stream
*before* the WebSocket server boots. With the env var unset, the real Vertex
Gemini adapter is used (``-m live_gemini`` tests).

This file lives under ``tests/`` (testing-specialist file ownership). It does
not modify any ``services/agent/`` code. The stub boundary is the **only**
permitted mock per job-0017 kickoff; every other layer (WebSocket transport,
envelope validation through grace2_contracts, asyncio cancellation, MCP) runs
real against the live process.
"""

from __future__ import annotations

import asyncio
import os
import sys


# Deterministic deltas: 12 short tokens with realistic word boundaries. Total
# length and shape stays stable across runs so latency p50/p95 measures
# transport, not LLM nondeterminism.
_STUB_DELTAS = [
    "Stub", " reply", ":", " SFINCS",
    " is", " a", " reduced",
    "-complexity", " hydrodynamic", " solver",
    ".", "",
]


async def _stub_stream_reply(client, model, user_text):
    """Deterministic replacement for ``grace2_agent.adapter.stream_reply``.

    Yields the canned deltas with a short sleep between them so cancel paths
    have a real window to interrupt. The ``client`` and ``model`` arguments
    are accepted for signature parity and otherwise ignored.
    """
    for delta in _STUB_DELTAS:
        if not delta:
            continue
        # Short pause so cancel can land mid-stream deterministically.
        await asyncio.sleep(0.05)
        yield delta


def _stub_build_client(settings):
    """Stub client so we never touch Vertex AI in stub mode."""

    class _DummyClient:
        pass

    return _DummyClient()


def _install_stub() -> None:
    import grace2_agent.adapter as _adapter
    import grace2_agent.server as _server

    _adapter.stream_reply = _stub_stream_reply  # type: ignore[assignment]
    _adapter.build_client = _stub_build_client  # type: ignore[assignment]
    # server.py did a `from .adapter import ... stream_reply` (binding at
    # import time), so we must rebind the server-local reference too.
    _server.stream_reply = _stub_stream_reply  # type: ignore[assignment]
    _server.build_client = _stub_build_client  # type: ignore[assignment]


def main() -> int:
    if os.environ.get("GRACE2_TEST_STUB_GEMINI") == "1":
        _install_stub()
    from grace2_agent.main import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
