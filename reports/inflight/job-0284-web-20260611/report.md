# job-0284 — web — mobile map-centric pass — report

STATE: DONE. The mobile (<768px) UI is now map-centric: the drawer panel
surface is gone (components float as individual translucent hairline cards
over the map, invisible tap-to-close backdrop), the chat bottom sheet is
translucent in both states so the map reads through, and inside a Case the
"Cases" breadcrumb link is the SINGLE back affordance (the ← arrow is
mobile-removed). All presentation-only; 100% functionality retained
(verified: backdrop tap-close, delete ConfirmationDialog viewport-centering,
Case snap-to-location, sheet expand/collapse, composer, tool cards/strip).
686/686 vitest green. Desktop proven BYTE-IDENTICAL (all four before/after
desktop captures are file-bytes-identical — see proof below). HMR compiled
at every save; nothing restarted; zero Gemini calls.

## Baseline note

Kickoff said 642 tests post-0283; actual baseline at my start was **680/680
(43 files)** because the concurrent job-0285 (landing page) had added
EntryRouter/Landing/Privacy tests to the shared working tree. After this
job: **686/686** (+6 new, 2 deliberately-updated pins, zero regressions).
job-0285 committed its files mid-job; I never touched main.tsx/EntryRouter/
pages/*.

## What changed, by mission item

### 1. Single back affordance — "Cases" IS the back button (mobile)

`CaseView` gained a `mobile?: boolean` prop (default false — desktop
byte-identical). When true, the ← arrow button (`grace2-case-view-back`)
is NOT rendered; the existing "Cases" link (`grace2-case-view-cases-link`,
same onBack, 44px touch bump from the job-0278 CSS scope) is the one way
back, labeled "Cases". App.tsx passes `mobile` ONLY in the drawer branch
(itself `isMobile`-gated). Desktop keeps arrow + link exactly as before.

### 2. Floating drawer — no panel, components float

`MobileDrawer.tsx` (mobile-only component):
- Backdrop: `rgba(0,0,0,0.45)` dim → **transparent** invisible full-screen
  hit area. Tap-to-close behavior unchanged (re-verified live).
- Panel: solid `rgba(15,15,20,0.97)` + border + shadow → **transparent
  layout column** (width/padding/flex/z-index unchanged). In-code comment
  forbids backdrop-filter forever (hosts ConfirmationDialog,
  position:fixed).
- New bottom clearance `calc(138px + env(safe-area-inset-bottom))`: with
  the backdrop no longer opaque, the collapsed sheet is visible under the
  open drawer, and the drawer's footer pills were overlapping the composer
  (caught in my first after-capture). The padding floats them above it.
- ☰ opener joins the hairline family (rgba(18,19,24,0.92), hairline 0.08,
  radius 10, soft shadow, blur — leaf surface, no fixed children).

Per-card surfaces via the `.grace2-mobile-touch` scope (new job-0284 block
in global.css; `!important` against inline styles, same documented
trade-off as 0280/0283; block comment restates the no-backdrop-filter
hazard):
- Cases header (new `data-testid="grace2-cases-header"` in CasesPanel —
  attribute only, zero desktop pixels): family gradient @0.82 + hairline
  0.10 + radius 10 card.
- Case rows: rgba(18,19,24,0.82) + hairline 0.12 + radius 8 + soft shadow;
  active row rgba(30,45,75,0.86) + blue 0.55 border. (+ New Case radius 8;
  empty state rgba(18,19,24,0.72) dashed hairline.)
- CaseView breadcrumb: family gradient @0.82 + hairline 0.10 + radius 10
  (replaces the job-0280 flat rgba(255,255,255,0.05) which assumed a solid
  drawer behind it).
- LayerPanel: floats as ONE translucent card — `position: static` (drops
  the inline absolute pin that stretched it full-height), width auto,
  max-height 100% (internal scroll preserved), family gradient @0.82 +
  hairline 0.10. Deliberate deviation from "per-ROW cards": LayerPanel row
  backgrounds are STATE-DRIVEN inline styles (drag/active tints) that a
  per-row `!important` override would clobber — functionality 100% wins;
  the panel card itself floats and the map reads through it. Its
  pre-existing inline blur is safe (no fixed descendants below it).
- Empty-layers placeholder (App.tsx mobile branch, inline):
  rgba(18,19,24,0.72) dashed-hairline card.
- Footer pills (`inlinePillStyle`, mobile-only variant): float over the
  map now → translucent hairline family (rgba(18,19,24,0.85), hairline
  0.10, radius 999, 12px type).

### 3. Translucent chat sheet (both states)

`mobileSheetContainerStyle` (mobile-only, exported for tests):
- background: solid rgba(20,20,25,0.96) → family gradient at **0.58
  collapsed / 0.68 expanded** (inside the kickoff's 0.55–0.7 window;
  expanded needs the extra scrim for #eee text over a light basemap —
  ~5.9:1 contrast; collapsed is mostly the opaque composer card anyway).
- border #333 → hairline 0.10; radius 14 → **12** (family panel radius);
  shadow softened 0.45 → 0.35. NO backdrop-filter — comment documents the
  ChartGallery containing-block hazard at the site.
- Header divider (mobile branch): #333 → hairline 0.08.
- Handle bar: #555 → rgba(255,255,255,0.35) (reads on any basemap).
- Collapsed active-tool strip: own translucent card now that the sheet
  behind it is see-through — rgba(18,19,24,0.72) + hairline 0.10 + radius 8.
- ChatInput/composer untouched (opaque #1a1a20 keeps the typing surface
  fully legible — the "scrim behind text" the kickoff allows).

### 4. Cohesion + 5. Snap-to-location

All new surfaces use the job-0283 family (hairlines 0.08–0.12, 12px
panel / 8px row / 10px chrome-bar radii, the 26,27,33→18,19,24 gradient at
translucent alphas). Snap-to-location: zero changes to useCases /
case_zoom / bus / Map command paths; `after_mobile_drawer_case.png` shows
the camera on Boise from the Case-open replay.

## Functionality verification (live, dev-seam only)

- Delete ConfirmationDialog from a Case row INSIDE the open drawer:
  bounding box `{x:0, y:333, w:390, h:177}` on a 390x844 viewport —
  viewport-centered with full-screen dim, NOT trapped in the drawer column
  (`after_mobile_delete_dialog.png`). The backdrop-filter hazard pins hold.
- Backdrop tap at (370,400) (outside the drawer column) → drawer count 0.
- Sheet toggle expand/collapse, tool-card injection, composer all exercised
  in the screenshot run.

## Tests — 686 passed / 43 files (baseline 680; zero regressions)

NEW (+6):
- `CaseView.test.tsx` +2: mobile renders NO ← arrow and the Cases link is
  the single back affordance (fires onBack); desktop default keeps both.
- `MobileDrawer.test.tsx` +2: transparent backdrop + surfaceless panel;
  NO backdrop-filter/filter/transform/will-change on the drawer (hosts
  position:fixed ConfirmationDialog — hazard pin).
- `Chat.mobileSheet.test.tsx` +2: translucent family gradient in BOTH
  states with every alpha inside [0.55, 0.7] + hairline border; NO
  backdrop-filter/filter/transform/will-change on the sheet (hosts
  position:fixed ChartGallery — hazard pin).

DELIBERATELY UPDATED (2 pins — this job IS the mobile pass those pins
protected against):
- `Chat.mobileSheet.test.tsx`: sheet radius pin "14px 14px 0 0" →
  "12px 12px 0 0" (family panel radius).
- `BottomRowButtons.test.tsx`: inline-pill pin (radius 14 / #444, the
  job-0280 rendering) → translucent hairline family pin.

Pre-existing (NOT this job): bare `npx tsc --noEmit` fails on 4 unrelated
test files (ws.test.tsx, ws.stickyAnon.test.tsx, Chat.caseTagRouting,
Chat.perCaseStreams — mock-typing drift known since job-0280/0283). My
touched files emit zero tsc errors.

## Desktop-unchanged proof (byte-level, stronger than required)

`evidence/snapshot_0284.mjs` captured 4 desktop scenes (1440x900@2x,
light+dark × root+in-Case, populated via the blessed dev seams, NO running
timer so frames are deterministic) from the PRISTINE tree (job-0284 changes
`git stash push`-ed for the capture, then popped) and again from the final
tree, with identical timings against the live Vite dev server:

**All four before/after pairs are FILE-BYTES-IDENTICAL** (PIL full-frame
diff = zero differing pixels AND raw PNG bytes equal):
desk_root_light, desk_root_dark, desk_case_light, desk_case_dark.
Left-rail and chat-panel crop regions also independently identical. No
desktop pixel moved.

## Evidence (`reports/inflight/job-0284-web-20260611/evidence/`)

- `snapshot_0284.mjs` — capture script (dev-seam injections; no agent).
- `{before,after}_desk_{root,case}_{light,dark}.png` — desktop byte-compare set.
- `{before,after}_mobile_drawer_root.png` — floating Cases cards over the map.
- `{before,after}_mobile_drawer_case.png` — single "Cases" back affordance,
  floating LayerPanel card, Boise snap-to-location visible.
- `{before,after}_mobile_sheet_collapsed.png` — translucent collapsed sheet.
- `{before,after}_mobile_sheet_expanded.png` — translucent expanded sheet
  with replayed bubbles + complete tool card + RUNNING tool card (ticking).
- `after_mobile_delete_dialog.png` — viewport-centered dialog from inside
  the drawer (hazard regression check).

## Risks / notes for the orchestrator

1. **Deliberate scope deviation**: LayerPanel floats as one translucent
   card, not per-row cards (state-driven inline row backgrounds — drag /
   active tints — would be clobbered by `!important` overrides; the
   kickoff's "functionality 100%" outranks per-row floating). Documented
   in the CSS block.
2. Expanded-sheet legibility is tuned to 0.68 alpha; over extremely busy
   light basemaps (dense cadastral linework) secondary #888 text is
   readable but lower-contrast. Easy knob if the user wants more/less map.
3. The drawer's new 138px bottom clearance assumes the collapsed sheet's
   ~126px height; if the composer grows significantly (multi-line drafts
   while the drawer is open), pills could overlap the sheet again —
   cosmetic only, everything stays tappable (drawer is z-above).
4. With the backdrop now invisible, the open drawer's only "modal" cue is
   the floating cards themselves; backdrop tap + Escape still close it
   (unchanged behavior, re-verified).
5. job-0283's report described the inline pills / mobile sheet as
   "byte-identical reference surfaces" — superseded BY DESIGN here (this
   job is the mobile pass); the two affected test pins were updated with
   comments saying exactly that.
6. SettingsPopup.tsx remains untouched and unstaged (pre-existing Tools-
   section modification preserved). main.tsx / EntryRouter / pages (job-
   0285) untouched; job-0285 committed mid-job and `git stash pop` merged
   cleanly around it.

## Files changed

- `web/src/components/MobileDrawer.tsx` — transparent backdrop/panel,
  bottom clearance, family ☰ button, hazard comments
- `web/src/components/CaseView.tsx` — `mobile` prop, arrow gated off
- `web/src/components/CasesPanel.tsx` — header data-testid only
- `web/src/components/BottomRowButtons.tsx` — inline pill family restyle
- `web/src/App.tsx` — drawer CaseView gets `mobile`; mobile empty-layers
  card restyle (mobile branch only)
- `web/src/Chat.tsx` — translucent sheet (both states), header divider,
  handle bar, active-tool strip card (all mobile-gated branches)
- `web/src/styles/global.css` — job-0284 `.grace2-mobile-touch` block
  (floating-card surfaces); job-0280 breadcrumb rule superseded
- Tests: `CaseView.test.tsx`, `components/MobileDrawer.test.tsx`,
  `Chat.mobileSheet.test.tsx`, `BottomRowButtons.test.tsx`
- `reports/inflight/job-0284-web-20260611/{audit.md,report.md,STATE,evidence/*}`
