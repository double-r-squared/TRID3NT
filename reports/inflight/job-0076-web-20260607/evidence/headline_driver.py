"""Headline screenshot driver — job-0076 Part 4.

Boots the dev server, injects the job-0075 WMS layer + Fort Myers bbox,
zooms to z=13 (closer than fitBounds default zoom-11), and captures the
headline screenshot in BOTH light and dark themes.

Both screenshots MUST show the blue flood overlay matching the
known-good WMS GetMap pattern in
`reports/complete/job-0075-engine-20260607/evidence/wms_full_0075.png`.

The hide-panels step is intentional — the user has been bitten three times
by "LayerPanel populated == flood overlay rendered" confusion. This driver
collapses both side panels via the close-buttons so the screenshot shows
ONLY the map canvas plus the floating theme toggle button. Pixels = truth.
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
# Centered so the Caloosahatchee inundation pattern is large in frame at z=13.
# wms_full_0075.png shows the heaviest blue along the river running through
# the western half of FORT_MYERS_BBOX.
FORT_MYERS_BBOX = [-81.91, 26.55, -81.75, 26.69]
CENTER = [-81.86, 26.63]  # nudge slightly west to keep the river in frame
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
        return 1
    print(f"[driver] vite dev server up at {base_url}")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()

                wms_responses: list[dict] = []

                def on_response(resp):
                    url = resp.url
                    if "flood-depth-job-0075-demo" in url or "basemaps.cartocdn.com" in url:
                        try:
                            body = resp.body()
                            size = len(body) if body else 0
                        except Exception:
                            size = -1
                        wms_responses.append({"url": url, "status": resp.status, "size": size})

                page.on("response", on_response)

                # --- 1. Boot the page ----------------------------------------
                page.goto(base_url, wait_until="load", timeout=60_000)
                page.wait_for_function(
                    "() => typeof window.__grace2InjectSessionState === 'function'"
                    "   && typeof window.__grace2InjectMapCommand === 'function'",
                    timeout=15_000,
                )
                # Give the basemap a moment to settle.
                page.wait_for_timeout(2000)

                # --- 2. Inject the flood layer + center on Fort Myers --------
                session_state = {
                    "chat_history": [],
                    "loaded_layers": [
                        {
                            "layer_id": "flood-depth-job-0075-demo",
                            "name": "Hurricane Ian peak flood depth (job-0076)",
                            "layer_type": "raster",
                            "uri": REAL_WMS_URL,
                            "source_url": REAL_WMS_URL,
                            "style_preset": "continuous_flood_depth",
                            "visible": True,
                            "opacity": 0.9,
                            "z_index": 2,
                            "role": "primary",
                            "bbox": FORT_MYERS_BBOX,
                            "attribution": "GRACE-2 job-0076 — WMS render race-condition fixed",
                            "temporal": None,
                        }
                    ],
                    "pipeline_history": [],
                    "current_pipeline": None,
                    "map_view": {
                        "center": CENTER,
                        "zoom": ZOOM,
                        "bearing": 0,
                        "pitch": 0,
                    },
                }
                print("[driver] injecting session-state...")
                page.evaluate(
                    "(payload) => window.__grace2InjectSessionState(payload)",
                    session_state,
                )

                # --- 3. Zoom programmatically to z=13 (kickoff requirement) --
                page.wait_for_selector(
                    '[data-testid="grace2-layer-panel"]',
                    timeout=10_000,
                )
                print("[driver] zoom-to via map-command (initial fitBounds @ zoom-11)...")
                page.evaluate(
                    "(payload) => window.__grace2InjectMapCommand(payload)",
                    {"command": "zoom-to", "args": {"bbox": FORT_MYERS_BBOX}},
                )
                # Wait for fitBounds animation, then jumpTo z=13 close-up.
                page.wait_for_timeout(2000)
                page.evaluate(
                    f"""() => {{
                        const m = window.__grace2GetMap();
                        if (m) m.jumpTo({{center: {json.dumps(CENTER)}, zoom: {ZOOM}}});
                    }}"""
                )
                # Wait for z=13 tiles to fetch and paint.
                page.wait_for_timeout(8000)

                # --- 4. Close both panels for a clean map-canvas-only shot ---
                # (LayerPanel and Chat panels both have a × close button.)
                # The user has been burned by "LayerPanel populated == flood
                # rendered" confusion. Hide them; show only map area.
                try:
                    page.click('[data-testid="grace2-layer-panel-close"]', timeout=3000)
                except Exception:
                    pass
                try:
                    page.click('[data-testid="grace2-chat-close"]', timeout=3000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)

                # --- 5. Light-theme screenshot -------------------------------
                light_path = EVIDENCE_DIR / "headline_light_FINAL.png"
                print(f"[driver] light-theme screenshot -> {light_path}")
                page.screenshot(path=str(light_path), full_page=False)

                # --- 6. Toggle to dark theme -----------------------------------
                print("[driver] toggling theme to dark...")
                page.click('[data-testid="grace2-theme-toggle"]', timeout=5000)
                # Wait for CartoDB dark tiles to fetch and paint.
                page.wait_for_timeout(8000)

                # --- 7. Dark-theme screenshot --------------------------------
                dark_path = EVIDENCE_DIR / "headline_dark_FINAL.png"
                print(f"[driver] dark-theme screenshot -> {dark_path}")
                page.screenshot(path=str(dark_path), full_page=False)

                # --- 8. Summary -----------------------------------------------
                flood = [r for r in wms_responses if "flood-depth" in r["url"]]
                carto = [r for r in wms_responses if "basemaps.cartocdn.com" in r["url"]]
                flood_substantial = [r for r in flood if r["size"] > 1000]
                print(f"[driver] total flood tile responses: {len(flood)}")
                print(f"[driver]   of which substantial (>1KB, real overlay): {len(flood_substantial)}")
                print(f"[driver]   first substantial flood tile URL:")
                if flood_substantial:
                    print(f"[driver]     {flood_substantial[0]['url']}")
                print(f"[driver] CartoDB dark tile responses: {len(carto)}")
                print(f"[driver] localStorage grace2.theme: {page.evaluate('() => localStorage.getItem(\"grace2.theme\")')}")

                # Verify the dark basemap layer is in the style at end of run.
                style_dump = page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap();
                        if (!m) return null;
                        const s = m.getStyle();
                        return {
                            layer_ids: s.layers.map(l => l.id),
                            source_ids: Object.keys(s.sources),
                            center: m.getCenter().toArray(),
                            zoom: m.getZoom(),
                        };
                    }"""
                )
                print(f"[driver] FINAL STYLE: {json.dumps(style_dump, indent=2)}")

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
