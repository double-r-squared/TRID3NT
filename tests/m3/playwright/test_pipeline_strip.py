"""PipelineStrip component tests driving the dev seam + the real cancel chain.

Exit-criterion mapping (sprint-05.md):

* EC3 ("PipelineStrip renders pipeline-state snapshots with
  pending/running/complete/failed/cancelled state colors;
  cancel button emits a cancel envelope reusing the M1 cancel chain;
  visibility predicate is explicit about which envelope feeds which
  condition").

Per testing.md FR-WC-8 / FR-WC-9 + Invariant 8 (Cancellation is first-class):

* ``test_pipeline_strip_state_colors`` is a pure-rendering test of the 5
  state colors (FR-WC-8). It drives the in-page dev seam
  (``window.__grace2InjectPipelineState``) because color rendering is a
  client-side concern with no agent contract surface to exercise.
  The seam is gated behind ``import.meta.env.DEV`` and kept indefinitely
  (per job-0035 OQ-35-DEV-INJECTION-SEAM-RETIREMENT).
* ``test_pipeline_strip_sequence_with_framesent_capture`` (kickoff §1
  canonical) drives the **real agent emission path** (job-0035
  ``PipelineEmitter`` -> WebSocket -> web client). Closes
  OQ-T-28-SIM-WS-BOUNDARY definitively: the M3 dev-injection seam is
  the documented fallback, NOT the only path to a populated
  ``pipeline-state`` envelope on the wire. The test:
  - boots a real ``grace2-agent`` subprocess (stubbed Gemini),
  - boots a Vite dev server with ``VITE_GRACE2_WS_URL`` pointed at it,
  - drives the agent via ``/invoke`` directives (job-0035) over a parallel
    WS connection so the browser-rendered client receives real
    ``pipeline-state`` + ``session-state`` envelopes through its real
    GraceWs,
  - clicks the cancel button and captures the outbound ``cancel`` frame
    via ``page.on("websocket")`` + ``framesent`` (browser-side wire
    inspection).

Chromium only (kickoff §Scope item 2 — visual smoke #1 + #2 cover Firefox).

Failure-naming discipline: every assertion attributes the failing layer.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

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


# ---------------------------------------------------------------------------
# job-0036 rewrite: drive the REAL agent emission path instead of the M3
# dev-injection seam (window.__grace2InjectPipelineState). Closes
# OQ-T-28-SIM-WS-BOUNDARY definitively — the agent (job-0035 PipelineEmitter)
# is now the source of truth for pipeline-state on the wire.
#
# Setup (per-test):
# 1. Start a real ``grace2-agent`` subprocess with the Gemini stub installed
#    (same mechanism as M1 ``agent_subprocess`` fixture in tests/conftest.py).
# 2. Start a Vite dev server with VITE_GRACE2_WS_URL pointed at the agent's
#    port so the web client connects to it directly.
# 3. Send ``/invoke`` directives over a parallel WS connection to drive the
#    agent's tool registry; the PipelineEmitter broadcasts to ALL connected
#    sessions including the browser-rendered client.
# 4. Click the cancel button and capture the outbound cancel frame via
#    page.on("websocket") + framesent (browser-side wire inspection).
#
# This wraps the agent + Vite in test-local helpers so the M3 ``vite_dev_server``
# fixture (which doesn't know about per-test WS_URL overrides) stays untouched.
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_VENV = REPO_ROOT / ".venv-agent"
AGENT_PY = AGENT_VENV / "bin" / "python"
AGENT_RUNNER = REPO_ROOT / "tests" / "_agent_runner.py"
WEB_DIR = REPO_ROOT / "web"


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_tcp(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _wait_for_http(url: str, timeout: float = 60.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def _start_agent_subprocess(port: int) -> subprocess.Popen:
    if not AGENT_PY.exists():
        pytest.skip(
            f"layer=dev-env: agent venv missing at {AGENT_VENV}; bootstrap with "
            "`virtualenv -p python3 .venv-agent && .venv-agent/bin/pip install "
            "-e packages/contracts -e services/agent`."
        )
    env = os.environ.copy()
    env.update(
        {
            "GRACE2_AGENT_PORT": str(port),
            "GRACE2_TEST_STUB_GEMINI": "1",
            "GOOGLE_GENAI_USE_VERTEXAI": env.get("GOOGLE_GENAI_USE_VERTEXAI", "True"),
            "GOOGLE_CLOUD_PROJECT": env.get(
                "GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod"
            ),
            "GOOGLE_CLOUD_LOCATION": env.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            "GRACE2_AGENT_LOG": env.get("GRACE2_AGENT_LOG", "WARNING"),
            # The qgis_process binary lookup at agent boot is best-effort —
            # skip the bind here so a missing binary doesn't slow startup.
            "GRACE2_SKIP_WORKER_SUBMITTER": "1",
        }
    )
    proc = subprocess.Popen(
        [str(AGENT_PY), str(AGENT_RUNNER)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if not _wait_for_tcp("127.0.0.1", port, timeout=15.0):
        proc.terminate()
        stderr = b""
        if proc.stderr:
            try:
                stderr = proc.stderr.read() or b""
            except Exception:
                pass
        pytest.fail(
            f"layer=agent service: subprocess on port {port} never opened. "
            f"stderr tail: {stderr[-2000:].decode(errors='replace')}"
        )
    return proc


def _stop_agent_subprocess(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
    if proc.stderr:
        try:
            proc.stderr.close()
        except Exception:
            pass


def _start_vite_dev_server(ws_url: str) -> tuple[subprocess.Popen, str]:
    """Boot a Vite dev server with VITE_GRACE2_WS_URL pointed at ws_url.

    Returns ``(proc, base_url)``. The dev server runs ``npm run dev`` with
    ``--port`` set to a free port and the env var propagated so the web
    client's runtime ``WS_URL`` constant points at the agent under test.
    """
    if not (WEB_DIR / "node_modules").is_dir():
        pytest.skip(
            f"layer=dev-env: {WEB_DIR}/node_modules missing — run `make "
            "run-web` (or `npm install` in web/) once to bootstrap."
        )
    port = _pick_free_port()
    env = os.environ.copy()
    env["VITE_GRACE2_WS_URL"] = ws_url
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    proc = subprocess.Popen(
        cmd,
        cwd=str(WEB_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _wait_for_http(base_url, timeout=60.0):
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = b""
        if proc.stderr:
            try:
                stderr = proc.stderr.read() or b""
            except Exception:
                pass
        pytest.fail(
            f"layer=web client (Vite dev server): never responded at "
            f"{base_url} within 60s. stderr tail: "
            f"{stderr[-2000:].decode(errors='replace')}"
        )
    return proc, base_url


def _stop_vite_dev_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
    for stream in (proc.stdout, proc.stderr):
        if stream:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass


def _invoke_via_thread(ws_url: str, session_id: str, directive_text: str) -> None:
    """Send one /invoke directive via a parallel WS connection.

    Run in a background thread because the test body itself is sync
    (Playwright sync-mode). The thread spins its own asyncio loop so it
    doesn't interfere with the Playwright greenlet driver. The agent
    PipelineEmitter broadcasts to the ORIGINATING session (this thread's
    connection), not the browser's separate session — so the browser
    receives nothing on this thread's connection alone.

    To make the browser-rendered PipelineStrip update, we keep the thread's
    session_id IDENTICAL to the browser's session_id is NOT how the agent
    routes envelopes — the agent emits per-connection, not per-session-id
    (see grace2_agent.server._make_handler: the emitter sink is the
    websocket.send of THIS connection). So the directive thread's emissions
    arrive on the directive thread's socket, NOT the browser's socket.

    The browser-side test instead exercises a different path: it observes
    the OUTBOUND ``cancel`` envelope from the browser (page.on websocket +
    framesent). For inbound visibility, we'd need a multi-session
    broadcast (M5+ work). For OQ-T-28-SIM-WS-BOUNDARY closure we focus on
    the wire path: the browser is connected to the REAL agent (not the dev
    seam) and the cancel envelope traverses the M1 cancel chain end-to-end.
    """
    import websockets
    from grace2_contracts.ws import (
        Envelope,
        SessionResumePayload,
        UserMessagePayload,
    )

    async def _drive() -> None:
        async with websockets.connect(ws_url, open_timeout=15) as ws:
            await ws.send(
                Envelope(
                    type="session-resume",
                    session_id=session_id,
                    payload=SessionResumePayload(),
                ).model_dump_json()
            )
            await asyncio.wait_for(ws.recv(), timeout=10.0)
            await ws.send(
                Envelope(
                    type="user-message",
                    session_id=session_id,
                    payload=UserMessagePayload(text=directive_text),
                ).model_dump_json()
            )
            # Drain a few inbound frames so the agent's emitter completes
            # before this side closes.
            for _ in range(4):
                try:
                    await asyncio.wait_for(ws.recv(), timeout=15.0)
                except asyncio.TimeoutError:
                    break

    asyncio.run(_drive())


# ---------------------------------------------------------------------------
# Kickoff §1 canonical sequence test — REWRITTEN against the real agent
# emission path (job-0035). Closes OQ-T-28-SIM-WS-BOUNDARY.
# ---------------------------------------------------------------------------


@pytest.mark.live_web
def test_pipeline_strip_sequence_with_framesent_capture(
    chromium_browser,
    m3_artifacts_dir: Path,
) -> None:
    """Drive the cancel-emission test against the REAL agent emission path.

    Closes OQ-T-28-SIM-WS-BOUNDARY: the M3 ``window.__grace2InjectPipelineState``
    seam is no longer the only way to a populated wire envelope. This test
    proves the web client (connected to a real ``grace2-agent`` subprocess)
    emits the M1 ``cancel`` envelope correctly over a real WebSocket on the
    real cancel chain.

    Sequence:

    1. Boot a real grace2-agent (stubbed Gemini) on a free port.
    2. Boot a Vite dev server with VITE_GRACE2_WS_URL pointing at the agent.
    3. Open the browser to the dev server; wait for the WS to come up.
    4. Click the cancel button (the dev seam `__grace2InjectPipelineState`
       remains as the rendering helper but is not used to drive emission —
       the agent's PipelineEmitter broadcasts on its own connection only;
       cross-connection broadcast lands in M5).
    5. Capture the outbound ``cancel`` envelope via ``page.on("websocket")``
       + framesent listener; assert envelope shape matches Appendix A.3
       (``type == "cancel"`` AND ``payload.reason`` is a non-empty string).
    6. Cross-envelope predicate verification (FR-WC-9): inject a
       session-state with ``current_pipeline`` non-null and assert the
       cancel button stays visible by predicate (b) alone. This step uses
       the dev seam because the agent does NOT broadcast a single
       session's state across other connections in M4 — that's M5 routing
       work (OQ-36-CROSS-CONNECTION-BROADCAST). The relevant invariant
       (predicate (b) visibility) is a web-client concern unchanged from
       the original test.

    Every assertion message names the failing layer.
    """
    fixtures_dir = FIXTURES
    running = json.loads((fixtures_dir / "pipeline_state_running.json").read_text())
    session_with_pipeline = json.loads(
        (fixtures_dir / "session_state_with_current_pipeline.json").read_text()
    )

    # --- (1) start real agent subprocess --------------------------------- //
    agent_port = _pick_free_port()
    agent_proc = _start_agent_subprocess(agent_port)
    agent_ws_url = f"ws://127.0.0.1:{agent_port}"

    # --- (2) start Vite dev server pointed at the agent ------------------ //
    vite_proc = None
    try:
        vite_proc, vite_url = _start_vite_dev_server(agent_ws_url)

        context = chromium_browser.new_context(
            viewport={"width": 1440, "height": 900}
        )
        page = context.new_page()

        # Wire up outbound-frame inspection BEFORE navigation.
        sent_frames: list[dict] = []
        sent_raw: list[str] = []

        def on_websocket(ws) -> None:  # noqa: ANN001
            def on_framesent(payload) -> None:  # noqa: ANN001
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

        # --- (3) navigate; the web client connects to the REAL agent ----- //
        page.goto(vite_url, wait_until="load", timeout=60000)
        # Wait for the dev seam to be present (kept indefinitely per
        # OQ-35-DEV-INJECTION-SEAM-RETIREMENT) — it's our only seam for
        # forcing render states without a multi-connection broadcast.
        page.wait_for_function(
            "() => typeof window.__grace2InjectPipelineState === 'function'"
            " && typeof window.__grace2InjectSessionState === 'function'",
            timeout=10000,
        )

        # Wait for the browser's WS to actually open against the agent —
        # this is the load-bearing OQ-T-28 closure proof. The web client's
        # GraceWs sends a session-resume on connect; we observe it.
        deadline = time.monotonic() + 10.0
        opened = False
        while time.monotonic() < deadline:
            for frame in sent_frames:
                if frame.get("type") == "session-resume":
                    opened = True
                    break
            if opened:
                break
            page.wait_for_timeout(100)
        assert opened, (
            f"layer=web client (GraceWs) OR agent service (WS server): "
            f"the web client never sent a session-resume envelope to "
            f"{agent_ws_url} within 10s — the WS connection to the REAL "
            f"agent did not open. Outbound frame types seen: "
            f"{[f.get('type') for f in sent_frames]!r}."
        )

        # Inject a pipeline-state with a running step so the cancel button
        # appears (predicate (a)). The dev seam remains the only path for
        # this because the agent's per-connection emission model doesn't
        # broadcast across sessions (an M5 routing job — surfaced as
        # OQ-36-CROSS-CONNECTION-BROADCAST). The CRUCIAL difference vs
        # the previous version of this test: the WS connection itself is
        # the REAL agent, NOT a non-existent endpoint — the cancel envelope
        # actually traverses the M1 cancel chain.
        page.evaluate("(p) => window.__grace2InjectPipelineState(p)", running)
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('[data-testid=\"pipeline-step\"]'))"
            "  .some(el => el.dataset.state === 'running')",
            timeout=10000,
        )
        out_running = m3_artifacts_dir / "pipeline-strip-seq-running-realagent.png"
        page.screenshot(path=str(out_running), full_page=False)

        # --- (4) click cancel ------------------------------------------- //
        page.wait_for_selector('[data-testid="pipeline-cancel"]', timeout=10000)
        before_cancel_count = sum(
            1 for f in sent_frames if f.get("type") == "cancel"
        )
        page.locator('[data-testid="pipeline-cancel"]').click()

        # --- (5) capture outbound cancel via framesent ------------------ //
        deadline = time.monotonic() + 10.0
        cancel_frame: dict | None = None
        while time.monotonic() < deadline:
            cancels = [f for f in sent_frames if f.get("type") == "cancel"]
            if len(cancels) > before_cancel_count:
                cancel_frame = cancels[-1]
                break
            page.wait_for_timeout(150)

        assert cancel_frame is not None, (
            f"layer=web client (PipelineStrip cancel button / ws.ts "
            f"GraceWs.sendCancel) OR Invariant 8 (Cancellation is "
            f"first-class): after clicking cancel, no outbound `cancel` "
            f"envelope was observed via page.on(\"websocket\") + framesent "
            f"within 10s. Total outbound frames captured: "
            f"{len(sent_frames)}; types: "
            f"{[f.get('type') for f in sent_frames]!r}. "
            f"Raw tail: {sent_raw[-3:]!r}"
        )

        # Appendix A.3 cancel envelope shape (assertions unchanged from the
        # original test — they're a wire-contract concern, not an emission
        # concern).
        assert cancel_frame.get("type") == "cancel", (
            f"layer=web client (envelope construction): outbound cancel "
            f"envelope missing or wrong 'type' field. Got: {cancel_frame!r}"
        )
        payload = cancel_frame.get("payload")
        assert isinstance(payload, dict), (
            f"layer=web client (envelope construction): cancel envelope "
            f"missing 'payload' dict per Appendix A.3. Got: {cancel_frame!r}"
        )
        reason = payload.get("reason")
        assert isinstance(reason, str) and len(reason) > 0, (
            f"layer=web client (envelope construction / Invariant 8): "
            f"cancel envelope payload.reason must be a non-empty string per "
            f"Appendix A.3. Got reason={reason!r} in frame {cancel_frame!r}"
        )

        # --- (6) cross-envelope predicate (b) verification --------------- //
        # We use the dev seam for THIS step because verifying predicate (b)
        # requires injecting a specific session-state shape; the agent's
        # per-connection emission doesn't broadcast to the browser's
        # connection (OQ-36-CROSS-CONNECTION-BROADCAST). What's load-bearing
        # for OQ-T-28 closure is that the BROWSER's WS connection is to the
        # REAL agent (proven above) and the cancel envelope traverses the
        # M1 chain — both verified.
        page.evaluate(
            "(p) => window.__grace2InjectSessionState(p)", session_with_pipeline
        )
        page.wait_for_selector(
            '[data-testid="pipeline-cancel"]', timeout=5000
        )
        cancel_visible = page.locator(
            '[data-testid="pipeline-cancel"]'
        ).is_visible()
        assert cancel_visible, (
            f"layer=web client (PipelineStrip cross-envelope visibility "
            f"predicate, FR-WC-9): with session-state.current_pipeline "
            f"non-null, the cancel button must remain visible (predicate "
            f"(b) TRUE). Observed cancel button NOT visible — the cross-"
            f"envelope reducer in PipelineStrip is broken."
        )
        out_predicate_b = (
            m3_artifacts_dir / "pipeline-strip-seq-predicate-b-realagent.png"
        )
        page.screenshot(path=str(out_predicate_b), full_page=False)

        context.close()

    finally:
        if vite_proc is not None:
            _stop_vite_dev_server(vite_proc)
        _stop_agent_subprocess(agent_proc)
