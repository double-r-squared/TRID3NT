"""First-token latency over N=10 consecutive warm WS calls — p50/p95.

Informational vs NFR-P-1 (2s budget). This mitigates the single-run snapshot
issue from job-0015 OQ-A-2 by measuring p50/p95 over a sustained warm window
rather than one-shot. Runs against the Gemini-stubbed agent so the number
captures TRANSPORT latency (websocket + envelope serialization + event-loop
hops), not LLM time. Real-Gemini measurement is the live_gemini marker test.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time

import pytest

from grace2_contracts.ws import SessionResumePayload

from ._ws_helpers import open_session, send_user_message, serialize


N_WARMUP = 1
N_SAMPLES = 10


async def _one_round_trip(ws, sid: str, text: str) -> float:
    """Time the first non-empty delta after user-message is sent."""
    sent = time.monotonic()
    await send_user_message(ws, sid, text)
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        parsed = json.loads(raw)
        if parsed["type"] == "agent-message-chunk" and parsed["payload"].get("delta"):
            first = time.monotonic()
            break
    # Drain until terminal so the next iteration starts clean.
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        parsed = json.loads(raw)
        if parsed["type"] == "agent-message-chunk" and parsed["payload"].get("done"):
            break
    return (first - sent) * 1000.0


async def test_first_token_latency_p50_p95(
    agent_subprocess: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Capture p50/p95 first-token latency over N=10 warm samples."""
    ws, sid = await open_session(agent_subprocess)
    samples_ms: list[float] = []
    try:
        # Warm-up samples (discarded).
        for _ in range(N_WARMUP):
            await _one_round_trip(ws, sid, "warmup")

        # Measured samples.
        for i in range(N_SAMPLES):
            t_ms = await _one_round_trip(ws, sid, f"ping-{i}")
            samples_ms.append(t_ms)
    finally:
        await ws.close()

    p50 = statistics.median(samples_ms)
    samples_sorted = sorted(samples_ms)
    # p95 with linear interpolation; for N=10 returns ~9.5th-percentile slot.
    idx = max(0, min(len(samples_sorted) - 1, int(round(0.95 * (len(samples_sorted) - 1)))))
    p95 = samples_sorted[idx]
    mean = statistics.mean(samples_ms)

    # Print to stdout so the make-test transcript captures it as the AC5
    # latency-table evidence; failing the test on threshold is NOT the point
    # (NFR-P-1 is informational here per kickoff).
    msg = (
        f"\n[NFR-P-1 first-token (stubbed-Gemini transport)] "
        f"N={len(samples_ms)} p50={p50:.1f}ms p95={p95:.1f}ms mean={mean:.1f}ms "
        f"samples_ms={[round(s,1) for s in samples_ms]}"
    )
    print(msg)
    # Sanity bound: stubbed transport must be far below the 30s cancel budget.
    assert p95 < 5000.0, (
        f"AGENT/transport layer regression — stub p95={p95:.1f}ms exceeds 5s. "
        "Real-Gemini latency is a separate live_gemini measurement."
    )
