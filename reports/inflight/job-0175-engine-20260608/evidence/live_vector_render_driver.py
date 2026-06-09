"""job-0175 — LIVE Playwright verification: vector polygon render.

Per memory ``feedback_playwright_must_drive_live_agent``: NO ``__grace2Inject*``
seams permitted; the test must drive Gemini through the real chat input over
the WebSocket and verify the resulting map shows polygons.

Pre-conditions:
  - agent backend running on 127.0.0.1:8765 (restarted post-fix)
  - web Vite dev server running on 127.0.0.1:5173 with HMR'd code

Run from repo root:
    .venv-agent/bin/python reports/inflight/job-0175-engine-20260608/evidence/live_vector_render_driver.py

Captures screenshots demonstrating polygons/lines render on the map for:
  1. "Show me weather alerts across America" — NWS alerts CONUS polygons
  2. "Show me protected areas in Big Cypress" — WDPA polygons
  3. "Show me roads near Fort Myers" — OSM road linestrings
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE_URL = "http://127.0.0.1:5177"
OUT_DIR = Path(__file__).parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

ANON_KEY = "grace2_anonymous_accepted"

# Prompts to drive Gemini live.
PROMPTS = [
    {
        "name": "1_weather_alerts_conus",
        "prompt": (
            "Show me active weather alerts across the United States. "
            "Use fetch_nws_alerts_conus. Don't ask follow-up questions, "
            "just dispatch the tool."
        ),
        "wait_s": 180,  # NWS CONUS fetch + FGB conversion + GCS read for inline (large)
    },
    {
        "name": "2_protected_areas_big_cypress",
        "prompt": (
            "Show me protected areas in Big Cypress National Preserve, Florida. "
            "Use fetch_wdpa_protected_areas. Don't ask follow-up questions, "
            "just dispatch the tool."
        ),
        "wait_s": 180,
    },
    {
        "name": "3_roads_fort_myers",
        "prompt": (
            "Show me roads near Fort Myers, Florida using fetch_roads_osm with "
            "a 5 km radius. Don't ask follow-up questions, just dispatch the tool."
        ),
        "wait_s": 180,
    },
]


def set_anon_flag(page) -> None:
    page.evaluate(f"() => localStorage.setItem('{ANON_KEY}', 'true')")


def wait_for_either(page, settle_s: int) -> None:
    """Wait until the chat input goes idle (no spinner / not disabled) OR
    the wait budget expires. Returns either way."""
    deadline = time.time() + settle_s
    while time.time() < deadline:
        try:
            input_busy = page.evaluate(
                "() => document.querySelector('[data-testid=chat-input]')?.disabled === true"
            )
            running = page.evaluate(
                "() => document.querySelectorAll('[data-state=running]').length"
            )
            if not input_busy and (running or 0) == 0:
                # idle
                page.wait_for_timeout(2500)  # let map settle visually
                return
        except Exception:
            pass
        page.wait_for_timeout(2000)


def snapshot_map_state(page) -> dict:
    """Read the live MapLibre style + sources/layers via the
    `__grace2GetMap` debug seam. NOT an inject seam — purely introspective."""
    js = """
    () => {
      const get = window.__grace2GetMap;
      const m = get ? get() : null;
      if (!m) return { error: 'no map' };
      const style = m.getStyle();
      const layers = (style.layers || []).map(l => ({ id: l.id, type: l.type, source: l.source }));
      const sources = Object.keys(style.sources || {});
      return { layers, sources };
    }
    """
    try:
        return page.evaluate(js)
    except Exception as exc:
        return {"error": str(exc)}


def main() -> int:
    from playwright.sync_api import sync_playwright

    findings: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        console_msgs: list[str] = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
            set_anon_flag(page)
            page.goto(BASE_URL, wait_until="load", timeout=60_000)
            # Capture immediate snapshot for debugging.
            page.screenshot(path=str(OUT_DIR / "0a_after_goto.png"), full_page=False)
            page.wait_for_timeout(2000)
            page.screenshot(path=str(OUT_DIR / "0a2_after_goto_2s.png"), full_page=False)
            print(f"after-goto title={page.title()!r}", flush=True)
            print(f"console_msgs after goto: {console_msgs[-10:]}", flush=True)
            body_html = page.evaluate("() => document.body.innerHTML.slice(0, 2000)")
            print(f"body html snippet: {body_html[:1500]}", flush=True)
            # If the AuthGate is showing, click "continue as anonymous".
            try:
                anon_btn = page.locator(
                    '[data-testid="grace2-auth-gate-anonymous"]',
                ).first
                anon_btn.wait_for(state="visible", timeout=10000)
                print("auth-gate present — clicking anonymous", flush=True)
                anon_btn.click()
                page.wait_for_timeout(2000)
                page.screenshot(path=str(OUT_DIR / "0b_after_anon_click.png"), full_page=False)
            except Exception as exc:
                # already past the gate (anon flag picked up); proceed.
                print(f"no auth gate (or skipped): {exc}", flush=True)
            # Diagnostic: what testids ARE on the page right now.
            ids = page.evaluate(
                "() => Array.from(document.querySelectorAll('[data-testid]')).map(e => e.getAttribute('data-testid')).slice(0, 20)"
            )
            print(f"testids visible: {ids}", flush=True)
            page.wait_for_selector('[data-testid="grace2-app-shell"]', timeout=30000)
            page.wait_for_selector('[data-testid="chat-input"]', timeout=30000)
            page.wait_for_timeout(2000)

            # Capture initial app shell (baseline).
            page.screenshot(path=str(OUT_DIR / "0_baseline.png"), full_page=False)

            for step in PROMPTS:
                print(f"=== driving prompt: {step['name']} ===", flush=True)
                # Wait for chat input to be idle.
                page.wait_for_selector(
                    '[data-testid="chat-input"]:not([disabled])', timeout=30000,
                )
                chat_input = page.locator('[data-testid="chat-input"]').first
                chat_input.click()
                # Clear (in case prior text persisted).
                chat_input.fill("")
                page.wait_for_timeout(200)
                chat_input.fill(step["prompt"])
                page.wait_for_timeout(300)
                page.keyboard.press("Enter")
                print(f"submitted; waiting up to {step['wait_s']}s for settle…", flush=True)
                wait_for_either(page, step["wait_s"])

                state = snapshot_map_state(page)
                findings[step["name"]] = {
                    "prompt": step["prompt"],
                    "map_state": state,
                }
                ss_path = OUT_DIR / f"{step['name']}.png"
                page.screenshot(path=str(ss_path), full_page=False)
                print(f"  captured {ss_path.name}", flush=True)

            # Save console + findings for post-hoc inspection.
            (OUT_DIR / "console.log").write_text("\n".join(console_msgs), encoding="utf-8")
            (OUT_DIR / "findings.json").write_text(
                json.dumps(findings, indent=2, default=str), encoding="utf-8",
            )
        finally:
            ctx.close()
            browser.close()

    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
