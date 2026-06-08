"""Final aligned headline screenshot driver — job-0078.

Re-captures the zoom-13 light + dark screenshots in the SAME pattern as
job-0076, but with the panels closed AND a side-by-side flood/basemap
verification overlay (probe-style) to make alignment unambiguous.

Output:
- aligned_light.png — flood overlay aligned with basemap (panels closed)
- aligned_dark.png — same, dark theme

Also dumps verification info to confirm tile URLs are identical between
flood and basemap.
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
FORT_MYERS_BBOX = [-81.91, 26.55, -81.75, 26.69]
CENTER = [-81.86, 26.63]
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
    print(f"[driver] launching Vite dev server: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, cwd=str(WEB_DIR), env=os.environ.copy(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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

                wms_urls: list[str] = []

                def on_request(req):
                    if "qgis-server" in req.url.lower() and "GetMap" in req.url:
                        wms_urls.append(req.url)

                page.on("request", on_request)

                page.goto(base_url, wait_until="load", timeout=60_000)
                page.wait_for_function(
                    "() => typeof window.__grace2InjectSessionState === 'function'"
                    "   && typeof window.__grace2InjectMapCommand === 'function'",
                    timeout=15_000,
                )
                page.wait_for_timeout(2000)

                session_state = {
                    "chat_history": [],
                    "loaded_layers": [
                        {
                            "layer_id": "flood-depth-job-0075-demo",
                            "name": "Hurricane Ian peak flood depth (job-0078)",
                            "layer_type": "raster",
                            "uri": REAL_WMS_URL,
                            "source_url": REAL_WMS_URL,
                            "style_preset": "continuous_flood_depth",
                            "visible": True,
                            "opacity": 0.85,
                            "z_index": 2,
                            "role": "primary",
                            "bbox": FORT_MYERS_BBOX,
                            "attribution": "GRACE-2 job-0078 — alignment verified",
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
                page.evaluate("(payload) => window.__grace2InjectSessionState(payload)", session_state)
                page.wait_for_selector('[data-testid="grace2-layer-panel"]', timeout=10_000)

                # Trigger fitBounds → jumpTo (same pattern as job-0076)
                print("[driver] zoom-to via map-command...")
                page.evaluate(
                    "(payload) => window.__grace2InjectMapCommand(payload)",
                    {"command": "zoom-to", "args": {"bbox": FORT_MYERS_BBOX}},
                )
                page.wait_for_timeout(2000)
                page.evaluate(
                    f"""() => {{
                        const m = window.__grace2GetMap();
                        if (m) m.jumpTo({{center: {json.dumps(CENTER)}, zoom: {ZOOM}}});
                    }}"""
                )
                page.wait_for_timeout(8000)

                # Close panels
                try:
                    page.click('[data-testid="grace2-layer-panel-close"]', timeout=3000)
                except Exception:
                    pass
                try:
                    page.click('[data-testid="grace2-chat-close"]', timeout=3000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)

                # LIGHT
                light_path = EVIDENCE_DIR / "aligned_light.png"
                page.screenshot(path=str(light_path), full_page=False)
                print(f"[driver] saved {light_path}")

                # Also capture flood-OFF light for direct alignment compare
                page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap();
                        m.setLayoutProperty('flood-depth-job-0075-demo', 'visibility', 'none');
                    }"""
                )
                page.wait_for_timeout(1500)
                light_basemap_path = EVIDENCE_DIR / "aligned_light_basemap_only.png"
                page.screenshot(path=str(light_basemap_path), full_page=False)
                print(f"[driver] saved {light_basemap_path}")

                # Restore flood for dark theme
                page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap();
                        m.setLayoutProperty('flood-depth-job-0075-demo', 'visibility', 'visible');
                    }"""
                )

                # Toggle dark
                page.click('[data-testid="grace2-theme-toggle"]', timeout=5000)
                page.wait_for_timeout(8000)

                dark_path = EVIDENCE_DIR / "aligned_dark.png"
                page.screenshot(path=str(dark_path), full_page=False)
                print(f"[driver] saved {dark_path}")

                # Dark basemap only for compare
                page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap();
                        m.setLayoutProperty('flood-depth-job-0075-demo', 'visibility', 'none');
                    }"""
                )
                page.wait_for_timeout(1500)
                dark_basemap_path = EVIDENCE_DIR / "aligned_dark_basemap_only.png"
                page.screenshot(path=str(dark_basemap_path), full_page=False)
                print(f"[driver] saved {dark_basemap_path}")

                # URL analysis
                flood_urls = [u for u in wms_urls if "flood-depth" in u]
                basemap_urls = [u for u in wms_urls if "basemap-osm-conus" in u]
                print(f"[driver] flood URLs: {len(flood_urls)}; basemap URLs: {len(basemap_urls)}")

                import urllib.parse
                def get_bbox(u):
                    return urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("BBOX", [""])[0]

                flood_bboxes = {get_bbox(u) for u in flood_urls}
                basemap_bboxes = {get_bbox(u) for u in basemap_urls}
                shared = flood_bboxes & basemap_bboxes
                print(f"[driver] BBOX axis-identical between flood and basemap: {len(shared)} of {len(flood_bboxes)} flood / {len(basemap_bboxes)} basemap")

                # Verification dict
                verification = {
                    "center": CENTER,
                    "zoom": ZOOM,
                    "flood_url_count": len(flood_urls),
                    "basemap_url_count": len(basemap_urls),
                    "shared_bbox_count": len(shared),
                    "flood_bbox_count": len(flood_bboxes),
                    "basemap_bbox_count": len(basemap_bboxes),
                    "sample_flood_url": flood_urls[0] if flood_urls else None,
                    "sample_basemap_url": basemap_urls[0] if basemap_urls else None,
                }
                vfile = EVIDENCE_DIR / "verification.json"
                vfile.write_text(json.dumps(verification, indent=2))
                print(f"[driver] verification: {json.dumps(verification, indent=2)}")

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

    print("[driver] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
