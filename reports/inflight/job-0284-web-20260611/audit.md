# job-0284 — web — mobile map-centric pass (kickoff, frozen)

Specialist: web (Fable runner, MAX effort). Dispatched 2026-06-11.

## Mission (user-directed, their words in quotes)

Revise the MOBILE UI (<768px) to be map-centric, floating, and cohesive —
"functionality 100%".

1. "cases should be the back button no need for another one" — inside a
   Case on mobile, the "Cases" breadcrumb IS the back affordance; remove
   any duplicate/back-arrow button so there is exactly ONE way back,
   labeled Cases.
2. "the panel should be invisible and components should float" — the
   mobile drawer loses its solid panel surface: no opaque sheet behind the
   content; Case rows / CaseView / LayerPanel rows float as individual
   hairline cards directly over the map (subtle scrim ONLY behind text
   where legibility on a light basemap demands it — prefer per-card
   translucent backgrounds over a panel-wide one). Backdrop tap-to-close
   still works (an invisible full-screen hit area is fine).
3. "the chat should also be transparent ... so map is still visible
   underneath, this is a map centric app" — the chat bottom sheet becomes
   translucent: rgba background (~0.55–0.7 alpha, tune for legibility) so
   the map reads through, in both collapsed and expanded states. Message
   bubbles/cards keep enough contrast to read.
4. Cohesion: use the hairline design family job-0283 established
   (rgba(255,255,255,0.06–0.14) hairlines, 12px panel / 8px row radii,
   gradient-but-now-translucent surfaces).
5. The Case snap-to-location behavior (job-0280/0281) must keep working —
   presentation changes only.

## Critical engineering hazard

backdrop-filter creates a CSS containing block that traps position:fixed
descendants (modals/galleries render inside the panel instead of the
viewport). The mobile sheet and drawer HOST such descendants
(ChartGallery; ConfirmationDialog). Achieve translucency with rgba/alpha
backgrounds and gradients ONLY — do NOT add
backdrop-filter/filter/transform/will-change to any surface hosting
fixed-position children.

## DO NOT

- Touch desktop rendering (byte-identical requirement; prove with pixel
  comparison of before/after desktop screenshots).
- Restart anything (HMR live; every save must compile).
- Issue Gemini/Vertex calls (static screenshots + dev-seam injections only).
- Touch web/src/main.tsx or any file the concurrent job-0285 creates
  (Landing*/Privacy*/EntryRouter).
- Stage web/src/components/SettingsPopup.tsx (pre-existing uncommitted
  change stays unstaged).

## Constraints

- Web only (web/src). Chat.tsx carries per-Case streams (0266), case-tag
  routing (0277), the sheet + strip (0278/0280) — presentation-only edits.
- `git add` only this job's files.

## Acceptance

- Full vitest green (baseline 642 after job-0283; zero regressions; add
  tests for the single-back-affordance and translucent-surface pins).
- Mobile screenshots (390x844): floating drawer over the map, translucent
  collapsed sheet, translucent expanded sheet with injected tool cards,
  in-Case view showing the single Cases back affordance — under
  reports/inflight/job-0284-web-20260611/evidence/.
- Desktop pixel-compare proof.
- {audit.md, report.md, STATE=DONE}; commit "job-0284: ..." +
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>.
