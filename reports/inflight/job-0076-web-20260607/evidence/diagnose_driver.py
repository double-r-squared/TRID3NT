"""Diagnostic Playwright driver — job-0076 Part 1.

Goal: prove or disprove each hypothesis from the kickoff by logging every
HTTP request fired by MapLibre when we inject the job-0075 WMS layer + a
zoom-to(Fort Myers) map-command.

Output: evidence/diagnosis.log — a verbatim record of every request URL,
response status, and bytes returned, plus the post-injection style spec
of the map (layers + sources). The orchestrator can read this to confirm
the root cause without trusting any agent's narration.
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

    log_path = EVIDENCE_DIR / "diagnosis.log"
    log_file = log_path.open("w")

    def log(line: str) -> None:
        print(line)
        log_file.write(line + "\n")
        log_file.flush()

    port = _free_port()
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    log(f"[driver] launching Vite dev server: {' '.join(cmd)} (cwd={WEB_DIR})")
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
        log("[driver] FATAL: vite dev server never responded")
        log_file.close()
        return 1
    log(f"[driver] vite dev server up at {base_url}")

    wms_requests: list[dict] = []
    console_msgs: list[str] = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()

                def on_request(req):
                    if "wms" in req.url.lower() or "qgis-server" in req.url.lower() or "basemaps.cartocdn" in req.url.lower():
                        log(f"[REQ ] {req.method} {req.url[:300]}")

                def on_response(resp):
                    url = resp.url
                    if "wms" in url.lower() or "qgis-server" in url.lower() or "basemaps.cartocdn" in url.lower():
                        try:
                            body = resp.body()
                            size = len(body) if body else 0
                        except Exception:
                            size = -1
                        content_type = ""
                        try:
                            content_type = resp.headers.get("content-type", "")
                        except Exception:
                            pass
                        entry = {
                            "url": url,
                            "status": resp.status,
                            "size": size,
                            "content_type": content_type,
                        }
                        wms_requests.append(entry)
                        log(f"[RESP] {resp.status} {size}B ct={content_type} URL={url[:300]}")

                def on_console(msg):
                    console_msgs.append(f"[{msg.type}] {msg.text}")
                    if msg.type in ("error", "warning"):
                        log(f"[CONS-{msg.type.upper()}] {msg.text[:500]}")

                page.on("request", on_request)
                page.on("response", on_response)
                page.on("console", on_console)

                page.goto(base_url, wait_until="load", timeout=60_000)
                log("[driver] page loaded")

                page.wait_for_function(
                    "() => typeof window.__grace2InjectSessionState === 'function'"
                    "   && typeof window.__grace2InjectMapCommand === 'function'",
                    timeout=15_000,
                )
                log("[driver] dev-injection seam present")

                # Wait for map idle (basemap loaded) before injecting overlay.
                page.wait_for_timeout(2000)

                session_state = {
                    "chat_history": [],
                    "loaded_layers": [
                        {
                            "layer_id": "flood-depth-job-0075-demo",
                            "name": "Hurricane Ian peak flood depth (job-0075)",
                            "layer_type": "raster",
                            "uri": REAL_WMS_URL,
                            "source_url": REAL_WMS_URL,
                            "style_preset": "continuous_flood_depth",
                            "visible": True,
                            "opacity": 0.9,
                            "z_index": 2,
                            "role": "primary",
                            "bbox": FORT_MYERS_BBOX,
                            "attribution": "GRACE-2 job-0076 diagnostic",
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
                log("[driver] injecting session-state with REAL WMS URL (flood-depth-job-0075-demo)...")
                page.evaluate(
                    "(payload) => window.__grace2InjectSessionState(payload)",
                    session_state,
                )

                page.wait_for_selector(
                    '[data-testid="grace2-layer-panel"]',
                    timeout=10_000,
                )
                log("[driver] layer panel mounted")

                log("[driver] zoom-to Fort Myers via map-command...")
                page.evaluate(
                    "(payload) => window.__grace2InjectMapCommand(payload)",
                    {"command": "zoom-to", "args": {"bbox": FORT_MYERS_BBOX}},
                )

                # Programmatically force zoom 13 (closer than fitBounds default).
                page.wait_for_timeout(3000)
                page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap ? window.__grace2GetMap() : null;
                        if (m) { m.jumpTo({center: [-81.83, 26.62], zoom: 13}); }
                    }"""
                )
                page.wait_for_timeout(5000)
                log("[driver] post-injection settled; capturing style spec")

                # Capture the actual style spec the map now has.
                style_info = page.evaluate(
                    """() => {
                        // Try to find any MapLibre map instance.
                        // The activeMap module-local is not directly exposed, but
                        // we attached __grace2GetMap above if available.
                        const m = window.__grace2GetMap ? window.__grace2GetMap() : null;
                        if (!m) return { error: 'no map instance available via window.__grace2GetMap' };
                        const style = m.getStyle();
                        return {
                            layers: style.layers.map(l => ({
                                id: l.id, type: l.type, source: l.source,
                                visibility: (l.layout && l.layout.visibility) || 'visible',
                                opacity: (l.paint && l.paint['raster-opacity']) ?? null,
                            })),
                            sources: Object.entries(style.sources).map(([id, s]) => ({
                                id, type: s.type, tiles: s.tiles || null,
                            })),
                            center: m.getCenter().toArray(),
                            zoom: m.getZoom(),
                            bounds: m.getBounds().toArray(),
                        };
                    }"""
                )
                log(f"[driver] STYLE SPEC: {json.dumps(style_info, indent=2)}")

                # Dump all WMS requests captured
                log(f"[driver] TOTAL WMS RESPONSES: {len(wms_requests)}")
                flood_responses = [r for r in wms_requests if "flood-depth" in r["url"]]
                basemap_responses = [r for r in wms_requests if "basemap-osm-conus" in r["url"]]
                log(f"[driver] flood-depth tile responses: {len(flood_responses)}")
                log(f"[driver] basemap tile responses: {len(basemap_responses)}")

                # Save a screenshot of current state
                out_path = EVIDENCE_DIR / "diagnose_screenshot.png"
                page.screenshot(path=str(out_path), full_page=False)
                log(f"[driver] saved {out_path}")

                # Save first flood tile URL verbatim
                if flood_responses:
                    log("[driver] FIRST FLOOD TILE URL (verbatim):")
                    log(flood_responses[0]["url"])
                else:
                    log("[driver] NO FLOOD TILE REQUESTS WERE EVER MADE")

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

    log("[driver] done.")
    log_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
