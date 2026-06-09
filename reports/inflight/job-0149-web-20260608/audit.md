# Audit: Vector palette collision fix

**Job ID:** job-0149-web-20260608, **Sprint:** sprint-12-mega Wave 4.5, **Specialist:** web (Sonnet small)

**Required reads:**
- `web/src/lib/vector_rendering.ts` (job-0146)
- `reports/inflight/job-0148-testing-20260608/evidence/scenario11_layer_colors.json`

### Why

Wave 4 verification surfaced bug: panther + spoonbill both render as `#4477FF` (only alligator differs as `#98FF98`). Curated palette dispatch has a collision — investigate `paletteColorFor`/`presetColorFor`/style_preset routing.

### Scope

1. Diagnose: read `vector_rendering.ts`; check why two distinct layer_ids resolve to the same palette color
2. Fix: ensure deterministic palette gives DIFFERENT colors per layer_id; check that `style_preset` overrides don't accidentally collide
3. Acceptance: re-test 3 species against same bbox → 3 distinct colors in `dom_layer_inventory.json`

**Tests**: extend test_vector_rendering.test.ts with a 3-species explicit assertion (no two layer_ids return same color from `paletteColorFor`)

**Live verification**: Playwright dev-injection of Case 1 layers → screenshot showing 3 distinct species colors

### File ownership (exclusive)

- `web/src/lib/vector_rendering.ts` — fix only
- `web/src/lib/vector_rendering.test.ts` — extend
- `reports/inflight/job-0149-web-20260608/`


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

