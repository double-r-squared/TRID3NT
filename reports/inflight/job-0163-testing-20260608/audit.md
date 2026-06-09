# Audit: Wave 4.6 Playwright verification — live demo confirmation

**Job ID:** job-0163-testing-20260608, **Sprint:** sprint-12-mega Wave 4.6 Stage B, **Specialist:** testing (Opus — gated on Stage A landing)

### Why

After Wave 4.6 fixes (layer emission, auto-zoom + zoom-first, Cases dev fallback, web UI bundle), validate the complete end-to-end demo works:

1. Open app → AuthGate → anonymous continue
2. Create a Case ("Fort Myers flood study")
3. Send "Model peak flood depth from a 100-year design storm in Fort Myers, FL"
4. **Within ~5s**: map zooms to Fort Myers area (zoom-first UX)
5. **Within ~10s**: pipeline cards roll in with state visuals (grey → rainbow + spinner → green/red)
6. **Within ~6 min**: SFINCS completes + flood layer appears on map + raster visible
7. Collapse panel via chevron → chat preserved on re-expand
8. Case persists across restart (JSON file in dev_persistence/)

Capture 7 screenshots → `reports/inflight/job-0163-testing-20260608/evidence/`:
1. AuthGate
2. Anonymous app + Cases empty state
3. Case created + active
4. Prompt sent + map zoomed (zoom-first proof, BEFORE SFINCS finishes)
5. Pipeline cards in mid-run with rainbow gradient + spinner visible
6. Flood layer rendered on map with auto-zoom to bbox
7. Chat panel collapsed via chevron + re-expanded with content preserved

### File ownership

- `reports/inflight/job-0163-testing-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 4.6 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs.
3. UX language discipline: no internal terms in user-facing surfaces.
4. Pre-commit: `git pull --rebase` before commit.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix; co-author line
- [ ] Returns commit SHA + outcome + headline + evidence + OQs

