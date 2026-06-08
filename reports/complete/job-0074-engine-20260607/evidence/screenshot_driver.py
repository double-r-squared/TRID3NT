"""Headline screenshot driver — job-0074 Part 5 (FINAL sprint-10 deliverable).

Drives the dev-injection seam (window.__grace2InjectSessionState +
window.__grace2InjectMapCommand) with the REAL WMS URL for the freshly
published flood-depth-job-0074-demo layer.

Differences from job-0070 driver:
  - Layer ID: flood-depth-job-0074-demo (not job-0070-demo)
  - WMS URL: no hand-correction needed — DOUBLE-MNT-PREFIX bug is FIXED in
    this job's worker rebuild; worker emitted single-prefix URL natively.
  - opacity=0.9 (per kickoff spec)
  - App.tsx now wires onMapCommand → bus.pushMapCommand (OQ-72-APP-MAPCMD-WIRING
    closed in this job's Part 2). Production routing is complete end-to-end.

Usage:
    .venv-agent/bin/python reports/inflight/job-0074-engine-20260607/evidence/screenshot_driver.py

Prerequisites:
    - web/ Vite dev server can be launched (npm run dev)
    - Playwright + Chromium installed in .venv-agent
    - QGIS Server serving flood-depth-job-0074-demo (Part 4 done)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WEB_DIR = REPO_ROOT / "web"
EVIDENCE_DIR = Path(__file__).resolve().parent

# WMS URL — emitted natively by the rebuilt worker (DOUBLE-MNT-PREFIX fixed).
# No hand-correction needed for this job.
REAL_WMS_URL = (
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms"
    "?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0074-demo"
)

# Fort Myers BBOX (lon/lat)
FORT_MYERS_BBOX = [-81.91, 26.55, -81.75, 26.69]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 90.0) -> bool:
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


def main() -> int:
    from playwright.sync_api import sync_playwright

    port = _free_port()
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    print(f"[driver] launching Vite dev server: {' '.join(cmd)} (cwd={WEB_DIR})")
    proc = subprocess.Popen(
        cmd,
        cwd=str(WEB_DIR),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _wait_for_http(base_url, timeout=90.0):
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[driver] FATAL: vite dev server never responded", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr.read().decode(errors="replace")[-2000:], file=sys.stderr)
        return 1
    print(f"[driver] vite dev server up at {base_url}")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                page.goto(base_url, wait_until="load", timeout=60_000)

                page.wait_for_function(
                    "() => typeof window.__grace2InjectSessionState === 'function'"
                    "   && typeof window.__grace2InjectMapCommand === 'function'",
                    timeout=15_000,
                )

                session_state = {
                    "chat_history": [],
                    "loaded_layers": [
                        {
                            "layer_id": "flood-depth-job-0074-demo",
                            "name": "Hurricane Ian — peak flood depth",
                            "layer_type": "raster",
                            "uri": REAL_WMS_URL,
                            "source_url": REAL_WMS_URL,
                            "style_preset": "continuous_flood_depth",
                            "visible": True,
                            "opacity": 0.9,
                            "z_index": 2,
                            "role": "primary",
                            "bbox": FORT_MYERS_BBOX,
                            "attribution": "GRACE-2 job-0074 — FINAL sprint-10 (bugs fixed: DOUBLE-MNT + WMSCrsList + App.tsx routing)",
                            "temporal": None,
                        }
                    ],
                    "pipeline_history": [],
                    "current_pipeline": None,
                    "map_view": {
                        "center": [-81.83, 26.62],
                        "zoom": 11,
                        "bearing": 0,
                        "pitch": 0,
                    },
                }
                print("[driver] injecting session-state with REAL WMS URL (flood-depth-job-0074-demo)...")
                page.evaluate(
                    "(payload) => window.__grace2InjectSessionState(payload)",
                    session_state,
                )

                page.wait_for_selector(
                    '[data-testid="grace2-layer-panel"]',
                    timeout=10_000,
                )
                page.wait_for_selector(
                    '[data-testid="grace2-layer-legend"]',
                    timeout=10_000,
                )

                # --- Headline screenshot: Fort Myers with flood overlay ---------
                # The rebuilt worker now correctly emits a single-prefix WMS URL
                # (DOUBLE-MNT-PREFIX bug fixed). The COG is EPSG:32617 (job-0063
                # fix, job-0070 verified). QGIS Server publishes the layer at the
                # correct Fort Myers geographic location. Tiles in EPSG:3857 are
                # served correctly. Production map-command routing via App.tsx
                # onMapCommand wiring is now complete end-to-end.
                print("[driver] zoom-to Fort Myers via map-command (OQ-72-APP-MAPCMD-WIRING now complete)...")
                page.evaluate(
                    "(payload) => window.__grace2InjectMapCommand(payload)",
                    {"command": "zoom-to", "args": {"bbox": FORT_MYERS_BBOX}},
                )
                # 5 seconds for WMS tiles to fetch and render (kickoff spec: "Wait 5 seconds")
                page.wait_for_timeout(5000)

                out_path = EVIDENCE_DIR / "headline_fort_myers_FINAL.png"
                print(f"[driver] screenshotting → {out_path}")
                page.screenshot(path=str(out_path), full_page=False)
                print("[driver] saved headline_fort_myers_FINAL.png")

                # Dump DOM state for diagnostics / audit
                added_layers = page.evaluate(
                    """() => {
                        const rows = Array.from(document.querySelectorAll(
                            '[data-testid="layer-row"]'
                        )).map(r => r.textContent || '');
                        const legend = document.querySelector('[data-testid="grace2-layer-legend"]');
                        return {
                            layer_panel_rows: rows,
                            legend_present: !!legend,
                            legend_text: legend ? legend.textContent : null,
                        };
                    }"""
                )
                print(f"[driver] DOM state: {json.dumps(added_layers, indent=2)}")

                context.close()
            finally:
                browser.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        for stream in (proc.stdout, proc.stderr):
            if stream:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

    print("[driver] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
