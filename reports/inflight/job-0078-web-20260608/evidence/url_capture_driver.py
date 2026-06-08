"""URL-capture Playwright driver — job-0078 Part 1 diagnosis.

Goal: compare flood-layer tile URLs vs basemap tile URLs at the SAME MapLibre
tile coordinates. If the BBOX values differ, that's the alignment bug. If
they're identical, the bug is on the server side.

Output:
- evidence/url_capture.log — full per-request log
- evidence/url_pairs.json — flood/basemap URL pairs at the same Mercator tile
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
FORT_MYERS_CENTER = [-81.83, 26.62]


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
        except Exception:
            time.sleep(0.3)
    return False


def _parse_wms_bbox(url: str) -> tuple[str, str] | None:
    """Return (layer_name, bbox_string) from a WMS GetMap URL, or None."""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        layers = qs.get("LAYERS", [""])[0]
        bbox = qs.get("BBOX", [""])[0]
        if not layers or not bbox:
            return None
        return layers, bbox
    except Exception:
        return None


def main() -> int:
    from playwright.sync_api import sync_playwright

    log_path = EVIDENCE_DIR / "url_capture.log"
    pairs_path = EVIDENCE_DIR / "url_pairs.json"
    log_file = log_path.open("w")

    def log(line: str) -> None:
        print(line)
        log_file.write(line + "\n")
        log_file.flush()

    port = _free_port()
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    log(f"[driver] launching Vite dev server: {' '.join(cmd)}")
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

    wms_urls: list[str] = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()

                def on_request(req):
                    if "qgis-server" in req.url.lower() and "GetMap" in req.url:
                        wms_urls.append(req.url)

                page.on("request", on_request)

                page.goto(base_url, wait_until="load", timeout=60_000)
                log("[driver] page loaded")

                page.wait_for_function(
                    "() => typeof window.__grace2InjectSessionState === 'function'"
                    "   && typeof window.__grace2InjectMapCommand === 'function'",
                    timeout=15_000,
                )
                log("[driver] dev-injection seam present")
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
                            "attribution": "GRACE-2 job-0078 diagnostic",
                            "temporal": None,
                        }
                    ],
                    "pipeline_history": [],
                    "current_pipeline": None,
                    "map_view": {
                        "center": FORT_MYERS_CENTER,
                        "zoom": 13,
                        "bearing": 0,
                        "pitch": 0,
                    },
                }
                log("[driver] injecting session-state...")
                page.evaluate(
                    "(payload) => window.__grace2InjectSessionState(payload)",
                    session_state,
                )

                page.wait_for_selector(
                    '[data-testid="grace2-layer-panel"]',
                    timeout=10_000,
                )
                log("[driver] layer panel mounted")

                # Programmatically jump to zoom 13 at Fort Myers and wait
                # for tiles
                page.wait_for_timeout(2000)
                page.evaluate(
                    """(c) => {
                        const m = window.__grace2GetMap ? window.__grace2GetMap() : null;
                        if (m) { m.jumpTo({center: c, zoom: 13}); }
                    }""",
                    FORT_MYERS_CENTER,
                )
                page.wait_for_timeout(8000)
                log(f"[driver] settled; {len(wms_urls)} total WMS GetMap URLs captured")

                # Dump style spec
                style_info = page.evaluate(
                    """() => {
                        const m = window.__grace2GetMap ? window.__grace2GetMap() : null;
                        if (!m) return { error: 'no map' };
                        const style = m.getStyle();
                        return {
                            layer_ids: style.layers.map(l => l.id),
                            sources: Object.fromEntries(Object.entries(style.sources).map(
                                ([id, s]) => [id, {type: s.type, tiles: s.tiles, tileSize: s.tileSize, bounds: s.bounds}]
                            )),
                            center: m.getCenter().toArray(),
                            zoom: m.getZoom(),
                            bounds: m.getBounds().toArray(),
                        };
                    }"""
                )
                log(f"[driver] STYLE SPEC: {json.dumps(style_info, indent=2)}")

                # Categorise
                flood_urls = [u for u in wms_urls if "flood-depth" in u]
                basemap_urls = [u for u in wms_urls if "basemap-osm-conus" in u]
                log(f"[driver] flood URLs: {len(flood_urls)}; basemap URLs: {len(basemap_urls)}")

                if flood_urls:
                    log("[driver] FIRST FLOOD URL:")
                    log(flood_urls[0])
                if basemap_urls:
                    log("[driver] FIRST BASEMAP URL:")
                    log(basemap_urls[0])

                # Parse bboxes and group flood vs basemap by bbox
                flood_bboxes = set()
                basemap_bboxes = set()
                for u in flood_urls:
                    p = _parse_wms_bbox(u)
                    if p:
                        flood_bboxes.add(p[1])
                for u in basemap_urls:
                    p = _parse_wms_bbox(u)
                    if p:
                        basemap_bboxes.add(p[1])

                log(f"[driver] distinct flood BBOXes: {len(flood_bboxes)}")
                log(f"[driver] distinct basemap BBOXes: {len(basemap_bboxes)}")
                log(f"[driver] BBOXes in flood NOT in basemap: {len(flood_bboxes - basemap_bboxes)}")
                log(f"[driver] BBOXes in basemap NOT in flood: {len(basemap_bboxes - flood_bboxes)}")
                log(f"[driver] BBOXes in BOTH: {len(flood_bboxes & basemap_bboxes)}")

                # Save URL pairs for any matched bbox
                pairs = []
                for bbox in (flood_bboxes & basemap_bboxes):
                    f_url = next((u for u in flood_urls if _parse_wms_bbox(u) and _parse_wms_bbox(u)[1] == bbox), None)
                    b_url = next((u for u in basemap_urls if _parse_wms_bbox(u) and _parse_wms_bbox(u)[1] == bbox), None)
                    if f_url and b_url:
                        pairs.append({"bbox": bbox, "flood": f_url, "basemap": b_url})

                # Also save lists for inspection
                pairs_data = {
                    "style_spec": style_info,
                    "flood_url_count": len(flood_urls),
                    "basemap_url_count": len(basemap_urls),
                    "flood_bbox_count": len(flood_bboxes),
                    "basemap_bbox_count": len(basemap_bboxes),
                    "matched_bbox_pairs": pairs[:10],
                    "sample_flood_url": flood_urls[0] if flood_urls else None,
                    "sample_basemap_url": basemap_urls[0] if basemap_urls else None,
                    "all_flood_bboxes": list(flood_bboxes),
                    "all_basemap_bboxes": list(basemap_bboxes),
                }
                with pairs_path.open("w") as f:
                    json.dump(pairs_data, f, indent=2)
                log(f"[driver] wrote {pairs_path}")

                # Screenshot current state for visual reference
                out_path = EVIDENCE_DIR / "capture_zoom13.png"
                page.screenshot(path=str(out_path), full_page=False)
                log(f"[driver] saved {out_path}")

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
                except Exception:
                    pass

    log("[driver] done.")
    log_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
