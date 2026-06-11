# job-0278-web-20260611 — mobile-friendly UI (purely UI): report

**Verdict: DONE** — mobile shell (drawer + bottom sheet) landed behind a
`useIsMobile()` guard; desktop unchanged; 616/616 vitest green (590 baseline
+ 26 new, zero regressions); static 390x844 screenshots + a 1440x900 desktop
regression control captured against the live dev server without sending any
chat prompts.

## What landed

### 1. `web/src/hooks/useIsMobile.ts` (new)
- `MOBILE_BREAKPOINT_PX = 768`; mobile ⇔ `(max-width: 767px)` matches.
- SSR-safe (`false` without `window`/`matchMedia`), listens for
  `MediaQueryList` `change` events (rotation/resize re-renders), legacy
  `addListener` fallback, full cleanup on unmount.
- This hook is the SINGLE guard for every mobile branch — desktop ≥768px
  takes the exact pre-existing code paths.

### 2. Left rail → slide-in drawer (mobile)
- `web/src/components/MobileDrawer.tsx` (new): `MobileDrawerButton` (top-left
  ☰, 44x44, `aria-expanded`/`aria-controls`) + `MobileDrawer` (backdrop
  rgba(0,0,0,0.45) z=40 + full-height left panel `min(320px, 85vw)` z=41,
  `role="dialog"`). Closed ⇒ nothing in the DOM; backdrop tap closes.
- App.tsx mounts the SAME rail content inside it: `CasesPanel` at root
  (selecting a Case closes the drawer), `CaseView` + `LayerPanel` inside a
  Case (LayerPanel positions itself inside a `position:relative; flex:1`
  wrapper), and the Settings/Secrets pills folded into the drawer footer via
  a new `BottomRowButtons variant="inline"` (opening either popup closes the
  drawer first). The desktop floating pills are suppressed on mobile (they
  would collide with the bottom sheet).
- Touch targets: `global.css` gains a `.grace2-mobile-touch`-scoped rule
  (class applied only by MobileDrawer) bumping Case-row buttons, + New Case,
  breadcrumb back/Cases, and the pills to min 44x44. LayerPanel's 16–22px
  drag/eye/slider cluster is deliberately EXCLUDED — bumping it would break
  the 288px row layout (kickoff: "bump hit area on mobile only if trivial").

### 3. Chat → bottom sheet (mobile)
- `web/src/Chat.tsx`: new `mobile` prop (presentation ONLY — the job-0266/
  0277 per-Case stream map, envelope routing, and scroll/auto-scroll
  machinery are untouched). Exported primitives for tests:
  `mobileSheetContainerStyle(expanded)` (bottom-pinned, full width, top-only
  border radius, z=32; `70vh` expanded / content-height collapsed) and
  `SheetToggleHandle` (full-width 44px grab-bar + chevron, `aria-expanded`).
- Collapsed = handle + composer pinned at the bottom (full width). Expanded
  = 70vh sheet with header + conversation scroll. The scroll area and header
  hide via `display:none` but STAY MOUNTED, so stream state, scroll
  position, and auto-scroll survive toggling. Submitting from collapsed
  auto-expands the sheet so the user sees the response stream in.
- The composer is in normal flow on mobile (not the desktop absolute
  overlay) with `env(safe-area-inset-bottom)` clearance; `ChatInput` gains a
  `fontSizePx` prop — 16px on mobile to defeat the iOS focus auto-zoom,
  default 14px on desktop (pixel-identical).
- Desktop right-collapse + both desktop hamburgers don't apply on mobile
  (the sheet is always mounted, its collapsed state IS the minimized form);
  the in-header `›` collapse chevron is hidden on mobile.

### 4. App.tsx mobile repositions
- `LayerLegend` rides in a zero-height offset wrapper (bottom: 116) so it
  clears the collapsed sheet; the expanded sheet (z=32) covers it naturally.
- `inline-chat-card-stack` (payload-warning gates + source suggestions):
  full-width 12px-gutter column on mobile (desktop 340px column anchored to
  the chat panel would clip at 390px).
- Upgrade toast: `right: 12` on mobile (desktop offsets assumed the 380px
  side panel and pushed it off-screen).

### 5. Verified, not changed
- `web/index.html` already carries `<meta name="viewport"
  content="width=device-width, initial-scale=1.0" />`.
- Modals already fit 390px: SaveGateModal `min(420px, 92vw)`,
  ConfirmationDialog `maxWidth: 90vw`, Settings/Secrets `min(480px, 92vw)`,
  ToolsCatalog `min(820px, 96vw)`, ImpactPanel `min(520px, 96vw)`,
  ChartGallery `min(…, 96vw)`. No edits needed (kickoff item 6 satisfied).

## Tests — 616 passed (39 files), baseline 590, zero regressions
New (26): `web/src/hooks/useIsMobile.test.tsx` (10 — constants, SSR-safety,
initial read, change events, legacy fallback, unmount cleanup),
`web/src/components/MobileDrawer.test.tsx` (6 — closed=empty DOM, open
renders backdrop/panel/children, backdrop-tap closes, inner clicks don't,
full open→close cycle in App wiring shape), `web/src/Chat.mobileSheet.test.tsx`
(8 — container style contract, handle a11y/toggle, collapsed→expanded→
collapsed cycle with content kept mounted), `BottomRowButtons.test.tsx` (+2 —
floating default, inline variant in normal flow).

## Evidence (static shell only — NO prompts sent, no inject seams)
Captured by `web/tools/screenshot_job0278_mobile.mjs` against the live Vite
dev server (localStorage anonymous-accepted pre-seeded; the app's own WS
connect to :8765 was left alone):
- `evidence/mobile_root_collapsed_sheet.png` — 390x844 root: basemap + ☰
  drawer button + collapsed sheet (handle + composer).
- `evidence/mobile_drawer_open.png` — drawer over backdrop: CasesPanel +
  Settings/Secrets pills in the footer.
- `evidence/mobile_sheet_expanded.png` — sheet at 70vh with header +
  conversation area + composer.
- `evidence/desktop_root_regression.png` — 1440x900 control: desktop layout
  unchanged (script also asserts no mobile testids exist on desktop and no
  desktop rail/hamburger testids exist on mobile).

## Risks / compromises for the orchestrator
1. **In-Case mobile drawer + LayerPanel and the expanded-sheet conversation
   were NOT live-verified with real layers/streams** — that requires driving
   Gemini, which the kickoff forbids. The drawer hosts the identical
   component instances desktop uses, and the sheet hides/shows the identical
   scroll DOM, so risk is low; bundle a visual check with the next live
   workflow per `feedback_bundle_ui_verification_with_existing_queries`.
2. **Map gestures vs. the sheet**: the collapsed sheet occupies the bottom
   ~120px, the legend sits above it; map pan/pinch elsewhere is untouched.
   No `touch-action` tuning was done on the map canvas (MapLibre handles
   pinch natively).
3. **LayerPanel inner controls keep desktop sizes** (16–22px drag/eye/slider)
   inside the drawer — deliberate, see touch-target note above.
4. **`data-sheet-state` attribute** on `grace2-chat` is new (undefined on
   desktop) — available for future Playwright assertions.
5. Pre-existing unrelated working-tree changes (e.g. `SettingsPopup.tsx`
   Wave 4.10/4.11 props) were left uncommitted, per kickoff.
6. The expanded-sheet screenshot shows `reconnecting` in the header — an
   artifact of two screenshot browser contexts cycling WS connections to the
   live agent; not a UI defect (root shot connected fine).
