# Audit: Left rail + nav restructure — Cases hierarchy + Settings/Secrets bottom row

**Job ID:** job-0143-web-20260608, **Sprint:** sprint-12-mega Wave 4, **Specialist:** web (Opus — substantial UX restructure)

**Required reads:**
- `web/src/components/{CasesPanel,LayerPanel,SecretsPanel,Mode2OfferModal,AuthGate,PersistenceChip}.tsx`
- `web/src/App.tsx` (current state machine + layout)
- User direction 2026-06-08: comprehensive UX critiques

### Why

User feedback on the Wave 3.5 screenshots flagged multiple stacked floating UI elements over the map. Restructure the left side into a coherent hierarchy.

### Scope

#### Part 1 — Cases-vs-Case nav hierarchy

When `active_case_id` is null:
- Left rail shows **CasesPanel** (existing — Cases list view) ONLY
- LayerPanel hidden (no layers in a non-Case context — fresh chat session)

When `active_case_id` is set:
- Left rail switches to **CaseView** mode showing:
  - Breadcrumb header: `← Cases / [Case Title]` (clicking arrow returns to Cases-list)
  - LayerPanel BELOW the breadcrumb showing this Case's loaded_layers
  - Cases-list NOT visible in this mode (no scrolled CasesPanel underneath)
- Switching back to Cases-list view via the breadcrumb arrow:
  - Emits `case-command(deselect)` (or just sets active_case_id to null client-side; server handles re-bind on next select)
  - Clears the map back to the global CONUS view
  - LayerPanel disappears

#### Part 2 — Left rail bottom-row buttons (`[Settings] [Secrets]`)

The left panel (whether CasesPanel or CaseView) now has a SHORTER vertical extent (e.g. ends ~24px above the viewport bottom). Underneath the panel, place a 2-button row:

```
+---------------------+
|  CasesPanel /       |
|  CaseView           |
|  ...                |
|                     |   ← left rail ends here
+---------------------+
                          ← 8px gap
[ ⚙ Settings ] [ 🔑 Secrets ]   ← bottom row
```

- Buttons styled as subtle rounded pills, dark-theme aware
- Each opens a full-screen overlay popup (NOT inline panel) — the popup has the controls and a close button (X top-right)

#### Part 3 — Settings popup contents

New `SettingsPopup` component. Contents:
- Section: **Account**
  - Display: user email (or "Anonymous mode" with subtle "Sign in to save your work" CTA)
  - Sign-out button
- Section: **Appearance**
  - Theme toggle (dark/light)
- Section: **About**
  - Build version + commit SHA (read from `import.meta.env.VITE_BUILD_SHA` if set, else "dev")
- Close (X) top-right; click-outside-to-dismiss

#### Part 4 — Secrets popup (restyled)

Take existing `SecretsPanel.tsx` contents and wrap them in the same overlay popup pattern as Settings:
- Full-screen overlay with subtle backdrop
- Centered card with secrets list + add form
- Close (X) top-right

#### Part 5 — Remove identity chip from main app

The existing `PersistenceChip` / identity chip in the top-right gets DELETED. All auth controls live in Settings now. Top-right becomes cleaner (just the hamburger menus from prior work).

#### Part 6 — Save-action login disclaimer

Today: AuthGate forces login OR anonymous-accept before app loads — so "Sign in to save" disclaimers shown unconditionally are noise.

Instead: when an anonymous user attempts a save-triggering action (create a Case, rename a Case, add a layer to a saved Case), show a single inline disclaimer/modal *at that moment*:
- "Anonymous Cases don't sync to your account. Sign in to save?"
- [Sign in] [Continue anyway]

This only appears on save attempts, not on every render. Implementation: extract a `useSaveGate` hook that intercepts save actions for anonymous users.

#### Part 7 — Map zoom controls

MapLibre's zoom controls currently overlap the chat panel area. Reposition:
- Move to **bottom-LEFT** corner (under the left rail bottom-row buttons, with safe-area padding so they don't overlap the Settings/Secrets buttons)
- OR top-right corner if cleaner; pick what doesn't overlap any other UI

**Tests** (Vitest):
- Active case_id null → CasesPanel visible, LayerPanel hidden
- Active case_id set → breadcrumb visible, LayerPanel visible, CasesPanel-list hidden
- Breadcrumb click clears active_case_id + restores Cases-list
- Settings button opens SettingsPopup
- Secrets button opens SecretsPopup
- SettingsPopup contains email + sign-out + theme toggle
- Save-gate fires only when anonymous user attempts save action
- Map zoom controls visible AND not overlapping chat

**Live verification** (Playwright — 6 screenshots):
- `01_cases_root_view.png` — Cases-list left rail, no LayerPanel, clean main view
- `02_case_active_view.png` — breadcrumb + LayerPanel only
- `03_bottom_row_buttons.png` — `[Settings] [Secrets]` under left panel
- `04_settings_popup.png` — settings popup full-screen with sections
- `05_secrets_popup.png` — secrets popup full-screen
- `06_anonymous_save_gate.png` — anonymous save attempt triggers inline disclaimer

### File ownership (exclusive)

- `web/src/components/CasesPanel.tsx` — modify for nav hierarchy
- `web/src/components/CaseView.tsx` (NEW) — breadcrumb + embedded LayerPanel
- `web/src/components/SettingsPopup.tsx` (NEW)
- `web/src/components/SecretsPopup.tsx` (NEW — wraps SecretsPanel contents)
- `web/src/components/BottomRowButtons.tsx` (NEW)
- `web/src/components/PersistenceChip.tsx` — DELETE (controls moved to Settings)
- `web/src/PersistenceChip.test.tsx` — DELETE
- `web/src/hooks/useSaveGate.ts` (NEW)
- `web/src/App.tsx` — layout restructure (~150 lines)
- `web/src/Map.tsx` — repositioning maplibre zoom controls (~20 lines)
- New tests for the above
- `reports/inflight/job-0143-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 4 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: pixel-level evidence required.
2. **Kickoff-front-loaded design**: execute scope, surface OQs, don't redesign.
3. **MongoDB MCP persistence (job-0115)**: use Persistence.* — no custom CRUD.
4. **Concurrent web jobs**: App.tsx will be touched by multiple Wave 4 jobs. Pre-commit `git pull --rebase` before commit. Idempotent-append discipline; if conflict, re-apply your specific changes.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] Live Playwright verification per kickoff (screenshots of NEW visual state vs old)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

