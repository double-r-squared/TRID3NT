# Audit: Auth-as-page — full-screen gate replacing AuthPanel

**Job ID:** job-0138-web-20260608, **Sprint:** sprint-12-mega Wave 3.5, **Specialist:** web

**Required reads:**
- `web/src/components/AuthPanel.tsx` (Wave 2 job-0123 — being REPLACED)
- `web/src/App.tsx` (auth state subscription)
- `web/src/auth.ts` (Firebase Auth client init)
- Memory: `feedback_geographic_clipping_pattern` (analogous pattern: deliberate UX choice points before computation)

### Scope (user direction 2026-06-08)

"the auth shouldn't be a panel it should be a page that keeps us gated from using the app... let's make it its own page"

REPLACE the floating top-right AuthPanel with a **full-screen gating page** that renders BEFORE the main app surfaces when no auth decision has been made.

1. **NEW `web/src/components/AuthGate.tsx`**:
   - Full-viewport overlay covering map + chat + all panels
   - Centered card: GRACE-2 logo/wordmark + tagline + 2 primary actions
   - Primary CTA: "Sign in with Google" (Firebase Auth signInWithPopup)
   - Secondary CTA: "Continue without saving (anonymous)" — sets a local-storage flag `grace2_anonymous_accepted=true` + proceeds to app
   - Footer link: small "Why sign in?" → explanatory modal (saves Cases, syncs across devices, unlocks Tier-2 APIs)
   - Dark theme aware (use existing CSS vars)
   - Logo can be text-only for v0.1 ("GRACE-2" wordmark)
2. **`web/src/App.tsx` gate logic**:
   - Subscribe to Firebase auth state
   - Compute `appShouldRender`:
     - `true` if Firebase user is authenticated AND not anonymous, OR
     - `true` if `grace2_anonymous_accepted` local-storage flag is set, OR
     - `false` otherwise
   - When `appShouldRender === false`: render `<AuthGate />` (full screen)
   - When `appShouldRender === true`: render the normal app (map + chat + panels)
3. **DELETE `web/src/components/AuthPanel.tsx`** + remove top-right mount from App.tsx
4. **Authenticated-state indicator**: small persistence chip near top-right showing user email/anonymous + sign-out (this is the residual "auth UI in the app" — much smaller than the old panel)
5. **Sign-out flow**: returns to AuthGate
6. **Anonymous → authenticated upgrade**: if user signed in while in anonymous mode, clear the anonymous flag + show toast "Welcome back — your Cases will now sync"

**Tests** (Vitest):
- AuthGate renders when no auth + no anonymous flag
- Main app renders when authenticated
- Main app renders when anonymous flag is set
- "Continue without saving" sets the flag + transitions to app
- Sign-out returns to AuthGate
- Why-sign-in modal opens + closes

**Live verification** (Playwright):
- Boot dev server with cleared local-storage
- Page loads → AuthGate visible (full-screen)
- Click "Continue without saving" → app appears
- Screenshot: AuthGate state + post-anonymous app state
- Reload page → app still loads (flag persisted)
- Click sign-out → AuthGate again

### File ownership (exclusive)

- `web/src/components/AuthGate.tsx` (NEW)
- `web/src/AuthGate.test.tsx` (NEW)
- `web/src/components/AuthPanel.tsx` — DELETE
- `web/src/AuthPanel.test.tsx` — DELETE
- `web/src/components/PersistenceChip.tsx` — MAY UPDATE (small residual auth indicator)
- `web/src/App.tsx` — gate logic + remove AuthPanel mount (~50 lines)
- `web/src/auth.ts` — additive: `signOut` + `getCurrentUser` helpers if missing
- `web/src/App.test.tsx` — extend with gate logic tests
- `reports/inflight/job-0138-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 3/3.5 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs, don't redesign.
3. MongoDB MCP persistence (job-0115): use Persistence.* — no custom CRUD.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

