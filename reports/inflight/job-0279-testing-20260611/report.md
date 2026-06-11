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

## Close-out (2026-06-11)

**Scenario B (location fidelity) — PASS via organic user evidence:** the
user's own prompt "Compute color relief for Seattle Washington" (after a
Tampa flood in the same session) geocoded Seattle and published
`seattle-colored-relief` (agent log 00:34). The synthetic re-run was
superseded.

**Hillshade ("the shadow") — PASS, Gemini-free:** compute_hillshade on the
cached Boulder DEM through the real registry (COG output via job-0271) +
publish (no-preset styling via job-0269b) → WMS GetMap 200 in 9.0s,
512x512, 255 gray levels, 100% opaque. /tmp/hillshade_verify.png delivered.
Layer: hillshade-boulder-verify-0279.
