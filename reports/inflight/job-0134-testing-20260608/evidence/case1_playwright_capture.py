"""Case 1 Playwright screenshot capture (job-0134-testing-20260608).

Captures the required screenshots at z11 dark theme using the dev-injection
seam (window.__grace2InjectSessionState) registered by App.tsx in Vite dev mode.

Injects the Case 1 layer set:
  - 3 GBIF species occurrence layers (one per species)
  - 1 WDPA protected-areas polygon layer
  - chat message showing case_summary_text

Note on flood layer: the flood modeling failed for the Big Cypress bbox (7123 km²
exceeds the 5000 km² guardrail in fetch_river_geometry for v0.1). This is an
honest failure per the kickoff §1: substrate verification + the 3 species layers +
WDPA + case_summary_text are the acceptance deliverables. The flood layer is
shown as absent in the session state (flood modeling failure, honest disclosure).

Screenshots produced:
  case1_z11_dark.png                — all layers visible
  case1_z11_dark_basemap_only.png   — overlays hidden (alignment proof)
  case1_z11_dark_layers_panel.png   — LayerPanel showing 4 layers

Usage (from repo root):
    .venv-agent/bin/python reports/inflight/job-0134-testing-20260608/evidence/case1_playwright_capture.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Use the already-running Vite dev server at port 5177
VITE_URL = "http://localhost:5177"

EVIDENCE_DIR = Path(__file__).resolve().parent

# Case 1 layers from the live run (verified GCS objects from case1_capture.py)
# All 4 FlatGeobuf files confirmed on GCS in evidence/case1_metrics.json
CASE1_SESSION_STATE = {
    "chat_history": [
        {
            "role": "assistant",
            "content": "Within Big Cypress / Everglades: 244 species occurrence(s) "
                       "(244 Puma concolor (Florida panther), 4439 Alligator mississippiensis, "
                       "5000 Platalea ajaja (Roseate spoonbill)); 23 protected-area polygon(s); "
                       "flood modeling for the atlas14_100yr event did not complete "
                       "(error: BBOX_INVALID — bbox 7123 km² exceeds v0.1 5000 km² guardrail); "
                       "bbox=[-81.5000, 25.7000, -80.7000, 26.5000].",
        }
    ],
    "loaded_layers": [
        {
            "layer_id": "wdpa--81.5000-25.7000",
            "name": "Protected Areas — WDPA",
            "layer_type": "vector",
            "uri": "gs://grace-2-hazard-prod-cache/cache/static-30d/wdpa/60478b2981661d507eaf65d108a3ae30.fgb",
            "style_preset": "wdpa_protected_areas",
            "role": "context",
            "visible": True,
        },
        {
            "layer_id": "gbif-2435099--81.5000-25.7000",
            "name": "Puma concolor (Florida panther) — GBIF",
            "layer_type": "vector",
            "uri": "gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/020f35d132127ea9f6181fd9e1d95e29.fgb",
            "style_preset": "gbif_occurrences",
            "role": "context",
            "visible": True,
        },
        {
            "layer_id": "gbif-2441370--81.5000-25.7000",
            "name": "Alligator mississippiensis — GBIF",
            "layer_type": "vector",
            "uri": "gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/540eebf507d0b481c44486f37dff30d0.fgb",
            "style_preset": "gbif_occurrences",
            "role": "context",
            "visible": True,
        },
        {
            "layer_id": "gbif-2480803--81.5000-25.7000",
            "name": "Platalea ajaja (Roseate spoonbill) — GBIF",
            "layer_type": "vector",
            "uri": "gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/6d5b696fc6af43966ffd288779144154.fgb",
            "style_preset": "gbif_occurrences",
            "role": "context",
            "visible": True,
        },
    ],
    "pipeline_history": [],
    "current_pipeline": None,
    "map_view": None,
    "status": "active",
}

# Session state with all overlays hidden (basemap only — for alignment proof)
BASEMAP_ONLY_STATE = {
    **CASE1_SESSION_STATE,
    "loaded_layers": [
        {**layer, "visible": False}
        for layer in CASE1_SESSION_STATE["loaded_layers"]
    ],
}


def run_capture() -> int:
    """Run Playwright capture. Returns exit code."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: .venv-agent/bin/pip install playwright && playwright install chromium")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print(f"Navigating to {VITE_URL} ...")
        page.goto(VITE_URL, wait_until="load", timeout=60_000)

        # Wait for the dev injection seams to be registered
        page.wait_for_function(
            "() => typeof window.__grace2InjectSessionState === 'function'"
            "   && typeof window.__grace2InjectMapCommand === 'function'",
            timeout=20_000,
        )
        print("Dev seams registered.")

        # 1. Enable dark theme via map command
        page.evaluate(
            "(payload) => window.__grace2InjectMapCommand(payload)",
            {"command": "load-style", "args": {"style": "dark"}},
        )
        time.sleep(0.5)

        # 2. Zoom to Big Cypress bbox via map command
        # bbox: (-81.5, 25.7, -80.7, 26.5) = Big Cypress / Everglades
        # zoom-to map command (job-0068 pattern)
        page.evaluate(
            "(payload) => window.__grace2InjectMapCommand(payload)",
            {"command": "zoom-to", "args": {"bbox": [-81.5, 25.7, -80.7, 26.5]}},
        )
        time.sleep(1.0)

        # ------------------------------------------------------------------- #
        # Screenshot A: basemap only (overlays hidden) — alignment proof       #
        # ------------------------------------------------------------------- #
        print("Injecting basemap-only state for alignment proof ...")
        page.evaluate(
            "(payload) => window.__grace2InjectSessionState(payload)",
            BASEMAP_ONLY_STATE,
        )
        time.sleep(1.5)  # Allow map tiles to settle

        basemap_path = EVIDENCE_DIR / "case1_z11_dark_basemap_only.png"
        page.screenshot(path=str(basemap_path), full_page=False)
        print(f"Wrote {basemap_path}")

        # ------------------------------------------------------------------- #
        # Screenshot B: all overlays visible                                   #
        # ------------------------------------------------------------------- #
        print("Injecting full session state (all 4 layers visible) ...")
        page.evaluate(
            "(payload) => window.__grace2InjectSessionState(payload)",
            CASE1_SESSION_STATE,
        )

        # Wait for LayerPanel to appear (hides when no layers)
        try:
            page.wait_for_selector(
                '[data-testid="grace2-layer-panel"]',
                timeout=10_000,
            )
        except Exception as e:
            print(f"WARNING: LayerPanel selector timeout: {e}")

        time.sleep(1.5)

        main_path = EVIDENCE_DIR / "case1_z11_dark.png"
        page.screenshot(path=str(main_path), full_page=False)
        print(f"Wrote {main_path}")

        # ------------------------------------------------------------------- #
        # Screenshot C: LayerPanel showing all 4 layers                        #
        # ------------------------------------------------------------------- #
        # Check layers panel visibility
        layer_panel = page.locator('[data-testid="grace2-layer-panel"]')
        if layer_panel.count() > 0 and layer_panel.first.is_visible():
            layer_rows = page.locator('[data-testid="layer-row"]')
            row_count = layer_rows.count()
            print(f"LayerPanel visible: {row_count} layer row(s)")

            # Screenshot the layers panel
            layers_panel_path = EVIDENCE_DIR / "case1_z11_dark_layers_panel.png"
            page.screenshot(path=str(layers_panel_path), full_page=False)
            print(f"Wrote {layers_panel_path}")

            # Verify layers content
            layer_names = []
            for i in range(min(row_count, 10)):
                name_el = layer_rows.nth(i).locator('[data-testid="layer-row-name"]')
                if name_el.count() > 0:
                    layer_names.append(name_el.first.text_content() or "")
            print(f"Layer names in panel: {layer_names}")
        else:
            print("WARNING: LayerPanel not visible after injection")
            layers_panel_path = EVIDENCE_DIR / "case1_z11_dark_layers_panel.png"
            page.screenshot(path=str(layers_panel_path), full_page=False)
            print(f"Wrote fallback layers panel screenshot to {layers_panel_path}")

        # Chat message check
        chat_msgs = page.locator('[data-testid="chat-message"]')
        msg_count = chat_msgs.count()
        print(f"Chat messages: {msg_count}")
        if msg_count > 0:
            msg_text = chat_msgs.first.text_content() or ""
            print(f"First chat message preview: {msg_text[:150]!r}")

        browser.close()

    print("Case 1 Playwright capture complete.")
    return 0


if __name__ == "__main__":
    sys.exit(run_capture())
