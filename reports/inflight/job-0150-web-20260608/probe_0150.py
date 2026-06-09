"""job-0150 — PayloadWarning visual restyle verification probe.

Focused scenario: inject a payload-warning envelope via the dev seam,
screenshot the resulting inline card, and capture DOM-level evidence that
box-shadow and border-radius are non-zero on the [data-testid="payload-warning-inline"]
element (the element the Wave 4 probe queried, which previously returned 'none'/'0px').
"""

from __future__ import annotations
import json
from pathlib import Path

BASE_URL = "http://127.0.0.1:5177"
OUT_DIR = Path(__file__).parent / "evidence"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ANON_KEY = "grace2_anonymous_accepted"


def set_anon_flag(page) -> None:
    page.evaluate(f"() => localStorage.setItem('{ANON_KEY}', 'true')")


def wait_for_app(page, timeout=12000):
    page.wait_for_selector('[data-testid="grace2-app-shell"]', timeout=timeout)
    page.wait_for_function(
        "() => typeof window.__grace2InjectSessionState === 'function'",
        timeout=timeout,
    )
    page.wait_for_timeout(900)


def run(pw):
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
        wait_for_app(page)
        page.wait_for_timeout(1500)

        # Inject payload warning via dev seam (same as probe.py scenario_09).
        warning = {
            "envelope_type": "tool-payload-warning",
            "warning_id": "warn_job0150",
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

        # Screenshot.
        ss_path = OUT_DIR / "payload_warning_polished.png"
        page.screenshot(path=str(ss_path), full_page=False)

        # DOM-level evidence: getComputedStyle on [data-testid="payload-warning-inline"].
        dom_info = page.evaluate(
            """() => {
                const c = document.querySelector('[data-testid="payload-warning-inline"]');
                if (!c) return {error: 'element_not_found'};
                const cs = getComputedStyle(c);
                return {
                    element_tag: c.tagName,
                    data_variant: c.getAttribute('data-variant'),
                    data_warning_id: c.getAttribute('data-warning-id'),
                    box_shadow: cs.boxShadow,
                    border_radius: cs.borderRadius,
                    background: cs.background,
                    inline_box_shadow: c.style.boxShadow,
                    inline_border_radius: c.style.borderRadius,
                    card_present: true,
                };
            }"""
        )

        diagnostics = {
            "scenario": "payload_warning_polished",
            "dom_info": dom_info,
            "screenshot": ss_path.name,
        }
        with open(OUT_DIR / "diagnostics_0150.json", "w") as f:
            json.dump(diagnostics, f, indent=2)

        print(f"Screenshot: {ss_path}")
        print(f"DOM info: {json.dumps(dom_info, indent=2)}")

        # Assertions.
        assert dom_info.get("card_present"), "payload-warning-inline element not found in DOM"
        assert dom_info.get("box_shadow") and dom_info["box_shadow"] != "none", \
            f"box-shadow is none/empty: {dom_info.get('box_shadow')!r}"
        assert dom_info.get("border_radius") and dom_info["border_radius"] != "0px", \
            f"border-radius is 0px: {dom_info.get('border_radius')!r}"
        # data-warning-id should propagate.
        assert dom_info.get("data_warning_id") == "warn_job0150", \
            f"data-warning-id mismatch: {dom_info.get('data_warning_id')!r}"

        print("\nALL ASSERTIONS PASSED")
        return dom_info

    finally:
        with open(OUT_DIR / "console_0150.txt", "w") as f:
            f.write("\n".join(console_msgs[-200:]))
        ctx.close()
        browser.close()


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        run(pw)


if __name__ == "__main__":
    main()
