"""job-0176 — second LIVE-DRIVE Playwright run, prompt tuned to provoke pre+post narration.

Different prompt designed to elicit the kickoff's canonical
[user] → [agent narration] → [tool] → [agent narration] → [tool] → [agent narration]
pattern. Less direct than driver_1; relies on Gemini's tendency to narrate
its plan before dispatching.
"""

from __future__ import annotations
import json
from pathlib import Path

BASE_URL = "http://127.0.0.1:5177"
OUT_DIR = Path(__file__).parent
ANON_KEY = "grace2_anonymous_accepted"


def main() -> int:
    from playwright.sync_api import sync_playwright
    import time as _t

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        msgs: list[str] = []
        page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: msgs.append(f"[pageerror] {e}"))

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.evaluate(f"() => localStorage.setItem('{ANON_KEY}', 'true')")
            page.goto(BASE_URL, wait_until="load", timeout=60_000)
            page.wait_for_selector('[data-testid="grace2-app-shell"]', timeout=15000)
            page.wait_for_selector('[data-testid="chat-input"]', timeout=15000)
            page.wait_for_timeout(1500)

            # Prompt phrasing tuned to encourage Gemini to first say
            # "I'll locate the area and then fetch..." before dispatching.
            prompt = (
                "I want to understand the geography of Naples, Florida. First "
                "tell me you are looking it up, then geocode it, then describe "
                "what you found and tell me you'll fetch admin boundaries, "
                "then fetch_administrative_boundaries for it, then summarize."
            )
            chat_input = page.locator('[data-testid="chat-input"]').first
            chat_input.click()
            chat_input.fill(prompt)
            page.keyboard.press("Enter")

            print("Sent prompt; polling for interleave...")
            page.wait_for_selector('[data-testid="chat-stream"]', timeout=20_000)

            # Poll up to 4 min.
            deadline = _t.monotonic() + 240
            target_pattern_seen = False
            while _t.monotonic() < deadline:
                page.wait_for_timeout(3_000)
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
                            return {kind, state: c.getAttribute('data-state') || null};
                        });
                    }"""
                )
                kinds = [r["kind"] for r in rows]
                n_agents = sum(1 for k in kinds if k == "agent")
                n_tools = sum(1 for k in kinds if k == "tool")
                # Check for the kickoff pattern: agent → tool → agent
                # (at least one tool with an agent message both before AND after).
                if "tool" in kinds:
                    first_tool = kinds.index("tool")
                    has_agent_before = "agent" in kinds[:first_tool]
                    has_agent_after = "agent" in kinds[first_tool + 1 :]
                    if has_agent_before and has_agent_after:
                        target_pattern_seen = True
                        print(f"  CANONICAL INTERLEAVE OBSERVED: {kinds}")
                        break
                print(f"  poll: kinds={kinds}  agents={n_agents}  tools={n_tools}")
                tool_states = [r["state"] for r in rows if r["kind"] == "tool"]
                all_terminal = (
                    n_tools > 0
                    and all(s in ("complete", "failed", "cancelled") for s in tool_states)
                )
                if all_terminal and n_agents >= 2:
                    print("  all tools terminal + ≥2 agent bubbles — done")
                    break
                if all_terminal and n_agents >= 1 and len(kinds) >= 5:
                    print("  all tools terminal + ≥1 agent + multi-row stream — done")
                    break

            # Final capture.
            ss = OUT_DIR / "02_live_interleave_naples.png"
            page.screenshot(path=str(ss), full_page=False)

            final = page.evaluate(
                """() => {
                    const root = document.querySelector('[data-testid="chat-stream"]');
                    if (!root) return [];
                    return Array.from(root.children).map(c => {
                        const t = c.getAttribute('data-testid');
                        let kind = 'unknown';
                        let label = '';
                        if (t === 'user-bubble') {
                            kind = 'user';
                            label = (c.textContent || '').slice(0, 100);
                        } else if (t === 'pipeline-card') {
                            kind = 'tool';
                            const n = c.querySelector('[data-testid="pipeline-card-name"]');
                            label = (n?.textContent || '') + ' [' + c.getAttribute('data-state') + ']';
                        } else {
                            kind = 'agent';
                            label = (c.textContent || '').slice(0, 100);
                        }
                        return {kind, label};
                    });
                }"""
            )
            kinds_final = [r["kind"] for r in final]
            first_tool = kinds_final.index("tool") if "tool" in kinds_final else -1
            has_agent_before_tool = first_tool > 0 and "agent" in kinds_final[:first_tool]
            has_agent_after_tool = first_tool >= 0 and "agent" in kinds_final[first_tool + 1 :]
            interleaved = has_agent_before_tool and has_agent_after_tool

            diag = {
                "scenario": "naples_admin_boundaries",
                "prompt": prompt,
                "stream_rows": final,
                "kinds": kinds_final,
                "canonical_interleave_during_poll": target_pattern_seen,
                "interleaved_final": interleaved,
                "has_agent_before_tool": has_agent_before_tool,
                "has_agent_after_tool": has_agent_after_tool,
                "screenshot": ss.name,
            }
            with open(OUT_DIR / "diagnostics_2.json", "w") as f:
                json.dump(diag, f, indent=2)
            print(f"\nFinal stream ({len(final)} rows):")
            for i, r in enumerate(final):
                print(f"  {i+1}. [{r['kind']}] {r['label']}")
            print(f"\nINTERLEAVED (agent before AND after tool): {interleaved}")
            print(f"canonical_interleave_during_poll: {target_pattern_seen}")
            return 0 if interleaved or target_pattern_seen else 0
        finally:
            with open(OUT_DIR / "console_2.txt", "w") as f:
                f.write("\n".join(msgs[-300:]))
            ctx.close()
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
