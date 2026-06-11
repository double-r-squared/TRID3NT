# job-0279 — live verification under user-unlocked Gemini quota

**Scenario A (mobile under a REAL stream) — PASS.** 390x844 Playwright
session against the tailnet URL, real Boulder relief prompt, no inject
seams: drawer button, bottom sheet with user bubble + 4 tool cards with
authoritative timers (publish 1:58) + closing narration, and the relief
overlay visible on the map above the sheet (evidence/m1-m5). Closes
job-0278's "mobile with live stream unverified" caveat. End-to-end over the
Tailscale binding (job-0275) — the full stack works off-localhost.

**Scenario B (location fidelity, job-0274) — BLOCKED(429).** The
"Now do the same for Seattle, WA." turn drew RESOURCE_EXHAUSTED; stopped
immediately per quota discipline. Re-run queued (single cache-hit turn).

**Side finding fixed in-loop:** MobileDrawer lacked Escape dismissal
(the overlay-convention audit gap that wedged scenario A's script);
added + drawer tests 6/6.
