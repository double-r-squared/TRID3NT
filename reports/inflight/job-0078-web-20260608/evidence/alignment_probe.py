"""Pixel-level alignment probe — job-0078.

Takes screenshots at zoom 13 centered on Fort Myers with the panels closed.
Capture: (a) basemap only (flood opacity 0), (b) flood only (no basemap,
by hiding basemap), (c) basemap + flood composite.

Diff (a) and (c) pixel-by-pixel to see exactly where the flood overlay
paints. If that paint area corresponds to where the basemap shows river
water, alignment is good.
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

REAL_WMS_URL = (
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms"
    "?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0075-demo"
)
FORT_MYERS_CENTER = [-81.86, 26.63]
ZOOM = 13


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
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    from playwright.sync_api import sync_playwright

    port = _free_port()
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    print(f"[probe] launching Vite dev server: {' '.join(cmd)}")
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
        print("[probe] FATAL: vite dev server never responded")
        return 1
    print(f"[probe] vite dev server up at {base_url}")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                page.goto(base_url, wait_until="load", timeout=60_000)
                page.wait_for_function(
                    "() => typeof window.__grace2InjectSessionState === 'function'",
                    timeout=15_000,
                )
                page.wait_for_timeout(2000)

                session_state = {
                    "chat_history": [],
                    "loaded_layers": [
                        {
                            "layer_id": "flood-depth-job-0075-demo",
                            "name": "Hurricane Ian peak flood depth",
                            "layer_type": "raster",
                            "uri": REAL_WMS_URL,
                            "source_url": REAL_WMS_URL,
                            "style_preset": "continuous_flood_depth",
                            "visible": True,
                            "opacity": 0.9,
                            "z_index": 2,
                            "role": "primary",
                            "bbox": [-81.91, 26.55, -81.75, 26.69],
                            "attribution": "GRACE-2 job-0078",
                            "temporal": None,
                        }
                    ],
                    "pipeline_history": [],
                    "current_pipeline": None,
                    "map_view": {
                        "center": FORT_MYERS_CENTER,
                        "zoom": ZOOM,
                        "bearing": 0,
                        "pitch": 0,
                    },
                }
                page.evaluate(
                    "(payload) => window.__grace2InjectSessionState(payload)",
                    session_state,
                )
                page.wait_for_selector('[data-testid="grace2-layer-panel"]', timeout=10_000)
                page.wait_for_timeout(2000)

                # Close the panels so they don't cover the map
                # The collapse buttons have specific testids
                try:
                    page.click('[data-testid="grace2-layer-panel-toggle"]', timeout=1000)
                except Exception:
                    pass
                try:
                    page.click('[data-testid="grace2-chat-toggle"]', timeout=1000)
                except Exception:
                    pass

                # Force jump to zoom 13 at Fort Myers
                page.evaluate(
                    """(c) => {
                        const m = window.__grace2GetMap ? window.__grace2GetMap() : null;
                        if (m) { m.jumpTo({center: c, zoom: 13}); }
                    }""",
                    FORT_MYERS_CENTER,
                )
                page.wait_for_timeout(8000)

                # Capture A: basemap + flood composite (current state)
                composite_path = EVIDENCE_DIR / "probe_composite.png"
                page.screenshot(path=str(composite_path), full_page=False)
                print(f"[probe] saved {composite_path}")

                # Capture B: basemap only (hide flood layer)
                page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap();
                        if (m && m.getLayer('flood-depth-job-0075-demo')) {
                            m.setLayoutProperty('flood-depth-job-0075-demo', 'visibility', 'none');
                        }
                    }"""
                )
                page.wait_for_timeout(2000)
                basemap_path = EVIDENCE_DIR / "probe_basemap_only.png"
                page.screenshot(path=str(basemap_path), full_page=False)
                print(f"[probe] saved {basemap_path}")

                # Capture C: flood only (hide basemap)
                page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap();
                        if (m && m.getLayer('flood-depth-job-0075-demo')) {
                            m.setLayoutProperty('flood-depth-job-0075-demo', 'visibility', 'visible');
                        }
                        if (m && m.getLayer('qgis-basemap')) {
                            m.setLayoutProperty('qgis-basemap', 'visibility', 'none');
                        }
                    }"""
                )
                page.wait_for_timeout(2000)
                flood_path = EVIDENCE_DIR / "probe_flood_only.png"
                page.screenshot(path=str(flood_path), full_page=False)
                print(f"[probe] saved {flood_path}")

                # Also dump style/center/zoom for reference
                info = page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap();
                        return {
                            center: m.getCenter().toArray(),
                            zoom: m.getZoom(),
                            bounds: m.getBounds().toArray(),
                        };
                    }"""
                )
                print(f"[probe] camera: {json.dumps(info)}")

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
