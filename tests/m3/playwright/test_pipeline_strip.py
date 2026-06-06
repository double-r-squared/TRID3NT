"""PipelineStrip component tests driving the dev seam + the real cancel chain.

Exit-criterion mapping (sprint-05.md):

* EC3 ("PipelineStrip renders pipeline-state snapshots with
  pending/running/complete/failed/cancelled state colors;
  cancel button emits a cancel envelope reusing the M1 cancel chain;
  visibility predicate is explicit about which envelope feeds which
  condition").

Per testing.md FR-WC-8 / FR-WC-9 + Invariant 8 (Cancellation is first-class):

* For state-color rendering we inject ``pipeline-state`` envelopes through
  the in-page dev seam (``window.__grace2InjectPipelineState``) because the
  agent does not yet emit them in M3 (M4 work — surfaced as Open Question).
* For the cancel emission, ``test_pipeline_strip_sequence_with_framesent_capture``
  (kickoff §1 canonical) drives the exact
  running→complete→running+cancel→cancelled fixture sequence; the outbound
  ``cancel`` frame is captured via ``page.on("websocket")`` +
  ``framesent`` (Playwright in-browser inspection of the wire — no
  background asyncio thread, no event-loop leak into the M1 protocol
  suite); the cross-envelope cancel-button visibility predicate is also
  verified by injecting ``session_state_with_current_pipeline.json`` so
  ``predicate (b)`` alone keeps the button visible.

Chromium only (kickoff §Scope item 2 — visual smoke #1 + #2 cover Firefox).

Failure-naming discipline: every assertion attributes the failing layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Expected state colors per FR-WC-8 + PipelineStrip.tsx STATE_COLOR map.
EXPECTED_COLORS = {
    "pending": "#9ca3af",
    "running": "#3b82f6",
    "complete": "#10b981",
    "failed": "#ef4444",
    "cancelled": "#eab308",
}


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _normalize_color(s: str) -> tuple[int, int, int] | None:
    """Accept ``rgb(r, g, b)`` / ``rgba(r, g, b, a)`` / ``#rrggbb`` and return
    an ``(r, g, b)`` tuple, or None when the string can't be parsed."""
    s = s.strip().lower()
    if s.startswith("#"):
        try:
            return _hex_to_rgb(s)
        except Exception:  # noqa: BLE001
            return None
    if s.startswith("rgb"):
        inner = s[s.index("(") + 1 : s.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:  # noqa: BLE001
            return None
    return None


# ---------------------------------------------------------------------------
# State-color rendering test (5 states × the dev-seam path)
# ---------------------------------------------------------------------------


@pytest.mark.live_web
def test_pipeline_strip_state_colors(
    vite_dev_server: str,
    chromium_browser,
    m3_artifacts_dir: Path,
) -> None:
    """Inject five pipeline-state snapshots — pending / running / complete /
    failed / cancelled — and assert each step chip's dot color matches the
    FR-WC-8 hex literal in PipelineStrip.tsx STATE_COLOR.

    The dot's background-color may be reported as ``rgb(…)`` by the browser
    even when the source CSS uses ``#rrggbb`` — we normalize both sides.
    """
    context = chromium_browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(vite_dev_server, wait_until="load", timeout=60000)
    page.wait_for_function(
        "() => typeof window.__grace2InjectPipelineState === 'function'",
        timeout=10000,
    )

    fixtures = {
        "pending": json.loads((FIXTURES / "pipeline_state_pending.json").read_text()),
        "running": json.loads((FIXTURES / "pipeline_state_running.json").read_text()),
        "complete": json.loads((FIXTURES / "pipeline_state_complete.json").read_text()),
        "failed": json.loads((FIXTURES / "pipeline_state_failed.json").read_text()),
        "cancelled": json.loads((FIXTURES / "pipeline_state_cancelled.json").read_text()),
    }

    # We assert per-state by feeding each fixture and inspecting the dot of
    # the step whose state we want — note the running fixture contains both a
    # complete + a running step so we get two colors for free.
    states_seen: dict[str, tuple[int, int, int]] = {}

    for state_name, fixture in fixtures.items():
        page.evaluate(
            "(p) => window.__grace2InjectPipelineState(p)",
            fixture,
        )
        # Wait for at least one chip to bind the state we expect.
        page.wait_for_function(
            "(want) => Array.from(document.querySelectorAll('[data-testid=\"pipeline-step\"]'))"
            "  .some(el => el.dataset.state === want)",
            arg=state_name,
            timeout=10000,
        )
        chip = page.locator(
            f'[data-testid="pipeline-step"][data-state="{state_name}"]'
        ).first
        dot = chip.locator('[data-testid="pipeline-step-dot"]')
        bg = dot.evaluate("(el) => getComputedStyle(el).backgroundColor")
        rgb = _normalize_color(bg)
        assert rgb is not None, (
            f"layer=web client (PipelineStrip.tsx FR-WC-8): could not parse "
            f"computed background-color {bg!r} for the '{state_name}' chip. "
            f"State color contract must produce parseable rgb()/hex."
        )
        states_seen[state_name] = rgb

    # Compare against the FR-WC-8 spec.
    for state, want_hex in EXPECTED_COLORS.items():
        want = _hex_to_rgb(want_hex)
        got = states_seen.get(state)
        assert got == want, (
            f"layer=web client (PipelineStrip.tsx FR-WC-8): state {state!r} "
            f"chip dot expected color {want_hex} ({want}); observed "
            f"{got!r}. State color contract drifted from "
            f"STATE_COLOR mapping."
        )

    out_png = m3_artifacts_dir / "pipeline-strip-state-colors.png"
    page.screenshot(path=str(out_png), full_page=False)
    context.close()


# (the previous external-WS-server capture path was removed when the
# kickoff §1 canonical sequence test below subsumed it — Playwright's
# in-browser `page.on("websocket") -> framesent` inspection is strictly
# more reliable than spinning up a background asyncio thread, and avoids a
# cross-test asyncio event-loop leak that breaks the M1 protocol suite's
# teardown. The sequence test covers cancel emission AND the cross-envelope
# predicate.)


# ---------------------------------------------------------------------------
# Kickoff §1 canonical sequence test: running → complete → running+cancel →
# cancelled, with the outbound cancel frame captured via the browser-side
# page.on("websocket") + framesent listener (no external WS server), AND an
# explicit cross-envelope cancel-button-visibility predicate check.
# ---------------------------------------------------------------------------


@pytest.mark.live_web
def test_pipeline_strip_sequence_with_framesent_capture(
    vite_dev_server: str,
    chromium_browser,
    m3_artifacts_dir: Path,
) -> None:
    """Drive the exact kickoff-mandated fixture sequence and assert.

    Sequence (kickoff §Scope item 1):

    1. Inject ``pipeline_state_running.json`` -> screenshot.
    2. Inject ``pipeline_state_complete.json`` -> screenshot.
    3. Inject ``pipeline_state_running.json`` again (so a running step is
       present and the cancel button shows under predicate (a)), click the
       cancel button.
    4. Capture the outbound ``cancel`` envelope via ``page.on("websocket")``
       + the ``framesent`` listener (browser-side wire inspection); assert
       envelope shape matches Appendix A.3 (``type == "cancel"`` AND
       ``payload.reason`` is a non-empty string).
    5. Inject ``pipeline_state_cancelled.json`` -> screenshot.
    6. Cross-envelope predicate verification: drop the pipeline-state
       running step (by injecting a complete-only state) so predicate (a)
       is FALSE, then additionally inject
       ``session_state_with_current_pipeline.json`` so predicate (b) is
       TRUE on its own; assert the cancel button remains visible.

    Every assertion message names the failing layer.
    """
    fixtures_dir = FIXTURES
    running = json.loads((fixtures_dir / "pipeline_state_running.json").read_text())
    complete = json.loads((fixtures_dir / "pipeline_state_complete.json").read_text())
    cancelled = json.loads((fixtures_dir / "pipeline_state_cancelled.json").read_text())
    session_with_pipeline = json.loads(
        (fixtures_dir / "session_state_with_current_pipeline.json").read_text()
    )

    context = chromium_browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    # Wire up the browser-side WebSocket inspection BEFORE navigation so we
    # catch every outbound frame from the moment the client opens its WS.
    sent_frames: list[dict] = []
    sent_raw: list[str] = []

    def on_websocket(ws) -> None:  # noqa: ANN001
        def on_framesent(payload) -> None:  # noqa: ANN001
            # Playwright reports the payload as str for text frames; binary
            # for binary. The web client emits JSON text frames per Appendix A.
            if isinstance(payload, (bytes, bytearray)):
                try:
                    text = payload.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    return
            else:
                text = str(payload)
            sent_raw.append(text)
            try:
                obj = json.loads(text)
            except Exception:  # noqa: BLE001
                return
            if isinstance(obj, dict):
                sent_frames.append(obj)

        ws.on("framesent", on_framesent)

    page.on("websocket", on_websocket)

    page.goto(vite_dev_server, wait_until="load", timeout=60000)
    page.wait_for_function(
        "() => typeof window.__grace2InjectPipelineState === 'function'"
        " && typeof window.__grace2InjectSessionState === 'function'",
        timeout=10000,
    )

    # --- (1) inject running -> screenshot ------------------------------- //
    page.evaluate("(p) => window.__grace2InjectPipelineState(p)", running)
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('[data-testid=\"pipeline-step\"]'))"
        "  .some(el => el.dataset.state === 'running')",
        timeout=10000,
    )
    out_running = m3_artifacts_dir / "pipeline-strip-seq-running.png"
    page.screenshot(path=str(out_running), full_page=False)

    # --- (2) inject complete -> screenshot ------------------------------ //
    page.evaluate("(p) => window.__grace2InjectPipelineState(p)", complete)
    page.wait_for_function(
        "() => {"
        "  const steps = document.querySelectorAll('[data-testid=\"pipeline-step\"]');"
        "  return steps.length > 0"
        "    && Array.from(steps).every(el => el.dataset.state === 'complete');"
        "}",
        timeout=10000,
    )
    out_complete = m3_artifacts_dir / "pipeline-strip-seq-complete.png"
    page.screenshot(path=str(out_complete), full_page=False)

    # --- (3) inject running again, then click cancel -------------------- //
    page.evaluate("(p) => window.__grace2InjectPipelineState(p)", running)
    # Cancel button visibility predicate (a): a running step in last
    # pipeline-state envelope. Wait for the cancel button.
    page.wait_for_selector('[data-testid="pipeline-cancel"]', timeout=10000)

    # Snapshot the number of cancel frames seen before the click so we can
    # filter for the click-induced frame deterministically.
    before_cancel_count = sum(
        1 for f in sent_frames if isinstance(f, dict) and f.get("type") == "cancel"
    )

    page.locator('[data-testid="pipeline-cancel"]').click()

    # --- (4) capture outbound cancel via framesent ---------------------- //
    import time as _t

    deadline = _t.monotonic() + 10.0
    cancel_frame: dict | None = None
    while _t.monotonic() < deadline:
        cancels = [
            f
            for f in sent_frames
            if isinstance(f, dict) and f.get("type") == "cancel"
        ]
        if len(cancels) > before_cancel_count:
            cancel_frame = cancels[-1]
            break
        page.wait_for_timeout(150)

    assert cancel_frame is not None, (
        f"layer=web client (PipelineStrip cancel button / ws.ts "
        f"GraceWs.sendCancel) OR Invariant 8 (Cancellation is first-class): "
        f"after clicking the cancel button, no outbound `cancel` envelope was "
        f"observed via page.on(\"websocket\") + framesent within 10s. "
        f"Total outbound frames captured: {len(sent_frames)}; types: "
        f"{[f.get('type') if isinstance(f, dict) else None for f in sent_frames]!r}. "
        f"Raw tail: {sent_raw[-3:]!r}"
    )

    # Appendix A.3 cancel envelope shape: type == "cancel" with
    # payload.reason a non-empty string (per kickoff §1).
    assert cancel_frame.get("type") == "cancel", (
        f"layer=web client (envelope construction): outbound cancel envelope "
        f"missing or wrong 'type' field. Got: {cancel_frame!r}"
    )
    payload = cancel_frame.get("payload")
    assert isinstance(payload, dict), (
        f"layer=web client (envelope construction): cancel envelope "
        f"missing 'payload' dict per Appendix A.3. Got: {cancel_frame!r}"
    )
    reason = payload.get("reason")
    assert isinstance(reason, str) and len(reason) > 0, (
        f"layer=web client (envelope construction / Invariant 8): cancel "
        f"envelope payload.reason must be a non-empty string per Appendix A.3 "
        f"(the M1 cancel chain expects a user-facing reason). Got reason="
        f"{reason!r} in frame {cancel_frame!r}"
    )

    # --- (5) inject cancelled -> screenshot ----------------------------- //
    page.evaluate("(p) => window.__grace2InjectPipelineState(p)", cancelled)
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('[data-testid=\"pipeline-step\"]'))"
        "  .some(el => el.dataset.state === 'cancelled')",
        timeout=10000,
    )
    out_cancelled = m3_artifacts_dir / "pipeline-strip-seq-cancelled.png"
    page.screenshot(path=str(out_cancelled), full_page=False)

    # --- (6) cross-envelope predicate (b) verification ------------------- //
    # Inject a pipeline-state with all `complete` steps so predicate (a)
    # is FALSE (no running step), then inject the
    # session_state_with_current_pipeline.json so predicate (b) is TRUE.
    # The cancel button should remain visible by predicate (b) alone.
    page.evaluate("(p) => window.__grace2InjectPipelineState(p)", complete)
    page.wait_for_function(
        "() => {"
        "  const steps = document.querySelectorAll('[data-testid=\"pipeline-step\"]');"
        "  return steps.length > 0"
        "    && Array.from(steps).every(el => el.dataset.state === 'complete');"
        "}",
        timeout=10000,
    )
    page.evaluate(
        "(p) => window.__grace2InjectSessionState(p)", session_with_pipeline
    )

    # Per kickoff: predicate (b) alone (session-state.current_pipeline
    # non-null) must keep the cancel button visible. Wait briefly for the
    # reducer to dispatch, then assert.
    page.wait_for_selector(
        '[data-testid="pipeline-cancel"]', timeout=5000
    )
    cancel_visible = page.locator('[data-testid="pipeline-cancel"]').is_visible()
    assert cancel_visible, (
        f"layer=web client (PipelineStrip cross-envelope visibility "
        f"predicate, FR-WC-9): with pipeline-state carrying only `complete` "
        f"steps (predicate (a) FALSE) and session-state.current_pipeline "
        f"non-null (predicate (b) TRUE), the cancel button must remain "
        f"visible — that's the cross-envelope union the kickoff and "
        f"job-0026 wired. Observed cancel button NOT visible."
    )

    out_predicate_b = m3_artifacts_dir / "pipeline-strip-seq-predicate-b-only.png"
    page.screenshot(path=str(out_predicate_b), full_page=False)

    context.close()
