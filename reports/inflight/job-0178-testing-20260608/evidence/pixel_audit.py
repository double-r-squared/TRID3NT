"""job-0178 — Pixel audit of each scenario's map_overlay vs map_basemap.

The driver's coarse delta-pixel calc was misleading because (a) successive
scenarios accumulated layers on the same map view rather than starting clean,
and (b) the dark left-panel area dominated low-light counts.

This script does a cleaner per-scenario delta: crop to the visible map area
(skipping the 90-px LayerPanel on the left), and compare basemap vs overlay
strictly within the map region. Records per-channel signatures that
correspond to each expected overlay's coloration.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from PIL import Image

OUT_DIR = Path(__file__).parent

# Map element bounding box per Map.tsx, measured from the headline screenshots:
# Left rail ~225px, map starts at x=225 in a 1600x1000 viewport when both
# panels open. But the map screenshot is taken with .locator('grace2-map'),
# which clips to the map div directly. So sampling all pixels in those
# screenshots is fine — the map element itself IS just the map.
#
# However the map screenshot is 600×375 (or similar). Let's verify.

SCENARIOS = [
    {
        "key": "01_radar_america",
        "name": "S1 — Radar over America",
        "expects": "raster_overlay (NEXRAD green/yellow/red)",
    },
    {
        "key": "02_alerts_america",
        "name": "S2 — Weather alerts CONUS",
        "expects": "vector_polygon (NWS yellow/red transparent fills)",
    },
    {
        "key": "03_wdpa_big_cypress",
        "name": "S3 — WDPA Big Cypress",
        "expects": "vector_polygon (WDPA green outlines)",
    },
    {
        "key": "04_roads_fort_myers",
        "name": "S4 — OSM roads Fort Myers",
        "expects": "vector_linestring (OSM red/orange road lines)",
    },
    {
        "key": "05_retry_failure",
        "name": "S5 — Retry path WDPA Florida",
        "expects": "vector_polygon (WDPA retry success) + retry chain in chat",
    },
]


def classify_pixels(img: Image.Image) -> dict:
    """Tally pixel categories that map to overlay signatures."""
    px = img.load()
    w, h = img.size
    cnt = dict(
        total=0,
        radar_green=0,  # NEXRAD low reflectivity
        radar_yellow=0,
        radar_warm=0,  # red/orange
        road_red_orange=0,  # OSM road raster lines
        alert_polygon=0,  # transparent fills
        wdpa_green_outline=0,
        basemap_landgreen=0,  # OSM tile land color
        basemap_water=0,
        dark=0,
        bright_chroma=0,  # any high-saturation overlay pixel
    )
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            cnt["total"] += 1
            mx = max(r, g, b); mn = min(r, g, b); chroma = mx - mn
            # Dark UI panel
            if mx < 40:
                cnt["dark"] += 1
                continue
            # NEXRAD radar palette (lots of saturated greens/yellows/reds)
            if g > 180 and r < 150 and b < 150:
                cnt["radar_green"] += 1
                cnt["bright_chroma"] += 1
            elif r > 200 and g > 180 and b < 130:
                cnt["radar_yellow"] += 1
                cnt["bright_chroma"] += 1
            elif r > 200 and g < 150 and b < 150:
                cnt["radar_warm"] += 1
                cnt["bright_chroma"] += 1
                if g > 80 and g < 180:
                    cnt["road_red_orange"] += 1  # OSM road also matches
            elif chroma > 80:
                cnt["bright_chroma"] += 1
            # OSM basemap land (light tan / cream)
            if 220 < r < 250 and 215 < g < 245 and 195 < b < 230 and chroma < 40:
                cnt["basemap_landgreen"] += 1
            # OSM water (light blue)
            if 160 < r < 200 and 200 < g < 240 and 220 < b < 255:
                cnt["basemap_water"] += 1
            # WDPA / park polygon overlay (semi-transparent green)
            if 100 < g < 200 and r < g and b < g and chroma > 30 and not (g > 180 and r < 150):
                cnt["wdpa_green_outline"] += 1
    return cnt


def main():
    rows = []
    for sc in SCENARIOS:
        base_p = OUT_DIR / f"{sc['key']}_map_basemap.png"
        over_p = OUT_DIR / f"{sc['key']}_map_overlay.png"
        if not base_p.exists() or not over_p.exists():
            rows.append({
                "name": sc["name"],
                "status": "MISSING",
                "base": base_p.exists(),
                "over": over_p.exists(),
            })
            continue
        base_img = Image.open(base_p).convert("RGB")
        over_img = Image.open(over_p).convert("RGB")
        size = base_img.size
        base = classify_pixels(base_img)
        over = classify_pixels(over_img)
        # Deltas — positive means MORE of that signature in overlay vs baseline.
        deltas = {k: over.get(k, 0) - base.get(k, 0) for k in base.keys()}

        # Verdict: any positive Δ in the expected signature classes counts.
        signature = {
            "01_radar_america": ["radar_green", "radar_yellow", "radar_warm"],
            "02_alerts_america": ["radar_green", "radar_yellow", "bright_chroma"],
            "03_wdpa_big_cypress": ["wdpa_green_outline", "bright_chroma"],
            "04_roads_fort_myers": ["road_red_orange", "radar_warm", "bright_chroma"],
            "05_retry_failure": ["wdpa_green_outline", "bright_chroma"],
        }[sc["key"]]
        sig_delta = sum(deltas.get(k, 0) for k in signature)
        rendered = sig_delta > 200

        rows.append({
            "name": sc["name"],
            "expects": sc["expects"],
            "image_size": list(size),
            "baseline": base,
            "with_overlay": over,
            "deltas": deltas,
            "signature_keys": signature,
            "signature_delta": sig_delta,
            "rendered": rendered,
        })

    (OUT_DIR / "pixel_audit.json").write_text(json.dumps(rows, indent=2))

    print("\nPixel-audit results (per scenario):\n")
    for r in rows:
        if r.get("status") == "MISSING":
            print(f"  [MISSING] {r['name']}")
            continue
        verdict = "RENDERED" if r["rendered"] else "NO-CHANGE"
        print(f"  [{verdict}] {r['name']}")
        print(f"    expects: {r['expects']}")
        print(f"    signature: {r['signature_keys']} Δ={r['signature_delta']}")
        print(f"    notable Δs: " + ", ".join(
            f"{k}={v}" for k, v in r["deltas"].items() if abs(v) > 100
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
