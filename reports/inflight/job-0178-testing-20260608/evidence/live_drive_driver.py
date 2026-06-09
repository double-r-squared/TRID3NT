"""job-0178 — LIVE-DRIVE Playwright verification of Wave 4.9 fixes.

Drives Gemini through the real chat input for 4 scenarios + 1 retry-path
prompt. NO ``__grace2Inject*`` seams used (per memory
``feedback_playwright_must_drive_live_agent``).

Scenarios:
    1. Radar over America (raster — control)
    2. Weather alerts across America (vector polygon — was the job-0175 bug)
    3. Protected areas in Big Cypress (vector polygon + multi-tool chain)
    4. Roads near Fort Myers (vector linestring)
    5. Recoverable failure → retry (per job-0177)

For each scenario we capture:
    - Headline screenshot
    - Map canvas non-basemap-pixel evidence (vector / raster overlay rendered?)
    - DOM stream rows (interleave preserved?)
    - Tool card terminal states

Run from repo root:
    .venv-agent/bin/python reports/inflight/job-0178-testing-20260608/evidence/live_drive_driver.py
"""
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_URL = "http://127.0.0.1:5177"
OUT_DIR = Path(__file__).parent
ANON_KEY = "grace2_anonymous_accepted"
SCENARIO_TIMEOUT_S = 240  # per scenario, generous for live Gemini


@dataclass
class ScenarioOutcome:
    name: str
    prompt: str
    screenshot: str = ""
    map_overlay_screenshot: str = ""
    map_overlay_basemap_screenshot: str = ""
    stream_rows: list[dict[str, Any]] = field(default_factory=list)
    interleave_ok: bool = False
    tool_terminal_states: list[str] = field(default_factory=list)
    map_pixel_evidence: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)


def set_anon_flag(page) -> None:
    page.evaluate(f"() => localStorage.setItem('{ANON_KEY}', 'true')")


def wait_until_terminal_or_post_narration(page, deadline_s: float) -> list[dict]:
    """Poll the chat stream until either all tool cards are terminal AND we
    have at least one post-tool agent bubble, or deadline reached.
    """
    deadline = time.monotonic() + deadline_s
    final_rows: list[dict] = []
    while time.monotonic() < deadline:
        page.wait_for_timeout(3000)
        rows = page.evaluate(
            """() => {
                const root = document.querySelector('[data-testid="chat-stream"]');
                if (!root) return [];
                return Array.from(root.children).map(c => {
                    const t = c.getAttribute('data-testid');
                    let kind = 'unknown';
                    if (t === 'user-bubble') kind = 'user';
                    else if (t === 'pipeline-card') kind = 'tool';
                    else kind = 'agent';
                    return {
                        kind,
                        state: c.getAttribute('data-state') || null,
                        text: (c.textContent || '').slice(0, 200),
                    };
                });
            }"""
        )
        final_rows = rows
        kinds = [r["kind"] for r in rows]
        tool_states = [r["state"] for r in rows if r["kind"] == "tool"]
        n_tools = len(tool_states)
        n_agents = sum(1 for k in kinds if k == "agent")
        all_terminal = n_tools > 0 and all(
            s in ("complete", "failed", "cancelled") for s in tool_states
        )
        print(f"  poll: kinds={kinds[-10:]} tool_states={tool_states} n_agents={n_agents}")
        if all_terminal and n_agents >= 1:
            # Look for the canonical pattern: post-tool agent text.
            last_tool_idx = max(i for i, k in enumerate(kinds) if k == "tool")
            if "agent" in kinds[last_tool_idx + 1 :]:
                print("  → exit poll: tools terminal + post-tool agent narration")
                break
            # Else accept if we have ≥2 agent bubbles (pre+post).
            if n_agents >= 2:
                print("  → exit poll: ≥2 agent bubbles")
                break
            # Or if no agent narration ever, exit anyway after all tools terminal.
            if time.monotonic() > deadline - 30:
                print("  → exit poll: deadline window, tools terminal")
                break
    return final_rows


def count_non_basemap_pixels(png_bytes: bytes) -> dict[str, Any]:
    """Quick pixel inspection of the map canvas region. Returns counts of
    non-basemap-colored pixels — basemap is the dark-theme MapLibre style
    (rgb mostly < 60 across channels) so anything bright (RGB > 100 or
    significantly red/green/blue) suggests an overlay.

    Detects vector polygon outlines, vector linestrings, raster overlays,
    and color markers.
    """
    try:
        from PIL import Image
    except ImportError:
        return {"error": "PIL not available"}
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = img.size
    pixels = img.load()
    # Sample every 4th pixel for speed.
    counts = {
        "total_sampled": 0,
        "bright_pixels": 0,  # likely overlay (vector stroke, raster, label)
        "warm_pixels": 0,  # red/orange (radar / fire)
        "blue_pixels": 0,  # cool — water raster or alert blue
        "yellow_pixels": 0,  # alert yellow / fire perimeter
        "saturated_pixels": 0,  # high-chroma (likely overlay)
        "dark_pixels": 0,  # basemap dominant
    }
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            r, g, b = pixels[x, y]
            counts["total_sampled"] += 1
            max_ch = max(r, g, b)
            min_ch = min(r, g, b)
            chroma = max_ch - min_ch
            if max_ch < 60:
                counts["dark_pixels"] += 1
            if max_ch > 100:
                counts["bright_pixels"] += 1
            if chroma > 50:
                counts["saturated_pixels"] += 1
            if r > 150 and g < 100 and b < 100:
                counts["warm_pixels"] += 1
            if b > 120 and r < 100:
                counts["blue_pixels"] += 1
            if r > 180 and g > 160 and b < 100:
                counts["yellow_pixels"] += 1
    counts["fraction_overlay"] = round(
        (counts["bright_pixels"]) / max(1, counts["total_sampled"]), 4
    )
    counts["fraction_saturated"] = round(
        counts["saturated_pixels"] / max(1, counts["total_sampled"]), 4
    )
    counts["fraction_dark"] = round(
        counts["dark_pixels"] / max(1, counts["total_sampled"]), 4
    )
    return counts


def screenshot_map_canvas(page, out_path: Path) -> bytes:
    """Screenshot only the map area for pixel inspection."""
    map_el = page.locator('[data-testid="grace2-map"]').first
    png = map_el.screenshot(path=str(out_path))
    return png


def run_scenario(
    page,
    name: str,
    prompt: str,
    out_prefix: str,
    expect_overlay: bool = True,
) -> ScenarioOutcome:
    """Send a prompt, wait for completion, capture evidence."""
    outcome = ScenarioOutcome(name=name, prompt=prompt)
    start = time.monotonic()

    print(f"\n=== Scenario: {name} ===")
    print(f"  prompt: {prompt}")

    # Capture baseline map BEFORE sending (no overlay).
    baseline_path = OUT_DIR / f"{out_prefix}_map_basemap.png"
    baseline_png = screenshot_map_canvas(page, baseline_path)
    outcome.map_overlay_basemap_screenshot = baseline_path.name
    baseline_counts = count_non_basemap_pixels(baseline_png)
    print(f"  baseline pixel counts: bright={baseline_counts.get('bright_pixels')} sat={baseline_counts.get('saturated_pixels')}")

    # Send prompt.
    chat_input = page.locator('[data-testid="chat-input"]').first
    chat_input.click()
    chat_input.fill(prompt)
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")

    # Wait for stream.
    page.wait_for_selector('[data-testid="chat-stream"]', timeout=20_000)

    final_rows = wait_until_terminal_or_post_narration(page, SCENARIO_TIMEOUT_S)
    outcome.stream_rows = final_rows
    outcome.tool_terminal_states = [
        r["state"] for r in final_rows if r["kind"] == "tool"
    ]

    # Check interleave: at least 1 user → tool, ideally agent between user and tool.
    kinds = [r["kind"] for r in final_rows]
    if "user" in kinds and "tool" in kinds:
        user_idx = kinds.index("user")
        first_tool = kinds.index("tool", user_idx + 1) if "tool" in kinds[user_idx + 1:] else -1
        if first_tool > 0 and "agent" in kinds[user_idx + 1 : first_tool + 2]:
            outcome.interleave_ok = True
        elif first_tool > 0:
            outcome.notes.append("tool dispatched without pre-narration (acceptable)")
            # Still count as interleave if there's any agent text after tool.
            if "agent" in kinds[first_tool + 1 :]:
                outcome.interleave_ok = True

    # Capture headline screenshot.
    headline_path = OUT_DIR / f"{out_prefix}_headline.png"
    page.screenshot(path=str(headline_path), full_page=False)
    outcome.screenshot = headline_path.name

    # Capture map canvas AFTER (overlay should be present).
    overlay_path = OUT_DIR / f"{out_prefix}_map_overlay.png"
    overlay_png = screenshot_map_canvas(page, overlay_path)
    outcome.map_overlay_screenshot = overlay_path.name
    overlay_counts = count_non_basemap_pixels(overlay_png)
    outcome.map_pixel_evidence = {
        "baseline": baseline_counts,
        "with_overlay": overlay_counts,
        "delta_bright": overlay_counts.get("bright_pixels", 0)
        - baseline_counts.get("bright_pixels", 0),
        "delta_saturated": overlay_counts.get("saturated_pixels", 0)
        - baseline_counts.get("saturated_pixels", 0),
        "expect_overlay": expect_overlay,
    }
    outcome.duration_s = time.monotonic() - start

    print(f"  duration: {outcome.duration_s:.1f}s")
    print(f"  stream rows: {len(final_rows)}, kinds={kinds}")
    print(f"  tool terminal states: {outcome.tool_terminal_states}")
    print(f"  interleave_ok: {outcome.interleave_ok}")
    print(
        f"  pixel evidence: Δbright={outcome.map_pixel_evidence['delta_bright']} "
        f"Δsaturated={outcome.map_pixel_evidence['delta_saturated']}"
    )
    return outcome


def navigate_to_new_case(page) -> str:
    """Click new-case button, dismiss save-gate modal if present (anonymous
    user), wait for case view, return case ID from URL."""
    # First wait for cases panel.
    page.wait_for_selector('[data-testid="grace2-cases-panel"]', timeout=10_000)
    new_btn = page.locator('[data-testid="grace2-cases-new"]').first
    new_btn.click()
    # Anonymous users see the save-gate modal — click "Continue anyway".
    try:
        page.wait_for_selector(
            '[data-testid="grace2-save-gate-modal-continue"]',
            timeout=3_000,
        )
        page.locator('[data-testid="grace2-save-gate-modal-continue"]').first.click()
        print("  dismissed save-gate (Continue anyway)")
    except Exception:
        pass  # Not anonymous, or already dismissed.
    # Wait a moment for the create round-trip to settle (case row appears),
    # then click the most recent (first) case row to open it. The server may
    # not auto-activate the newly-created Case for anonymous sessions.
    page.wait_for_timeout(2000)
    rows = page.locator('[data-testid="grace2-case-row"]')
    if rows.count() > 0:
        rows.first.click()
        page.wait_for_timeout(800)
        print(f"  clicked first case row (of {rows.count()})")
    # Wait for case view to mount.
    try:
        page.wait_for_selector('[data-testid="grace2-case-view"]', timeout=15_000)
    except Exception:
        debug_path = OUT_DIR / "DEBUG_no_case_view.png"
        page.screenshot(path=str(debug_path), full_page=True)
        # Dump all data-testids visible.
        present = page.evaluate(
            "() => Array.from(document.querySelectorAll('[data-testid]')).map(e => e.getAttribute('data-testid'))"
        )
        (OUT_DIR / "DEBUG_present_testids.txt").write_text("\n".join(present))
        raise
    # Wait for chat input inside the case view.
    page.wait_for_selector('[data-testid="chat-input"]', timeout=15_000)
    page.wait_for_timeout(1500)
    url = page.url
    print(f"  new case URL: {url}")
    return url


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    overall: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url": BASE_URL,
        "scenarios": [],
        "console_messages_tail": [],
        "page_errors": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        msgs: list[str] = []
        errs: list[str] = []
        page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: errs.append(f"[pageerror] {e}"))

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
            set_anon_flag(page)
            page.goto(BASE_URL, wait_until="load", timeout=60_000)
            page.wait_for_selector('[data-testid="grace2-app-shell"]', timeout=15_000)

            scenarios = [
                {
                    "name": "S1 — Radar over America (raster control)",
                    "prompt": "Show me radar over America. Just fetch and display NEXRAD radar reflectivity over the continental US. No follow-up questions.",
                    "prefix": "01_radar_america",
                    "expect_overlay": True,
                },
                {
                    "name": "S2 — Weather alerts across America (vector polygon)",
                    "prompt": "Show me active NWS weather alerts across America. Display all current alerts as polygons on the map. No follow-up questions.",
                    "prefix": "02_alerts_america",
                    "expect_overlay": True,
                },
                {
                    "name": "S3 — Protected areas in Big Cypress (vector polygon + chain)",
                    "prompt": "Show me protected areas in Big Cypress National Preserve. Geocode the area first then fetch WDPA protected-area polygons within ~50km. No follow-up questions.",
                    "prefix": "03_wdpa_big_cypress",
                    "expect_overlay": True,
                },
                {
                    "name": "S4 — Roads near Fort Myers (vector linestring)",
                    "prompt": "Show me roads near Fort Myers, Florida. Geocode Fort Myers then fetch OSM road linestrings within a small bbox around the city center. No follow-up questions.",
                    "prefix": "04_roads_fort_myers",
                    "expect_overlay": True,
                },
                {
                    "name": "S5 — Recoverable failure → retry (per job-0177)",
                    "prompt": (
                        "Fetch protected areas using an obviously-invalid bbox "
                        "[200, 200, 250, 250] (out of lon/lat range). If the tool "
                        "fails, retry with a corrected bbox covering Florida "
                        "(roughly [-87, 24, -80, 31])."
                    ),
                    "prefix": "05_retry_failure",
                    "expect_overlay": False,  # may or may not render — focus is retry path
                },
            ]

            # Navigate to Cases panel & create first Case.
            new_case_url_1 = navigate_to_new_case(page)
            overall["case_1_url"] = new_case_url_1

            # Run S1 in this Case.
            outcome = run_scenario(
                page,
                scenarios[0]["name"],
                scenarios[0]["prompt"],
                scenarios[0]["prefix"],
                expect_overlay=scenarios[0]["expect_overlay"],
            )
            overall["scenarios"].append(outcome.__dict__)

            # For each remaining scenario, create a new Case (Wave 4.8 known
            # bug: layers/chat carry across — but here we *want* clean state
            # so we use a fresh Case for each map-overlay scenario to make
            # pixel comparison unambiguous).
            for sc in scenarios[1:]:
                # Back to cases list.
                back_link = page.locator('[data-testid="grace2-case-view-cases-link"]').first
                if back_link.count() > 0:
                    back_link.click()
                    page.wait_for_timeout(800)
                navigate_to_new_case(page)
                outcome = run_scenario(
                    page,
                    sc["name"],
                    sc["prompt"],
                    sc["prefix"],
                    expect_overlay=sc["expect_overlay"],
                )
                overall["scenarios"].append(outcome.__dict__)

        finally:
            overall["console_messages_tail"] = msgs[-100:]
            overall["page_errors"] = errs
            (OUT_DIR / "diagnostics.json").write_text(json.dumps(overall, indent=2, default=str))
            (OUT_DIR / "console.txt").write_text("\n".join(msgs))
            (OUT_DIR / "errors.txt").write_text("\n".join(errs))
            ctx.close()
            browser.close()

    # Summary.
    print("\n\n=== SUMMARY ===")
    pass_count = 0
    fail_count = 0
    for sc in overall["scenarios"]:
        name = sc["name"]
        states = sc["tool_terminal_states"]
        any_complete = any(s == "complete" for s in states)
        pixel = sc["map_pixel_evidence"]
        delta = pixel.get("delta_bright", 0) + pixel.get("delta_saturated", 0)
        rendered = delta > 200  # rough threshold
        verdict = "PASS" if (any_complete and (rendered or not pixel.get("expect_overlay"))) else "FAIL"
        if verdict == "PASS":
            pass_count += 1
        else:
            fail_count += 1
        print(
            f"  [{verdict}] {name}: tools={states} interleave={sc['interleave_ok']} "
            f"Δpixels={delta} rendered={rendered}"
        )
    print(f"\n  Pass: {pass_count} / {len(overall['scenarios'])}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
