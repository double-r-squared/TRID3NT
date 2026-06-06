"""LayerPanel component tests driving the dev seam.

Exit-criterion mapping (sprint-05.md):

* EC2 ("LayerPanel renders loaded_layers from a session-state envelope with
  visibility checkbox, 0..1 opacity slider, drag-and-drop reorder; name +
  attribution per row; updates on map-command envelopes").

Boundary: the agent does not yet emit populated ``session-state.loaded_layers``
in M3 (M4 work). We inject the seeded envelope through the in-page dev seam
``window.__grace2InjectSessionState`` that App.tsx exposes under
``import.meta.env.DEV``. Surfaced as Open Question per testing.md.

Parametrized across Chromium + Firefox-ESR — visual smoke #2 per kickoff
§Scope item 4 cross-browser clarification. The FR-WC-1 cross-browser
acceptance for the layout shell + LayerPanel rendering depends on both
engines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.live_web
def test_layer_panel_renders_from_session_state(
    vite_dev_server: str,
    browser,  # parametrized across chromium + firefox via conftest.py
    browser_name: str,
    m3_artifacts_dir: Path,
) -> None:
    """Inject a 2-layer session-state via the dev seam; assert two panel rows
    render, each with visibility checkbox + opacity slider + drag handle +
    name. Then inject a map-command (set-layer-visibility) and assert the
    target row's checkbox flipped.
    """
    seeded = json.loads((FIXTURES / "session_state_seeded.json").read_text())

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    page.goto(vite_dev_server, wait_until="load", timeout=60000)

    # The dev seam attaches asynchronously inside a useEffect — wait for it.
    page.wait_for_function(
        "() => typeof window.__grace2InjectSessionState === 'function'",
        timeout=10000,
    )

    page.evaluate(
        "(payload) => window.__grace2InjectSessionState(payload)",
        seeded,
    )

    # Wait for the panel rows to materialize.
    page.wait_for_selector(
        '[data-testid="grace2-layer-panel"] [data-testid="layer-row"]',
        timeout=10000,
    )

    rows = page.locator('[data-testid="layer-row"]')
    row_count = rows.count()
    assert row_count == 2, (
        f"layer=web client (LayerPanel.tsx): expected 2 rendered rows from "
        f"the seeded 2-layer session-state, observed {row_count}. The "
        f"dev-seam injection or the reducer's session-state action is "
        f"broken."
    )

    # Each row must have: drag handle + visibility checkbox + opacity slider.
    drag_handles = page.locator('[data-testid="layer-drag-handle"]')
    vis_boxes = page.locator('[data-testid="layer-visibility"]')
    opacity_sliders = page.locator('[data-testid="layer-opacity"]')

    assert drag_handles.count() == 2, (
        f"layer=web client (LayerPanel.tsx FR-WC-4): expected 2 drag handles "
        f"(one per row), observed {drag_handles.count()}. Drag-and-drop "
        f"reorder is mandatory in FR-WC-4 v0.1 scope."
    )
    assert vis_boxes.count() == 2, (
        f"layer=web client (LayerPanel.tsx FR-WC-4): expected 2 visibility "
        f"checkboxes, observed {vis_boxes.count()}."
    )
    assert opacity_sliders.count() == 2, (
        f"layer=web client (LayerPanel.tsx FR-WC-4): expected 2 opacity "
        f"sliders, observed {opacity_sliders.count()}."
    )

    # Both names + at least one attribution rendered (kickoff: name +
    # attribution per row).
    panel_text = page.locator('[data-testid="grace2-layer-panel"]').text_content() or ""
    assert "Storm-surge max depth" in panel_text, (
        f"layer=web client (LayerPanel.tsx): seeded layer name "
        f"'Storm-surge max depth' missing from rendered panel text. "
        f"Panel text head: {panel_text[:300]!r}"
    )
    assert "Basemap OSM CONUS" in panel_text, (
        f"layer=web client (LayerPanel.tsx): seeded layer name "
        f"'Basemap OSM CONUS' missing from rendered panel text."
    )
    assert "OpenStreetMap" in panel_text, (
        f"layer=web client (LayerPanel.tsx): the seeded OSM attribution "
        f"must render in the row."
    )

    # Both seeded layers visible:true → both checkboxes should be checked.
    for i in range(2):
        assert vis_boxes.nth(i).is_checked(), (
            f"layer=web client (LayerPanel.tsx): visibility checkbox row "
            f"{i} expected checked from seed; observed unchecked. Reducer "
            f"may be mis-mapping ProjectLayerSummary.visible."
        )

    # Inject a map-command set-layer-visibility=false for the top layer (the
    # storm-surge layer has z_index=2 which is sorted top-of-stack-first).
    top_layer_id = seeded["loaded_layers"][0]["layer_id"]
    page.evaluate(
        "(p) => window.__grace2InjectMapCommand(p)",
        {
            "command": "set-layer-visibility",
            "layer_id": top_layer_id,
            "visible": False,
        },
    )
    # Top-of-stack-first means the row with z_index=2 (Storm-surge) is row 0.
    # 10s timeout is generous for Firefox's slower React reducer dispatch.
    page.wait_for_function(
        "(testid) => {"
        "  const rows = document.querySelectorAll('[data-testid=\"layer-row\"]');"
        "  if (rows.length < 1) return false;"
        "  const cb = rows[0].querySelector('[data-testid=\"layer-visibility\"]');"
        "  return cb && !cb.checked;"
        "}",
        arg="layer-visibility",
        timeout=10000,
    )

    # Final screenshot — committed as canonical evidence (per-browser).
    out_png = m3_artifacts_dir / f"layer-panel-{browser_name}.png"
    page.screenshot(path=str(out_png), full_page=False)

    context.close()
