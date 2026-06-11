# job-0283 — web — desktop sleekness pass — report

STATE: DONE. Desktop UI aligned to the job-0264/job-0280 design language
(hairline borders, 12px panel / 8px row radii, gradient surfaces, soft
shadows) — visual only, zero behavior change, every control/id/handler
intact. 642/642 vitest green (638 baseline + 4 new; ZERO existing tests
updated). Mobile proven byte-identical (pixel-diff below). HMR kept
compiling at every save; nothing restarted.

## Design language applied (reference = LayerPanel job-0264 polish)

- Panels: `linear-gradient(180deg, rgba(26,27,33,0.96), rgba(18,19,24,0.96))`,
  `1px solid rgba(255,255,255,0.06)`, radius 12, `0 8px 32px rgba(0,0,0,0.5)`.
- Inner rows / inner cards: `rgba(255,255,255,0.03)` + hairline + radius 8;
  active row `rgba(59,130,246,0.16)` + `rgba(59,130,246,0.55)` border.
- Modals: hairline `rgba(255,255,255,0.10)` (replacing solid #444), section
  dividers `rgba(255,255,255,0.08)` (replacing #333), buttons radius 8.
- Floating chrome (pills/hamburgers): `rgba(18,19,24,0.92)` + hairline
  `rgba(255,255,255,0.08)` + blur; pills radius 999, hamburgers radius 10.

## What changed, by surface

### 1. Left rail — via NEW `.grace2-desktop-rail` CSS scope (global.css)

The exact inverse of job-0280's `.grace2-mobile-touch` pattern: the class is
applied ONLY by App.tsx's two desktop rail wrappers (cases-list + case-view
modes, both `!isMobile`-gated), so CasesPanel / CaseView / CaseRow inline
styles are byte-untouched and the mobile drawer renders pixel-identical.
`!important` needed for the same documented reason (inline styles beat
stylesheets). Rules:

- CasesPanel: gradient surface + hairline + radius 12 + shadow; **width
  260 → 280** so the rail no longer jumps width when opening a Case
  (CaseView/LayerPanel column is 280) — sizing consistency, no layout
  structure change.
- Case rows: flat `rgba(255,255,255,0.03)` + hairline + radius 8 (was
  #333-bordered boxes-in-a-box), hover tint, active keeps the blue
  highlight (softened border), hazard chip border softened.
- "+ New Case" radius 8; empty-state dashed hairline.
- CaseView breadcrumb: gradient + hairline + radius 10 + blur (was
  #333/8px slab). Empty-layers placeholder (desktop instance): dashed
  hairline + radius 10.

### 2. Chat panel (desktop branch only — Chat.tsx)

- Desktop container extracted to exported `desktopChatContainerStyle`
  (mirrors the `mobileSheetContainerStyle` test-export pattern): radius
  8 → 12, added hairline border, flat `rgba(20,20,25,0.92)` → family
  gradient, shadow upgraded to the family value. Geometry pinned unchanged
  by test (right/top/bottom 16, width 380).
- Header: desktop gets hairline divider + LayerPanel header padding
  (12px 14px); mobile keeps `#333` + `10px 12px` byte-identical via the
  existing `mobile` conditional.
- Composer (ChatInput) untouched — it already led the family (hairline,
  radius 14, #1a1a20).

### 3. Pills + hamburgers + legend

- BottomRowButtons: `pillStyle` forked by variant — `floating` (desktop)
  joins the family (radius 999, hairline, blur, 12px type); `inline`
  (mobile drawer footer) keeps the job-0280 rendering byte-identical.
- Hamburger buttons (App.tsx, desktop-only): #444/radius-6 → hairline /
  radius 10 / blur / soft shadow.
- LayerLegend: added hairline border, radius 8 → 10, blur 4 → 6, bg
  rgba(15,15,20,0.72) → rgba(17,18,23,0.78). **Form-factor-shared by
  design** — the legend is not a job-0280 drawer/sheet surface; the family
  alignment deliberately applies on mobile too (noted, see mobile proof).

### 4. Modals (form-factor-shared by design — not job-0280 surfaces)

- SettingsPopup: card border hairline 0.10, section dividers 0.08, buttons
  hairline + radius 8, close radius 8. (Surgical staging — see below.)
- SecretsPopup + SecretsPanel: card hairline, close radius 8; panel inputs/
  buttons #555/4px → hairline/8px; secret-row divider #333 → hairline.
- SaveGateModal: card hairline; buttons radius 8 + hairline.
- ConfirmationDialog: card #444/radius 8 → hairline 0.10 / radius 12;
  buttons radius 4 → 8.
- ToolsCatalogPopup: card hairline; search input + close radius 8 +
  hairline; category chips hairline (active #3b82f6 state untouched);
  list dividers hairline.

## Bug caught and fixed during the screenshot pass

First after-capture showed the delete ConfirmationDialog rendering pinned
inside the 280px CasesPanel column instead of viewport-centered: the new
`backdrop-filter` on the panel made it the CSS **containing block for
position:fixed descendants**. Removed `backdrop-filter` from the two
surfaces that host fixed-position children — CasesPanel (hosts
ConfirmationDialog) and the desktop Chat container (hosts ChartGallery,
`position:fixed` full-viewport) — with code comments explaining why it must
never come back. Their 0.96-alpha surfaces hide blur anyway. Leaf surfaces
(breadcrumb, hamburgers, pills, legend) keep blur safely. Re-captured: the
dialog centers with full-viewport dim, identical to before.

## Tests — 642 passed (40 files), zero regressions

- Baseline re-run before work: 638/638. After: 642/642.
- **Zero existing style-asserting tests needed updating** (verified: the
  pinned styles — mobile sheet radius "14px 14px 0 0", ChatInput #1xxxxx bg,
  UserBubble, ThinkingIndicator chrome, PipelineCard tints — are all
  untouched surfaces).
- 4 NEW tests:
  - `BottomRowButtons.test.tsx` (+2): floating pills = desktop family
    (radius 999 + hairline 0.08); inline pills = job-0280 mobile rendering
    pinned byte-stable (radius 14 + #444).
  - `Chat.test.tsx` (+2): `desktopChatContainerStyle` joined the family
    (radius 12 / hairline / gradient / shadow) AND geometry unchanged
    (absolute, right/top/bottom 16, width 380, overflow hidden).

## Evidence (static screenshots, dev-seam injections — NOT e2e)

`reports/inflight/job-0283-web-20260611/evidence/` via `snapshot_0283.mjs`
(1440x900 desktop @2x, 390x844 mobile @2x, against the live Vite dev
server; case-list/case-open/case-open-chat/pipeline-state seams per their
blessed component-screenshot use). Before set captured from the pristine
tree before any edit; after set from the final tree:

- `{before,after}_root_{light,dark}.png` — root: populated CasesPanel +
  chat + pills.
- `{before,after}_case_{light,dark}.png` — in-Case: breadcrumb + populated
  LayerPanel (3 layers) + chat with replayed user/agent bubbles + complete
  tool card + RUNNING tool card (ticking timer) + legend.
- `{before,after}_settings_modal.png`, `{before,after}_secrets_modal.png`,
  `{before,after}_confirm_dialog.png` — modal family.
- `{before,after}_mobile_sheet_collapsed.png`, `{before,after}_mobile_drawer.png`
  — mobile control.

**Mobile-unchanged proof (pixel-level):** the drawer region (left 320 css
px, full height) of `mobile_drawer` and the sheet region (bottom ~150 css
px, full width) of `mobile_sheet_collapsed` are **byte-IDENTICAL** between
before and after captures (PIL bytes comparison; run inline, see job log).
job-0280's drawer/sheet surfaces are untouched.

Both themes checked: dark-chrome panels hold contrast on the light map
(hairline + shadow separate them) and on the dark map (gradient surface
reads above the near-black basemap; the new hairlines are what keep panel
edges legible there — see `after_*_dark.png`).

## SettingsPopup — surgical staging (pre-existing uncommitted change)

`web/src/components/SettingsPopup.tsx` carried a PRE-EXISTING uncommitted
modification (the Wave 4.10/4.11 Tools section: `onOpenToolsCatalog` /
`onOpenRoutingDashboard` props + Tools JSX). Per kickoff it stays
uncommitted. My 4 style-constant edits touch lines identical in HEAD and
working tree, so the staged blob was built as HEAD content + style edits
only (`git hash-object -w` + `git update-index --cacheinfo`): the commit
contains ONLY the job-0283 style changes; the Tools-section change remains
unstaged in the working tree exactly as found.

## Risks / notes for the orchestrator

1. **Deliberate cross-form-factor deltas** (noted above, kickoff scope 3+4):
   LayerLegend + the five modals render identically on mobile, so their
   family alignment shows there too. They are NOT job-0280 drawer/sheet
   surfaces; those are proven byte-identical.
2. The `.grace2-desktop-rail` CSS uses `!important` against inline styles —
   same documented trade-off as job-0280's `.grace2-mobile-touch` block.
   Anyone restyling CasesPanel/CaseView inline must check that block.
3. **backdrop-filter ⇒ containing-block hazard** (the bug fixed above) is
   now documented at both removal sites; future polish must not re-add
   blur to any surface hosting `position:fixed` children (ConfirmationDialog,
   ChartGallery — and any future fixed overlay mounted inside a panel).
4. Pre-existing (NOT from this job): bare `npx tsc --noEmit` fails on 4
   unrelated test files (vitest mock typing drift — ws.test.tsx etc., known
   since job-0280). Touched files verified clean.
5. The chat header's "M1 stub" chip and connection-status copy were left
   verbatim (kickoff: no control/text removal; it is shared with the mobile
   sheet). Candidate for a future copy-cleanup job.

## Files changed

- `web/src/styles/global.css` (new `.grace2-desktop-rail` block)
- `web/src/App.tsx` (rail wrappers get the class; hamburger family)
- `web/src/Chat.tsx` (exported desktopChatContainerStyle; header hairline)
- `web/src/Chat.test.tsx` (+2 tests)
- `web/src/components/BottomRowButtons.tsx` (variant style fork)
- `web/src/BottomRowButtons.test.tsx` (+2 tests)
- `web/src/components/LayerLegend.tsx`
- `web/src/components/SettingsPopup.tsx` (style constants only — staged surgically)
- `web/src/components/SecretsPopup.tsx`
- `web/src/components/SecretsPanel.tsx`
- `web/src/components/SaveGateModal.tsx`
- `web/src/components/ConfirmationDialog.tsx`
- `web/src/components/ToolsCatalogPopup.tsx`
- `reports/inflight/job-0283-web-20260611/{audit.md,report.md,STATE,evidence/*}`
