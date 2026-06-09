# Audit: Secrets popup flatten — drop card-within-popup nesting

**Job ID:** job-0151-web-20260608, **Sprint:** sprint-12-mega Wave 4.5, **Specialist:** web (Sonnet small)

**Required reads:**
- `web/src/components/SecretsPopup.tsx` (job-0143 — wraps SecretsPanel inside overlay)
- `web/src/components/SecretsPanel.tsx` (Wave 2 — has its own card styling)

### Why

User direction 2026-06-08: "in the secrets section it's a card within a popup it should just be a popup no need to add the apikeys card within it just lay it out within the popup". Currently SecretsPopup wraps SecretsPanel which has its own card styling — visually nested.

### Scope

1. Either (a) refactor SecretsPanel content to drop its outer card styling and let SecretsPopup provide the overlay/card, OR (b) drop SecretsPopup's inner wrapper card and let SecretsPanel be the popup content directly
2. Goal: ONE popup-overlay surface with the secrets list + add form laid out flat inside — no double-card nesting
3. Same width + close-X position as SettingsPopup (visual parity)

**Tests**: SecretsPopup test confirms single card depth (no nested cards)

**Live verification**: Playwright screenshot of secrets popup showing flat layout (no inset)

### File ownership (exclusive)

- `web/src/components/SecretsPopup.tsx` — restructure
- `web/src/components/SecretsPanel.tsx` — may need style adjustment for embedding
- `web/src/SecretsPopup.test.tsx`
- `reports/inflight/job-0151-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Sibling Wave 4.5 files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence.
2. Kickoff-front-loaded design: execute scope, surface OQs.
3. UX language discipline: no internal terms ("Mode 1/2", "Tier", "OQ-*") in user-facing surfaces.
4. Pre-commit: `git pull --rebase` before commit.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix; co-author line
- [ ] Returns commit SHA + outcome + headline + evidence + OQs

