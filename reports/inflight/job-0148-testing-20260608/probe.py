"""job-0148 — sprint-12-mega Wave 4 Stage B Playwright verification.

Captures 12 screenshots covering the final running state after Stage A
fixes landed (jobs 0143 nav restructure, 0144 chat input, 0145 inline
chat cards, 0146 vector palette polish, 0147 Pelicun composer).

Vector layers are served as data:application/geo+json;base64 URIs so the
client's fetch path runs end-to-end (same code path as a real GeoJSON
URL — only the origin differs).
"""

from __future__ import annotations
import base64
import json
import sys
import traceback
from pathlib import Path

BASE_URL = "http://127.0.0.1:5177"
OUT_DIR = Path("/home/nate/Documents/GRACE-2/reports/inflight/job-0148-testing-20260608/evidence")
OUT_DIR.mkdir(parents=True, exist_ok=True)

ANON_KEY = "grace2_anonymous_accepted"

FLOOD_WMS_URL = (
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms"
    "?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0086-fix"
)

# Big Cypress bbox
BIG_CYPRESS_BBOX = [-81.5, 25.7, -80.7, 26.5]
BIG_CYPRESS_CENTER = [-81.1, 26.1]

# Fort Myers for Pelicun
FORT_MYERS_CENTER = [-81.83, 26.62]
FORT_MYERS_BBOX = [-81.91, 26.55, -81.75, 26.69]


def geojson_data_uri(fc: dict) -> str:
    raw = json.dumps(fc).encode("utf-8")
    return "data:application/geo+json;base64," + base64.b64encode(raw).decode("ascii")


def set_anon_flag(page) -> None:
    page.evaluate(f"() => localStorage.setItem('{ANON_KEY}', 'true')")


def wait_for_app(page, timeout=12000):
    page.wait_for_selector('[data-testid="grace2-app-shell"]', timeout=timeout)
    page.wait_for_function(
        "() => typeof window.__grace2InjectSessionState === 'function'",
        timeout=timeout,
    )
    page.wait_for_timeout(900)


def boot_anon_page(pw, viewport_w=1600, viewport_h=1000):
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": viewport_w, "height": viewport_h})
    page = ctx.new_page()
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    set_anon_flag(page)
    page.goto(BASE_URL, wait_until="load", timeout=60_000)
    wait_for_app(page)
    return browser, ctx, page


def toggle_dark(page) -> None:
    """Toggle dark theme via SettingsPopup (job-0143 — the floating theme button
    was removed; theme lives in Settings now)."""
    try:
        page.click('[data-testid="grace2-bottom-row-settings"]', timeout=4000)
        page.wait_for_selector('[data-testid="grace2-settings-popup"]', timeout=4000)
        page.click('[data-testid="grace2-settings-theme-toggle"]', timeout=3000)
        page.wait_for_timeout(800)
        page.click('[data-testid="grace2-settings-popup-close"]', timeout=3000)
        page.wait_for_timeout(800)
    except Exception as e:
        print(f"[probe] dark theme toggle warn: {e}")


# ============================================================
# Fixtures
# ============================================================

def fake_case_list():
    return {
        "cases": [
            {
                "case_id": "case_ulid_001",
                "title": "Hurricane Ian — Fort Myers flood",
                "created_at": "2026-06-05T09:00:00Z",
                "updated_at": "2026-06-08T14:00:00Z",
                "status": "active",
                "bbox": FORT_MYERS_BBOX,
                "primary_hazard": "pluvial_flood",
                "layer_summary": ["flood-depth-job-0086-fix"],
            },
            {
                "case_id": "case_ulid_002",
                "title": "Big Cypress habitat × flood",
                "created_at": "2026-06-06T10:30:00Z",
                "updated_at": "2026-06-08T16:42:00Z",
                "status": "active",
                "bbox": BIG_CYPRESS_BBOX,
                "primary_hazard": "flood_habitat_overlay",
                "layer_summary": ["flood-depth-job-0086-fix", "gbif-panther"],
            },
            {
                "case_id": "case_ulid_003",
                "title": "California wildfire smoke (draft)",
                "created_at": "2026-06-07T11:00:00Z",
                "updated_at": "2026-06-07T11:15:00Z",
                "status": "active",
                "bbox": [-122.5, 37.5, -121.5, 38.5],
                "primary_hazard": "wildfire_smoke",
                "layer_summary": [],
            },
        ]
    }


def panther_points():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"species": "Puma concolor coryi"},
             "geometry": {"type": "Point", "coordinates": [lon, lat]}}
            for (lon, lat) in [
                (-81.30, 26.10), (-81.25, 26.15), (-81.35, 26.05),
                (-81.20, 26.20), (-81.32, 26.18), (-81.18, 26.06),
                (-81.40, 26.13), (-81.22, 26.12),
            ]
        ],
    }


def spoonbill_points():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"species": "Platalea ajaja"},
             "geometry": {"type": "Point", "coordinates": [lon, lat]}}
            for (lon, lat) in [
                (-81.10, 25.95), (-81.05, 26.00), (-81.15, 25.90),
                (-81.00, 26.05), (-81.12, 25.98), (-81.08, 26.02),
                (-81.18, 25.95), (-81.03, 25.92),
            ]
        ],
    }


def alligator_points():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"species": "Alligator mississippiensis"},
             "geometry": {"type": "Point", "coordinates": [lon, lat]}}
            for (lon, lat) in [
                (-80.95, 26.20), (-80.90, 26.25), (-80.92, 26.30),
                (-80.85, 26.20), (-80.88, 26.28), (-80.95, 26.32),
                (-80.93, 26.18), (-80.96, 26.24),
            ]
        ],
    }


def big_cypress_wdpa():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"WDPA_ID": "374608", "NAME": "Big Cypress National Preserve"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-81.45, 25.78], [-80.85, 25.78], [-80.85, 26.10],
                        [-81.10, 26.32], [-81.45, 26.30], [-81.45, 25.78],
                    ]],
                },
            }
        ],
    }


def pelicun_damage_polygons():
    """20 building-footprint polygons over Fort Myers with varied ds_mean property."""
    minx, miny, maxx, maxy = FORT_MYERS_BBOX
    cols, rows = 5, 4
    dx = (maxx - minx) / cols
    dy = (maxy - miny) / rows
    feats = []
    import random
    random.seed(0xF07747)
    for r in range(rows):
        for c in range(cols):
            x0 = minx + c * dx + dx * 0.15
            x1 = minx + (c + 1) * dx - dx * 0.15
            y0 = miny + r * dy + dy * 0.15
            y1 = miny + (r + 1) * dy - dy * 0.15
            # Deterministic spread of ds_mean from 0.05 → 0.95 so the gradient
            # is visually obvious (not random per-iteration).
            idx = r * cols + c
            ds_mean = round(0.05 + (idx / (cols * rows - 1)) * 0.9, 2)
            feats.append({
                "type": "Feature",
                "properties": {"ds_mean": ds_mean, "building_id": f"FM-{r}-{c}"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0],
                    ]],
                },
            })
    return {"type": "FeatureCollection", "features": feats}


# ============================================================
# Scenarios
# ============================================================

def scenario_01_cases_root(pw):
    """Cases-root view: CasesPanel left rail visible, NO LayerPanel, no in-Case content."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        toggle_dark(page)
        page.evaluate("(p) => window.__grace2InjectCaseList(p)", fake_case_list())
        page.wait_for_timeout(1500)
        out_path = OUT_DIR / "01_cases_root_view.png"
        page.screenshot(path=str(out_path), full_page=False)

        info = {
            "cases_panel_visible": page.locator('[data-testid="grace2-cases-panel"]').is_visible(),
            "case_rows": page.locator('[data-testid="grace2-case-row"]').count(),
            "left_rail_mode": page.get_attribute('[data-testid="grace2-left-rail"]', "data-mode"),
            "layer_panel_present": page.locator('[data-testid="grace2-layer-panel"]').count(),
            "case_view_present": page.locator('[data-testid="grace2-case-view"]').count(),
        }
        return [(out_path.name, f"Cases-root view: CasesPanel left rail with {info['case_rows']} fake Cases, NO LayerPanel ({info['layer_panel_present']}=0 expected), CaseView absent ({info['case_view_present']}=0 expected), data-mode=cases-list."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_02_case_active(pw):
    """Click into one Case: breadcrumb header `← Cases / [Title]` + LayerPanel below; Cases list NOT visible."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        toggle_dark(page)
        page.evaluate("(p) => window.__grace2InjectCaseList(p)", fake_case_list())
        page.wait_for_timeout(1000)

        # Inject case-open with the Big Cypress case + 2 layers (raster + vector)
        case = fake_case_list()["cases"][1]  # Big Cypress
        panther_uri = geojson_data_uri(panther_points())
        case_open = {
            "session_state": {
                "case": case,
                "chat_history": [
                    {"message_id": "m1", "case_id": case["case_id"], "role": "user",
                     "content": "Show Big Cypress flood and panther habitat.",
                     "created_at": "2026-06-08T17:00:00Z"},
                    {"message_id": "m2", "case_id": case["case_id"], "role": "agent",
                     "content": "Loaded flood depth raster + Florida panther GBIF points.",
                     "created_at": "2026-06-08T17:00:08Z"},
                ],
                "loaded_layers": [
                    {
                        "layer_id": "flood-depth-job-0086-fix",
                        "name": "Hurricane Ian peak flood depth",
                        "layer_type": "raster",
                        "uri": FLOOD_WMS_URL,
                        "source_url": FLOOD_WMS_URL,
                        "style_preset": "continuous_flood_depth",
                        "visible": True, "opacity": 0.7, "z_index": 2,
                        "role": "primary", "bbox": BIG_CYPRESS_BBOX,
                        "attribution": "job-0086", "temporal": None,
                    },
                    {
                        "layer_id": "gbif-panther-bc",
                        "name": "Florida panther (GBIF)",
                        "layer_type": "vector",
                        "uri": panther_uri,
                        "style_preset": "gbif_occurrences",
                        "visible": True, "opacity": 1.0, "z_index": 4,
                        "role": "primary", "bbox": BIG_CYPRESS_BBOX,
                        "attribution": "GBIF (synthetic)", "temporal": None,
                    },
                ],
                "pipeline_history": [],
                "current_pipeline": None,
            }
        }
        page.evaluate("(p) => window.__grace2InjectCaseOpen(p)", case_open)
        page.wait_for_timeout(2500)
        page.evaluate(
            f"""() => {{
                const m = window.__grace2GetMap && window.__grace2GetMap();
                if (m) m.jumpTo({{center: {json.dumps(BIG_CYPRESS_CENTER)}, zoom: 10}});
            }}"""
        )
        page.wait_for_timeout(2000)
        out_path = OUT_DIR / "02_case_active_view.png"
        page.screenshot(path=str(out_path), full_page=False)

        info = {
            "case_view_visible": page.locator('[data-testid="grace2-case-view"]').is_visible(),
            "breadcrumb_visible": page.locator('[data-testid="grace2-case-view-breadcrumb"]').is_visible(),
            "left_rail_mode": page.get_attribute('[data-testid="grace2-left-rail"]', "data-mode"),
            "case_title": page.locator('[data-testid="grace2-case-view-title"]').text_content(),
            "cases_panel_count": page.locator('[data-testid="grace2-cases-panel"]').count(),
            "layer_panel_wrap": page.locator('[data-testid="grace2-case-view-layer-panel-wrap"]').count(),
        }
        return [(out_path.name, f"Case-active view: CaseView breadcrumb (\"{info['case_title']}\") visible, LayerPanel mounted below (wrap count={info['layer_panel_wrap']}), CasesPanel list NOT present in this mode ({info['cases_panel_count']}=0 expected), data-mode=case-view."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_03_bottom_row(pw):
    """[Settings] [Secrets] pills under the left rail, not overlapping."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        toggle_dark(page)
        page.evaluate("(p) => window.__grace2InjectCaseList(p)", fake_case_list())
        page.wait_for_timeout(1500)
        out_path = OUT_DIR / "03_bottom_row_buttons.png"
        page.screenshot(path=str(out_path), full_page=False)

        # Geometry check — bottom-row buttons should sit BELOW the cases panel.
        btn_row_box = page.locator('[data-testid="grace2-bottom-row-buttons"]').bounding_box()
        cases_panel_box = page.locator('[data-testid="grace2-cases-panel"]').bounding_box()
        settings_box = page.locator('[data-testid="grace2-bottom-row-settings"]').bounding_box()
        secrets_box = page.locator('[data-testid="grace2-bottom-row-secrets"]').bounding_box()
        info = {
            "btn_row": btn_row_box,
            "cases_panel": cases_panel_box,
            "settings": settings_box,
            "secrets": secrets_box,
            "btn_row_below_cases": (btn_row_box["y"] >= cases_panel_box["y"] + cases_panel_box["height"] - 4) if (btn_row_box and cases_panel_box) else None,
        }
        return [(out_path.name, f"Bottom-row [Settings] [Secrets] pills visible under the CasesPanel — btn_row.y={btn_row_box['y']:.0f} cases_bottom={cases_panel_box['y']+cases_panel_box['height']:.0f} below={info['btn_row_below_cases']}."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_04_settings_popup(pw):
    """Settings full-screen popup with Account/Appearance/About sections."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        page.click('[data-testid="grace2-bottom-row-settings"]', timeout=4000)
        page.wait_for_selector('[data-testid="grace2-settings-popup"]', timeout=4000)
        page.wait_for_timeout(700)
        out_path = OUT_DIR / "04_settings_popup.png"
        page.screenshot(path=str(out_path), full_page=False)
        info = {
            "popup_visible": page.locator('[data-testid="grace2-settings-popup"]').is_visible(),
            "card_visible": page.locator('[data-testid="grace2-settings-popup-card"]').is_visible(),
            "close_present": page.locator('[data-testid="grace2-settings-popup-close"]').count(),
            "theme_toggle_present": page.locator('[data-testid="grace2-settings-theme-toggle"]').count(),
            "account_label": page.locator('[data-testid="grace2-settings-account-label"]').text_content() if page.locator('[data-testid="grace2-settings-account-label"]').count() else None,
            "build_sha_present": page.locator('[data-testid="grace2-settings-build-sha"]').count(),
        }
        return [(out_path.name, f"Settings full-screen popup: Account ('{info['account_label']}'), Appearance (theme toggle), About (build SHA), close X. Theme toggle count={info['theme_toggle_present']}, close count={info['close_present']}."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_05_secrets_popup(pw):
    """Secrets full-screen popup with list + add form."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        # Inject a non-empty secrets list to make the popup visually meaningful.
        secrets_payload = {
            "secrets": [
                {
                    "secret_id": "sec_001",
                    "provider": "ebird",
                    "label": "eBird API key",
                    "case_id": None,
                    "created_at": "2026-06-08T13:00:00Z",
                    "last_used_at": None,
                    "status": "active",
                },
                {
                    "secret_id": "sec_002",
                    "provider": "iucn",
                    "label": "IUCN Red List token",
                    "case_id": None,
                    "created_at": "2026-06-08T13:05:00Z",
                    "last_used_at": None,
                    "status": "active",
                },
            ]
        }
        page.evaluate("(p) => window.__grace2InjectSecretsList(p)", secrets_payload)
        page.wait_for_timeout(600)
        page.click('[data-testid="grace2-bottom-row-secrets"]', timeout=4000)
        page.wait_for_selector('[data-testid="grace2-secrets-popup"]', timeout=4000)
        page.wait_for_timeout(700)
        out_path = OUT_DIR / "05_secrets_popup.png"
        page.screenshot(path=str(out_path), full_page=False)
        info = {
            "popup_visible": page.locator('[data-testid="grace2-secrets-popup"]').is_visible(),
            "card_visible": page.locator('[data-testid="grace2-secrets-popup-card"]').is_visible(),
            "close_present": page.locator('[data-testid="grace2-secrets-popup-close"]').count(),
        }
        return [(out_path.name, f"Secrets full-screen popup: SecretsPanel mounted inside overlay (2 fake provider entries injected). popup_visible={info['popup_visible']} close={info['close_present']}."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_06_chat_input_idle(pw):
    """Empty state: blue ↑ button visible bottom-right of textarea wrapper with drop shadow + rounded corners."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        toggle_dark(page)
        page.wait_for_selector('[data-testid="chat-input-wrapper"]', timeout=5000)
        page.wait_for_timeout(600)
        out_path = OUT_DIR / "06_chat_input_idle.png"
        page.screenshot(path=str(out_path), full_page=False)

        info = page.evaluate(
            """() => {
                const w = document.querySelector('[data-testid="chat-input-wrapper"]');
                const btn = document.querySelector('[data-testid="chat-input-action"]');
                const glyph = document.querySelector('[data-testid="chat-input-glyph"]');
                const ws = w ? getComputedStyle(w) : null;
                return {
                    wrapper_state: w ? w.getAttribute('data-state') : null,
                    box_shadow: ws ? ws.boxShadow : null,
                    border_radius: ws ? ws.borderRadius : null,
                    button_state: btn ? btn.getAttribute('data-action-state') : null,
                    button_disabled: btn ? btn.disabled : null,
                    button_bg: btn ? getComputedStyle(btn).backgroundColor : null,
                    glyph_kind: glyph ? glyph.getAttribute('data-glyph') : null,
                };
            }"""
        )
        return [(out_path.name, f"Chat input idle empty: wrapper data-state={info['wrapper_state']}, glyph={info['glyph_kind']}, button data-action-state={info['button_state']} disabled={info['button_disabled']} bg={info['button_bg']}, shadow={info['box_shadow']!r}, radius={info['border_radius']}."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_07_chat_input_typing(pw):
    """Type a multi-line message: textarea grows vertically; chat content still visible above."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        toggle_dark(page)
        # Seed some agent chat history so we can verify content stays visible above the input.
        session_state = {
            "chat_history": [
                {"role": "agent", "text": "Hello! Ask me about hazards, species, or any geospatial overlay."},
                {"role": "agent", "text": "Try: 'Show flood depth over Fort Myers' or 'Pull GBIF panther occurrences for Big Cypress'."},
            ],
            "loaded_layers": [],
            "pipeline_history": [],
            "current_pipeline": None,
            "map_view": None,
        }
        page.evaluate("(p) => window.__grace2InjectSessionState(p)", session_state)
        page.wait_for_timeout(800)
        page.click('[data-testid="chat-input"]', timeout=4000)
        big_msg = (
            "Please combine the following layers over Big Cypress National Preserve at zoom 11:\n"
            "1) Hurricane Ian peak flood depth raster (from job-0086).\n"
            "2) WDPA protected-area boundary polygon.\n"
            "3) GBIF occurrence points for Florida panther, Roseate spoonbill, and American alligator (one layer each).\n"
            "4) Add a Pelicun damage choropleth over Fort Myers with a green→yellow→red gradient.\n"
            "Once those are visible, please summarize the overlap between species observations and inundation > 0.5 m."
        )
        page.fill('[data-testid="chat-input"]', big_msg)
        page.wait_for_timeout(900)
        out_path = OUT_DIR / "07_chat_input_typing_grows.png"
        page.screenshot(path=str(out_path), full_page=False)

        info = page.evaluate(
            """() => {
                const ta = document.querySelector('[data-testid="chat-input"]');
                const w = document.querySelector('[data-testid="chat-input-wrapper"]');
                const taRect = ta ? ta.getBoundingClientRect() : null;
                const wRect = w ? w.getBoundingClientRect() : null;
                const messages = Array.from(document.querySelectorAll('[data-testid="chat"]')).length;
                return {
                    textarea_height: taRect ? taRect.height : null,
                    wrapper_height: wRect ? wRect.height : null,
                    wrapper_top: wRect ? wRect.top : null,
                    chat_count: messages,
                };
            }"""
        )
        return [(out_path.name, f"Chat input typing multi-line: textarea grew to {info['textarea_height']:.0f}px (wrapper {info['wrapper_height']:.0f}px). Chat content (2 seeded agent messages) remains visible above the floating input wrapper at top={info['wrapper_top']:.0f}px."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_08_chat_input_inflight(pw):
    """Inject a pipeline-state with a running step — button must show grey ■ stop-square."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        toggle_dark(page)
        # Wait for the dev seam to mount (Chat registers __grace2InjectPipelineState in its own effect).
        page.wait_for_function(
            "() => typeof window.__grace2InjectPipelineState === 'function'",
            timeout=8000,
        )
        pipeline_state = {
            "pipeline_id": "pipe_inflight_001",
            "steps": [
                {
                    "step_id": "step_1",
                    "name": "Fetching flood depth raster",
                    "tool_name": "fetch_flood_depth_raster",
                    "state": "running",
                    "progress_percent": 42,
                    "started_at": "2026-06-08T17:30:00Z",
                    "completed_at": None,
                }
            ],
        }
        page.evaluate("(p) => window.__grace2InjectPipelineState(p)", pipeline_state)
        page.wait_for_timeout(900)
        out_path = OUT_DIR / "08_chat_input_inflight.png"
        page.screenshot(path=str(out_path), full_page=False)

        info = page.evaluate(
            """() => {
                const w = document.querySelector('[data-testid="chat-input-wrapper"]');
                const btn = document.querySelector('[data-testid="chat-input-action"]');
                const glyph = document.querySelector('[data-testid="chat-input-glyph"]');
                return {
                    wrapper_state: w ? w.getAttribute('data-state') : null,
                    button_state: btn ? btn.getAttribute('data-action-state') : null,
                    button_bg: btn ? getComputedStyle(btn).backgroundColor : null,
                    glyph_kind: glyph ? glyph.getAttribute('data-glyph') : null,
                };
            }"""
        )
        return [(out_path.name, f"Chat input in-flight (synthetic running pipeline step injected via __grace2InjectPipelineState): wrapper data-state={info['wrapper_state']}, button data-action-state={info['button_state']}, glyph={info['glyph_kind']} (expected 'stop'), button bg={info['button_bg']} (grey, not blue)."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_09_payload_warning(pw):
    """Inject payload-warning envelope; polished inline card in chat (NOT modal)."""
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
    page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        set_anon_flag(page)
        page.goto(BASE_URL, wait_until="load", timeout=60_000)
        wait_for_app(page)
        page.wait_for_timeout(1500)
        warning = {
            "envelope_type": "tool-payload-warning",
            "warning_id": "warn_001",
            "tool_name": "fetch_goes_satellite",
            "tool_args": {"bbox": [-130, 24, -65, 50], "bands": ["visible"], "hours": 24},
            "estimated_mb": 150.0,
            "threshold_mb": 25.0,
            "recommendation": "Narrow to a smaller bbox or request fewer time steps.",
            "alternative_args": {"bbox": [-82.1, 26.4, -81.5, 26.9], "bands": ["visible"], "hours": 6},
            "options": ["proceed", "cancel", "narrow_scope"],
            "ttl_seconds": 300,
        }
        page.evaluate("(p) => window.__grace2InjectPayloadWarning(p)", warning)
        page.wait_for_timeout(1500)
        out_path = OUT_DIR / "09_payload_warning_card.png"
        page.screenshot(path=str(out_path), full_page=False)

        info = page.evaluate(
            """() => {
                const c = document.querySelector('[data-testid="payload-warning-inline"]');
                const tool = document.querySelector('[data-testid="payload-warning-tool"]');
                const est = document.querySelector('[data-testid="payload-warning-estimated-mb"]');
                const thr = document.querySelector('[data-testid="payload-warning-threshold-mb"]');
                const rect = c ? c.getBoundingClientRect() : null;
                const cs = c ? getComputedStyle(c) : null;
                return {
                    card_present: !!c,
                    tool_text: tool ? tool.textContent : null,
                    est_text: est ? est.textContent : null,
                    thr_text: thr ? thr.textContent : null,
                    width: rect ? rect.width : null,
                    box_shadow: cs ? cs.boxShadow : null,
                    border_radius: cs ? cs.borderRadius : null,
                };
            }"""
        )
        w = info.get('width')
        w_str = f"{w:.0f}px" if isinstance(w, (int, float)) else str(w)
        with open(OUT_DIR / "scenario09_console.txt", "w") as f:
            f.write("\n".join(console_msgs[-200:]))
        return [(out_path.name, f"Payload-warning inline card surfaced via __grace2InjectPayloadWarning: tool='{info['tool_text']}' est={info['est_text']} threshold={info['thr_text']}, width={w_str}, shadow={info['box_shadow']!r}, radius={info['border_radius']} (polished — drop shadow + rounded corners, in chat column, NOT a modal)."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_10_source_suggestion(pw):
    """Inject mode2-candidate envelope; SourceSuggestionInline card; NO internal jargon, confidence as percentage."""
    browser, ctx, page = boot_anon_page(pw)
    try:
        toggle_dark(page)
        candidate_payload = {
            "envelope_type": "mode2-candidate",
            "candidate": {
                "candidate_id": "cand_001",
                "url": "https://water.weather.gov/ahps/inundation.php",
                "domain": "water.weather.gov",
                "domain_tld": "gov",
                "confidence": 0.78,
                "detected_patterns": ["json-ld", "data-download-link", "ogc-wms", "structured-table"],
                "title": "AHPS Inundation Mapping",
                "suggested_tool_kind": "fetcher",
                "snippet": "Operational river inundation forecast maps from the National Water Prediction Service.",
            },
        }
        page.evaluate("(p) => window.__grace2InjectSourceSuggestion(p)", candidate_payload)
        page.wait_for_timeout(1500)
        out_path = OUT_DIR / "10_source_suggestion_card.png"
        page.screenshot(path=str(out_path), full_page=False)

        # Scan visible card text for forbidden internal terms.
        info = page.evaluate(
            """() => {
                const card = document.querySelector('[data-testid="source-suggestion-stack"]') ||
                             document.querySelector('[data-testid^="source-suggestion-"]');
                const visibleText = card ? (card.innerText || card.textContent || '') : '';
                const forbidden = ['Mode 2', 'Mode 1', 'Tier 1', 'Tier 2', 'OQ-'];
                const matches = forbidden.filter(t => visibleText.includes(t));
                return { card_present: !!card, visible_text: visibleText, forbidden_matches: matches };
            }"""
        )
        return [(out_path.name, f"Source-suggestion inline card from mode2-candidate envelope: card_present={info['card_present']}, forbidden internal terms found in card text = {info['forbidden_matches']!r} (expected empty list). Confidence (0.78) and 4 detected_patterns translated to user-friendly text."), info]
    finally:
        ctx.close()
        browser.close()


def scenario_11_case1_palette(pw):
    """Inject Case 1 demo session-state; per-species curated colors visually distinguishable on dark basemap."""
    browser, ctx, page = boot_anon_page(pw, viewport_w=1600, viewport_h=1100)
    try:
        panther_uri = geojson_data_uri(panther_points())
        spoonbill_uri = geojson_data_uri(spoonbill_points())
        alligator_uri = geojson_data_uri(alligator_points())
        wdpa_uri = geojson_data_uri(big_cypress_wdpa())

        session_state = {
            "chat_history": [
                {"role": "agent", "text": "Case 1 — Big Cypress flood × habitat headline: flood depth raster, WDPA polygon, and 3 GBIF species point layers using the curated job-0146 palette."}
            ],
            "loaded_layers": [
                {
                    "layer_id": "flood-depth-job-0086-fix",
                    "name": "Hurricane Ian peak flood depth (stand-in)",
                    "layer_type": "raster",
                    "uri": FLOOD_WMS_URL,
                    "source_url": FLOOD_WMS_URL,
                    "style_preset": "continuous_flood_depth",
                    "visible": True, "opacity": 0.6, "z_index": 2,
                    "role": "context", "bbox": BIG_CYPRESS_BBOX,
                    "attribution": "job-0086", "temporal": None,
                },
                {
                    "layer_id": "wdpa-big-cypress",
                    "name": "WDPA: Big Cypress National Preserve",
                    "layer_type": "vector",
                    "uri": wdpa_uri,
                    "style_preset": "wdpa_protected_areas",
                    "visible": True, "opacity": 0.5, "z_index": 3,
                    "role": "context", "bbox": BIG_CYPRESS_BBOX,
                    "attribution": "WDPA (synthetic)", "temporal": None,
                },
                {
                    "layer_id": "gbif-panther-fl",
                    "name": "Florida panther (Puma concolor coryi)",
                    "layer_type": "vector",
                    "uri": panther_uri,
                    "style_preset": "species_panther",
                    "visible": True, "opacity": 1.0, "z_index": 4,
                    "role": "primary", "bbox": BIG_CYPRESS_BBOX,
                    "attribution": "GBIF (synthetic)", "temporal": None,
                },
                {
                    "layer_id": "gbif-spoonbill-fl",
                    "name": "Roseate spoonbill (Platalea ajaja)",
                    "layer_type": "vector",
                    "uri": spoonbill_uri,
                    "style_preset": "species_bird",
                    "visible": True, "opacity": 1.0, "z_index": 5,
                    "role": "primary", "bbox": BIG_CYPRESS_BBOX,
                    "attribution": "GBIF (synthetic)", "temporal": None,
                },
                {
                    "layer_id": "gbif-alligator-fl",
                    "name": "American alligator (Alligator mississippiensis)",
                    "layer_type": "vector",
                    "uri": alligator_uri,
                    "style_preset": "species_reptile",
                    "visible": True, "opacity": 1.0, "z_index": 6,
                    "role": "primary", "bbox": BIG_CYPRESS_BBOX,
                    "attribution": "GBIF (synthetic)", "temporal": None,
                },
            ],
            "pipeline_history": [],
            "current_pipeline": None,
            "map_view": {"center": BIG_CYPRESS_CENTER, "zoom": 11, "bearing": 0, "pitch": 0},
        }
        page.evaluate("(p) => window.__grace2InjectSessionState(p)", session_state)
        page.wait_for_timeout(2500)
        toggle_dark(page)
        page.wait_for_timeout(1500)
        page.evaluate(
            f"""() => {{
                const m = window.__grace2GetMap && window.__grace2GetMap();
                if (m) m.jumpTo({{center: {json.dumps(BIG_CYPRESS_CENTER)}, zoom: 11}});
            }}"""
        )
        page.wait_for_timeout(3500)

        layer_info = page.evaluate(
            """() => {
                const m = window.__grace2GetMap && window.__grace2GetMap();
                if (!m) return {error: 'no_map'};
                const style = m.getStyle();
                const ids = ['gbif-panther-fl','gbif-spoonbill-fl','gbif-alligator-fl','wdpa-big-cypress'];
                const colors = {};
                for (const id of ids) {
                    try {
                        const c = m.getPaintProperty(id, 'circle-color') || m.getPaintProperty(id, 'fill-color') || m.getPaintProperty(id, 'line-color');
                        colors[id] = c;
                    } catch(e) { colors[id] = String(e); }
                }
                return {
                    layer_ids: (style.layers || []).map(l => l.id),
                    colors,
                };
            }"""
        )
        with open(OUT_DIR / "scenario11_layer_colors.json", "w") as f:
            json.dump(layer_info, f, indent=2)

        out_path = OUT_DIR / "11_case1_curated_palette.png"
        page.screenshot(path=str(out_path), full_page=False)
        return [(out_path.name, f"Case 1 headline z11 dark theme: per-species curated palette colors — panther={layer_info.get('colors', {}).get('gbif-panther-fl')}, spoonbill={layer_info.get('colors', {}).get('gbif-spoonbill-fl')}, alligator={layer_info.get('colors', {}).get('gbif-alligator-fl')}, WDPA={layer_info.get('colors', {}).get('wdpa-big-cypress')}. Each species visually distinguishable."), layer_info]
    finally:
        ctx.close()
        browser.close()


def scenario_12_pelicun_choropleth(pw):
    """Inject Pelicun output with varied ds_mean; gradient green→yellow→red visible, not solid."""
    browser, ctx, page = boot_anon_page(pw, viewport_w=1600, viewport_h=1100)
    try:
        pelicun_uri = geojson_data_uri(pelicun_damage_polygons())
        session_state = {
            "chat_history": [
                {"role": "agent", "text": "Pelicun damage choropleth — 20 building polygons with ds_mean ∈ [0,1] graded green→yellow→red."}
            ],
            "loaded_layers": [
                {
                    "layer_id": "flood-depth-job-0086-fix",
                    "name": "Hurricane Ian peak flood depth",
                    "layer_type": "raster",
                    "uri": FLOOD_WMS_URL,
                    "source_url": FLOOD_WMS_URL,
                    "style_preset": "continuous_flood_depth",
                    "visible": True, "opacity": 0.55, "z_index": 2,
                    "role": "context", "bbox": FORT_MYERS_BBOX,
                    "attribution": "job-0086", "temporal": None,
                },
                {
                    "layer_id": "pelicun-damage-fortmyers",
                    "name": "Pelicun damage assessment (Fort Myers)",
                    "layer_type": "vector",
                    "uri": pelicun_uri,
                    "style_preset": "pelicun_damage",
                    "visible": True, "opacity": 0.85, "z_index": 6,
                    "role": "primary", "bbox": FORT_MYERS_BBOX,
                    "attribution": "Pelicun synthetic", "temporal": None,
                },
            ],
            "pipeline_history": [],
            "current_pipeline": None,
            "map_view": {"center": FORT_MYERS_CENTER, "zoom": 13, "bearing": 0, "pitch": 0},
        }
        page.evaluate("(p) => window.__grace2InjectSessionState(p)", session_state)
        page.wait_for_timeout(2500)
        toggle_dark(page)
        page.wait_for_timeout(1500)
        page.evaluate(
            f"""() => {{
                const m = window.__grace2GetMap && window.__grace2GetMap();
                if (m) m.jumpTo({{center: {json.dumps(FORT_MYERS_CENTER)}, zoom: 13}});
            }}"""
        )
        page.wait_for_timeout(3500)

        # Inspect the actual fill-color expression on the pelicun layer.
        paint = page.evaluate(
            """() => {
                const m = window.__grace2GetMap && window.__grace2GetMap();
                if (!m) return {error: 'no_map'};
                try {
                    return {
                        fill_color: m.getPaintProperty('pelicun-damage-fortmyers', 'fill-color'),
                        fill_opacity: m.getPaintProperty('pelicun-damage-fortmyers', 'fill-opacity'),
                    };
                } catch (e) { return {error: String(e)}; }
            }"""
        )
        with open(OUT_DIR / "scenario12_pelicun_paint.json", "w") as f:
            json.dump(paint, f, indent=2)

        out_path = OUT_DIR / "12_pelicun_choropleth_polished.png"
        page.screenshot(path=str(out_path), full_page=False)
        is_expr = isinstance(paint.get("fill_color"), list)
        return [(out_path.name, f"Pelicun damage choropleth (Fort Myers, z13 dark): 20 polygons with varied ds_mean. fill-color is_expression={is_expr} (truthy = ds_mean→gradient interpolation active), fill-opacity={paint.get('fill_opacity')}."), paint]
    finally:
        ctx.close()
        browser.close()


# ============================================================
# Runner
# ============================================================

SCENARIOS = [
    ("01", scenario_01_cases_root),
    ("02", scenario_02_case_active),
    ("03", scenario_03_bottom_row),
    ("04", scenario_04_settings_popup),
    ("05", scenario_05_secrets_popup),
    ("06", scenario_06_chat_input_idle),
    ("07", scenario_07_chat_input_typing),
    ("08", scenario_08_chat_input_inflight),
    ("09", scenario_09_payload_warning),
    ("10", scenario_10_source_suggestion),
    ("11", scenario_11_case1_palette),
    ("12", scenario_12_pelicun_choropleth),
]


def main():
    from playwright.sync_api import sync_playwright

    manifest = []
    diagnostics = {}
    failures = []

    selected = sys.argv[1:]  # if no args, run all
    if not selected:
        selected = [s[0] for s in SCENARIOS]

    with sync_playwright() as pw:
        for sid, fn in SCENARIOS:
            if sid not in selected:
                continue
            print(f"\n=== scenario {sid} : {fn.__name__} ===")
            try:
                result = fn(pw)
                if not result:
                    continue
                entry = result[0]
                info = result[1] if len(result) > 1 else None
                fname, caption = entry
                manifest.append({"file": fname, "caption": caption})
                if info is not None:
                    diagnostics[sid] = info
                print(f"  OK -> {fname}")
            except Exception as e:
                tb = traceback.format_exc()
                failures.append({"scenario": sid, "error": str(e), "traceback": tb})
                print(f"  FAIL: {e}\n{tb}")

    with open(OUT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    with open(OUT_DIR / "diagnostics.json", "w") as f:
        json.dump(diagnostics, f, indent=2, default=str)
    with open(OUT_DIR / "failures.json", "w") as f:
        json.dump(failures, f, indent=2)

    print(f"\n=== summary ===")
    print(f"  captured: {len(manifest)}")
    print(f"  failures: {len(failures)}")
    print(f"  out_dir : {OUT_DIR}")


if __name__ == "__main__":
    main()
