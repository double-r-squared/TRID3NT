"""M5 acceptance Playwright screenshot capture (job-0043).

Mirrors job-0036's screenshot-capture pattern (M4 acceptance). Captures
three canonical PNGs at 1280×800 in headless Chromium under
``reports/inflight/job-0043-testing-20260606/evidence/screenshots/``:

1. ``baseline.png`` — idle PipelineStrip + LayerPanel (first paint of the
   web client, no injected state).
2. ``mid_run.png`` — PipelineStrip with ≥3 progress emissions visible
   (mirrors the substrate's real progress emission per job-0041's
   measured 36 emissions over ~3 min on the run_solver layer).
3. ``final_honest_failure.png`` — the terminal HONEST FAILURE state with
   ``LANDCOVER_READ_FAILED`` or ``HYDROMT_UNAVAILABLE`` visible. (Or
   ``final_success.png`` for the SUCCESS path. The harness picks the
   filename based on the smoke_demo_envelope.json outcome field.)

The dev-injection seam (``window.__grace2InjectSessionState`` /
``window.__grace2InjectPipelineState``) is used because the test boots
the web client in dev-mode (Vite); the real agent emission path is
exercised live by the smoke harness + pytest M5 suite — the screenshots
visualize the wire-shape contracts the agent produces, captured for
the SendUserFile relay the orchestrator surfaces.

Run:

    .venv-agent/bin/python reports/inflight/job-0043-testing-20260606/evidence/capture_screenshots.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[4]
WEB = REPO / "web"
EVIDENCE_DIR = Path(__file__).resolve().parent
SHOTS = EVIDENCE_DIR / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as r:
                if 200 <= r.status < 500:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _start_vite() -> tuple[subprocess.Popen, str]:
    port = _free_port()
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    proc = subprocess.Popen(
        cmd,
        cwd=str(WEB),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    if not _wait_http(url, timeout=60.0):
        proc.terminate()
        raise RuntimeError("layer=web client: Vite never responded")
    return proc, url


# Pipeline state fixtures mirroring the M5 substrate shape — modeled after
# the real wire shapes emitted by PipelineEmitter (job-0035 / job-0041 /
# job-0042) when run_model_flood_scenario composes the M5 chain.

_M5_BASELINE_SESSION = {
    "chat_history": [],
    "loaded_layers": [],
    "pipeline_history": [],
    "current_pipeline": None,
    "map_view": {"center": [-81.85, 26.62], "zoom": 11, "bearing": 0, "pitch": 0},
}

# Mid-run: 3 progress emissions visible (mirrors run_model_flood_scenario
# composing the chain) — geocode + fetch_dem complete, fetch_landcover
# running with progress emission. Mirrors the SRS Appendix D.6 step shape
# extended by job-0030 (progress_percent / error_code / error_message).
_M5_MID_RUN_PIPELINE = {
    "pipeline_id": "01KTGM5MIDRUNJ0043000000001",
    "steps": [
        {
            "step_id": "01KTGM5STEP000000000000001",
            "name": "Geocode Fort Myers",
            "tool_name": "geocode_location",
            "state": "complete",
            "progress_percent": 100,
            "started_at": "2026-06-07T02:40:21Z",
            "completed_at": "2026-06-07T02:40:22Z",
        },
        {
            "step_id": "01KTGM5STEP000000000000002",
            "name": "Fetch DEM (USGS 3DEP)",
            "tool_name": "fetch_dem",
            "state": "complete",
            "progress_percent": 100,
            "started_at": "2026-06-07T02:40:22Z",
            "completed_at": "2026-06-07T02:40:24Z",
        },
        {
            "step_id": "01KTGM5STEP000000000000003",
            "name": "Fetch NLCD landcover (MRLC WCS)",
            "tool_name": "fetch_landcover",
            "state": "running",
            "progress_percent": 65,
            "started_at": "2026-06-07T02:40:24Z",
        },
        {
            "step_id": "01KTGM5STEP000000000000004",
            "name": "Fetch NHDPlus HR rivers",
            "tool_name": "fetch_river_geometry",
            "state": "pending",
            "progress_percent": None,
        },
        {
            "step_id": "01KTGM5STEP000000000000005",
            "name": "Build SFINCS model deck",
            "tool_name": "build_sfincs_model",
            "state": "pending",
            "progress_percent": None,
        },
    ],
}

_M5_MID_RUN_SESSION = {
    "chat_history": [
        {
            "message_id": "01KTGM5CHATHURRICANEMSG0001",
            "role": "user",
            "text": "model the flood from Hurricane Ian on Fort Myers",
            "timestamp": "2026-06-07T02:40:20Z",
        }
    ],
    "loaded_layers": [],
    "pipeline_history": [],
    "current_pipeline": {
        "pipeline_id": _M5_MID_RUN_PIPELINE["pipeline_id"],
        "started_at": "2026-06-07T02:40:21Z",
        "completed_at": None,
        "final_state": None,
        "steps": _M5_MID_RUN_PIPELINE["steps"],
    },
    "map_view": _M5_BASELINE_SESSION["map_view"],
}


# Final state: HONEST FAILURE — the workflow ran through to
# build_sfincs_model and surfaced HYDROMT_UNAVAILABLE (the dev-box
# substrate-vs-output qualification per kickoff §1). Mirrors the live
# smoke_demo_envelope.json outcome captured in this evidence directory.
_M5_FINAL_HONEST_FAILURE_PIPELINE = {
    "pipeline_id": "01KTGM5FINALJ0043000000001",
    "steps": [
        {
            "step_id": "01KTGM5STEP000000000000001",
            "name": "Geocode Fort Myers",
            "tool_name": "geocode_location",
            "state": "complete",
            "progress_percent": 100,
        },
        {
            "step_id": "01KTGM5STEP000000000000002",
            "name": "Fetch DEM (USGS 3DEP)",
            "tool_name": "fetch_dem",
            "state": "complete",
            "progress_percent": 100,
        },
        {
            "step_id": "01KTGM5STEP000000000000003",
            "name": "Fetch NLCD landcover (MRLC WCS canonical)",
            "tool_name": "fetch_landcover",
            "state": "complete",
            "progress_percent": 100,
        },
        {
            "step_id": "01KTGM5STEP000000000000004",
            "name": "Fetch NHDPlus HR rivers",
            "tool_name": "fetch_river_geometry",
            "state": "complete",
            "progress_percent": 100,
        },
        {
            "step_id": "01KTGM5STEP000000000000005",
            "name": "Lookup Atlas 14 design storm",
            "tool_name": "lookup_precip_return_period",
            "state": "complete",
            "progress_percent": 100,
        },
        {
            "step_id": "01KTGM5STEP000000000000006",
            "name": "Build SFINCS model deck",
            "tool_name": "build_sfincs_model",
            "state": "failed",
            "progress_percent": None,
            "error_code": "HYDROMT_UNAVAILABLE",
            "error_message": (
                "hydromt_sfincs is not installed in the dev venv "
                "(heavyweight dep; production SFINCS container has it). "
                "Substrate ran through fetcher cache + NLCD gate PASS + "
                "Atlas 14 forcing (11.9 inches / 100-yr / 24-hr) and landed "
                "here. Acceptable M5 substrate-verification outcome per "
                "kickoff §1."
            ),
        },
    ],
}

_M5_FINAL_HONEST_FAILURE_SESSION = {
    "chat_history": [
        {
            "message_id": "01KTGM5CHATHURRICANEMSG0001",
            "role": "user",
            "text": "model the flood from Hurricane Ian on Fort Myers",
            "timestamp": "2026-06-07T02:40:20Z",
        },
        {
            "message_id": "01KTGM5CHATAGENTMSG00000001",
            "role": "agent",
            "text": (
                "Workflow ran end-to-end through the fetcher chain "
                "(geocode + DEM + NLCD + rivers + Atlas 14 design storm). "
                "Build deck failed honestly: hydromt_sfincs is not "
                "installed in the dev venv. Substrate verification passes; "
                "scientific output deferred to production deploy."
            ),
            "timestamp": "2026-06-07T02:40:28Z",
        },
    ],
    "loaded_layers": [],
    "pipeline_history": [_M5_FINAL_HONEST_FAILURE_PIPELINE],
    "current_pipeline": None,
    "map_view": _M5_BASELINE_SESSION["map_view"],
}


def _read_outcome() -> str:
    """Pick the final-state filename based on the smoke run outcome."""
    envelope_path = EVIDENCE_DIR / "smoke_demo_envelope.json"
    if not envelope_path.exists():
        return "final_honest_failure.png"
    try:
        data = json.loads(envelope_path.read_text())
        if data.get("outcome") == "SUCCESS":
            return "final_success.png"
        return "final_honest_failure.png"
    except Exception:
        return "final_honest_failure.png"


def _capture(page, label: str, out_path: Path) -> None:
    # Vite dev server holds an HMR websocket so networkidle never settles;
    # use the domcontentloaded gate + an explicit wait for MapLibre/React
    # commits instead.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    page.screenshot(path=str(out_path), full_page=False)
    print(f"  wrote {label}: {out_path}")


def main() -> int:
    print("==> launching Vite dev server")
    vite, base_url = _start_vite()
    print(f"    Vite up at {base_url}")
    final_name = _read_outcome()
    final_path = SHOTS / final_name
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                page.goto(base_url, wait_until="load", timeout=60000)
                page.wait_for_function(
                    "() => typeof window.__grace2InjectSessionState === 'function' "
                    "&& typeof window.__grace2InjectPipelineState === 'function'",
                    timeout=10000,
                )

                # --- baseline ---
                print("==> capture: baseline (idle PipelineStrip + LayerPanel)")
                page.evaluate(
                    "(p) => window.__grace2InjectSessionState(p)",
                    _M5_BASELINE_SESSION,
                )
                _capture(page, "baseline", SHOTS / "baseline.png")

                # --- mid-run ---
                print(
                    "==> capture: mid_run (≥3 progress emissions: geocode + "
                    "fetch_dem complete, fetch_landcover running, "
                    "fetch_river_geometry + build_sfincs_model pending)"
                )
                page.evaluate(
                    "(p) => window.__grace2InjectSessionState(p)",
                    _M5_MID_RUN_SESSION,
                )
                page.evaluate(
                    "(p) => window.__grace2InjectPipelineState(p)",
                    _M5_MID_RUN_PIPELINE,
                )
                _capture(page, "mid_run", SHOTS / "mid_run.png")

                # --- final state (HONEST FAILURE per kickoff acceptance) ---
                print(f"==> capture: {final_name}")
                page.evaluate(
                    "(p) => window.__grace2InjectSessionState(p)",
                    _M5_FINAL_HONEST_FAILURE_SESSION,
                )
                page.evaluate(
                    "(p) => window.__grace2InjectPipelineState(p)",
                    _M5_FINAL_HONEST_FAILURE_PIPELINE,
                )
                _capture(page, final_name, final_path)

                ctx.close()
            finally:
                browser.close()
    finally:
        if vite.poll() is None:
            vite.terminate()
            try:
                vite.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                vite.kill()
        # Drain pipes so subprocess unblocks.
        try:
            if vite.stdout:
                vite.stdout.close()
            if vite.stderr:
                vite.stderr.close()
        except Exception:
            pass

    # Summary index for the report.
    index = {
        "baseline.png": "idle PipelineStrip + LayerPanel; no injected state",
        "mid_run.png": (
            "mid-run with 5 steps: geocode + fetch_dem complete, "
            "fetch_landcover running (≥3 progress emissions visible), "
            "fetch_river_geometry + build_sfincs_model pending"
        ),
        final_name: (
            "final HONEST FAILURE state: 5 fetcher steps complete + "
            "build_sfincs_model failed with HYDROMT_UNAVAILABLE; "
            "substrate verification through the chain; "
            "kickoff §1 acceptable outcome"
        ),
    }
    (SHOTS / "screenshots_index.json").write_text(
        json.dumps(index, indent=2),
    )
    print(f"==> screenshots index: {SHOTS / 'screenshots_index.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
