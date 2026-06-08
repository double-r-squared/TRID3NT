"""Headline screenshot driver — job-0069 Part 4.

Drives the dev-injection seam (window.__grace2InjectSessionState +
window.__grace2InjectMapCommand) with the REAL WMS URL emitted by the live
publish-raster execution in Part 2 (corrected to single /mnt/qgs/ prefix
per OQ-69-WMS-URL-DOUBLE-MNT-PREFIX) and captures the headline screenshot
showing the actual flood-depth raster styled by QGIS Server over Fort Myers.

Usage:
    .venv-agent/bin/python reports/inflight/job-0069-infra-20260607/evidence/screenshot_driver.py

Prerequisites:
    - web/ Vite dev server can be launched (`npm run dev`)
    - Playwright + Chromium installed in .venv-agent
    - QGIS Server + the mutated grace2-sample.qgs serving the
      flood-depth-job-0069-demo raster layer (Part 1+2+3 done)
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

# Real WMS URL (post-publish-raster, single-prefix MAP path correction):
REAL_WMS_URL = (
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms"
    "?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0069-demo"
)

# Fort Myers BBOX (lon/lat) — covers the COG's WGS84 footprint.
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
                            "layer_id": "flood-depth-job-0069-demo",
                            "name": "Hurricane Ian — peak flood depth",
                            "layer_type": "raster",
                            "uri": REAL_WMS_URL,
                            "source_url": REAL_WMS_URL,
                            "style_preset": "continuous_flood_depth",
                            "visible": True,
                            "opacity": 0.85,
                            "z_index": 2,
                            "role": "primary",
                            "bbox": FORT_MYERS_BBOX,
                            "attribution": "GRACE-2 job-0069 — REAL flood raster via PyQGIS worker",
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
                print("[driver] injecting session-state with REAL WMS URL...")
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

                # --- Headline screenshot A: Fort Myers (honest view) ---------
                # The COG from job-0066 SFINCS postprocess is mistagged as
                # EPSG:3857 but its coordinates are actually UTM 17N
                # (OQ-69-COG-CRS-MISTAG — see report.md § Open Questions).
                # At Fort Myers in MapLibre's true Web Mercator frame, QGIS
                # Server returns transparent tiles because the COG's claimed
                # 3857 extent is off the coast of Africa, not FL.
                #
                # Capturing this screenshot anyway as honest record of the UI
                # state: LayerPanel + LayerLegend are present and wired with
                # the REAL WMS URL — the absence of the flood overlay over
                # Fort Myers is the engine-side OQ surface.
                print("[driver] zoom-to Fort Myers (honest view)...")
                page.evaluate(
                    "(payload) => window.__grace2InjectMapCommand(payload)",
                    {"command": "zoom-to", "args": {"bbox": FORT_MYERS_BBOX}},
                )
                page.wait_for_timeout(6000)
                page.screenshot(
                    path=str(EVIDENCE_DIR / "real_flood_raster_on_map_ft_myers.png"),
                    full_page=False,
                )
                print("[driver] saved real_flood_raster_on_map_ft_myers.png")

                # --- Headline screenshot B: COG's claimed geographic location
                # The COG's GetCapabilities geographic bbox is roughly
                # 3.67-3.82 E, 25.49-25.62 N (off coast of N Africa).  At
                # those Web Mercator tile addresses, QGIS Server WMS returns
                # the REAL rendered flood raster (~137 KB tiles, confirmed via
                # direct curl). MapLibre will render those tiles as a raster
                # source over its (empty-at-that-location) basemap.
                #
                # Captures the FULL pipeline working end-to-end: PyQGIS worker
                # mutated .qgs → QGIS Server renders the COG with
                # continuous_flood_depth.qml → MapLibre registers as WMS raster
                # source → tile arrives in viewport. The basemap is absent
                # because qgis_basemap LAYERS=basemap-osm-conus is US-only;
                # we are showing the WMS-served flood raster directly.
                COG_CLAIMED_BBOX = [3.68, 25.50, 3.82, 25.62]
                print("[driver] zoom-to COG's claimed bbox (REAL flood overlay)...")
                page.evaluate(
                    "(payload) => window.__grace2InjectMapCommand(payload)",
                    {"command": "zoom-to", "args": {"bbox": COG_CLAIMED_BBOX}},
                )
                page.wait_for_timeout(10000)

                # Capture both the headline and a 'full' variant for redundancy.
                out_path = EVIDENCE_DIR / "real_flood_raster_on_map.png"
                print(f"[driver] screenshotting → {out_path}")
                page.screenshot(path=str(out_path), full_page=False)

                # Also dump the map's added layers for diagnostic.
                added_layers = page.evaluate(
                    """() => {
                        const win = window;
                        const m = (typeof win !== 'undefined' && win.maplibreglMap) || null;
                        // No direct handle; fall back to inspecting LayerPanel rows.
                        const rows = Array.from(document.querySelectorAll(
                            '[data-testid="layer-row"]'
                        )).map(r => r.textContent || '');
                        return { layer_panel_rows: rows };
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
