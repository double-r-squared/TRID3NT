# job-0253 — Firebase Auth SDK wiring (web client) — FROZEN KICKOFF

**Specialist:** web
**Sprint:** 13.5 Stage 1
**Model:** Opus
**Opened:** 2026-06-11
**Depends on:** sprint-13 close (manifest); runs in parallel with job-0251b (infra/) + job-0252b (services/agent/) — ZERO file overlap with either; do not touch their trees.

## Binding decisions
- `reports/sprints/sprint-13-5-manifest.md` job-0253 scope + `reports/sprints/sprint-13-5-decisions.md`: Decision 4 (OAuth consent "GRACE-2", Testing mode), Decision 6 (**production requires sign-in; anonymous is dev-only** — the prod Firebase project has the anonymous provider DISABLED per job-0250), Decision 10 (canonical owner identity = internal users._id ULID; the client's Firebase uid is a credential, not the owner key — display identity from the auth-ack, never assume uid==owner id).
- Standing quota constraint: NO Gemini/Vertex generate calls.

## Pre-existing substrate (verify-then-harden — NOT greenfield)
sprint-12 Wave 2 (job-0123) already built:
- `web/src/auth.ts`: lazy Firebase init gated on `VITE_FIREBASE_PROJECT_ID` (absent → status "disabled", dev boots with no Firebase), Google popup sign-in, anonymous sign-in, sign-out, `getIdToken()`, auth-state subscription shim, `__setAuthForTesting` seam.
- `web/src/ws.ts`: sends the `auth-token` envelope FIRST on connect (`sendAuthToken` ~:878-920) with the ID token from `getIdToken()` + sticky `anonymous_user_id` hint; handles the auth-ack (`firebase_uid`, `tier`, `is_anonymous`). NOTE: browsers cannot set WS headers — the manifest's "header" wording is wrong; the envelope IS the transport (SRS H.5 / A.x). Do not invent a header path.
- Agent side (job-0252, DONE): `AUTH_REQUIRED=true` → unauthenticated/forged-token sockets get an A.6 `AUTH_FAILED` error envelope then close code **4401**. Dev (no env) keeps anonymous fallback verbatim.
- `web/src/EntryRouter.tsx` (job-0285): "/" → Landing (no session), "/app" → app, "/privacy" → Privacy.

## The DELTA (what THIS job adds)
1. **`web/src/hooks/useAuth.ts`** (new): React hook over auth.ts — `{ user, status, signInWithGoogle, signOut }`, re-render on auth-state change, no Firebase types leaked.
2. **`web/src/components/AuthGuard.tsx`** (new): wraps the APP entry only (not Landing/Privacy). Behavior:
   - Firebase **disabled** (`VITE_FIREBASE_PROJECT_ID` absent — every dev/tailnet session today): render children unchanged. The live demo must be pixel-identical; this is the load-bearing constraint.
   - Firebase **enabled** + no signed-in user: render the sign-in page (minimal: GRACE-2 wordmark, "Sign in with Google" button, link to /privacy; visual language matches the job-0285 landing — dark, sans-serif stack, hairline cards; NOT onboarding polish, that's job-0258). No "continue as anonymous" on this surface — Decision 6 (the auth.ts anonymous helper stays for dev/tests; do not delete, do not surface in prod UI).
   - Enabled + signed-in: render children; token flows through the EXISTING ws.ts envelope path (verify, don't rewrite).
3. **4401 handling in ws.ts**: on close code 4401 / `AUTH_FAILED`, do NOT enter the reconnect loop (an invalid token would hammer the gate) — surface an auth-expired state the guard maps to the sign-in page; a fresh `getIdToken(forceRefresh)` retry once is acceptable before giving up.
4. **Sign-out affordance**: minimal — in the existing Settings popup (one button, calls signOut, drops to the guard). Touch `SettingsPopup.tsx` ONLY if it is clean in git status at your start; it currently carries unrelated uncommitted edits — if still dirty, put sign-out on the sign-in/guard surface instead and flag in the report.
5. **Tests (vitest)**: guard renders children when disabled (snapshot-stable); sign-in page when enabled+signed-out (fake auth seam); children when enabled+signed-in; 4401 → no reconnect storm + auth-expired surfaced; token still injected into the auth-token envelope (existing ws tests stay green). Full web suite green (currently ~686).

## Hard constraints
- NO Gemini/Vertex calls. Do NOT restart/disturb the running dev agent or the served web dev server's behavior under current env (no VITE_FIREBASE_PROJECT_ID set → everything must look and behave identically).
- File ownership: `web/src/hooks/useAuth.ts` (new), `web/src/components/AuthGuard.tsx` (new), `web/src/auth.ts` (harden only), `web/src/ws.ts` (4401 handling, additive), `web/src/EntryRouter.tsx` or `web/src/App.tsx` (guard wiring only, additive), tests. NOTHING under `infra/` or `services/agent/`.
- Do NOT stage files you didn't change; never `git add -A` (the tree carries unrelated uncommitted edits, including `SettingsPopup.tsx` and Wave 4.10 docstring drift).
- Commit `job-0253: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
`reports/inflight/job-0253-web-20260611/{report.md,STATE=IN_REVIEW}`; report documents the guard's three-mode matrix with test evidence and screenshots if cheap (Playwright against the local dev server with the fake-auth seam is fine — NO live Firebase needed; the real-provider live-verify happens at job-0259 after deploy). 4-lens adversarial panel follows at orchestrator level.
