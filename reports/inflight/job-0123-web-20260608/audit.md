# Audit: Auth web UI — Firebase Auth integration + login flow

**Job ID:** job-0123-web-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** web

**Required reads:**
- `docs/srs/H-auth-and-users.md` (Wave 1.5)
- `packages/contracts/src/grace2_contracts/auth.py` (Wave 2 sibling job-0122)
- `web/src/App.tsx` (existing structure)
- `web/src/ws.ts` (WebSocket client)
- Firebase Auth JavaScript SDK docs

### Scope

Add Firebase Auth to the web client with a minimal login flow.

1. **`web/package.json`**: add `firebase` (Auth SDK v10+)
2. **NEW `web/src/auth.ts`**: Firebase Auth client init, login/logout helpers, ID-token retrieval
3. **NEW `web/src/components/AuthPanel.tsx`**: floating panel (top-right area, next to existing hamburger) — when not signed in: "Sign in with Google" + "Continue as anonymous" buttons; when signed in: display name + avatar + "Sign out"
4. **`web/src/ws.ts`**: after WS connect, automatically send `auth-token` envelope with Firebase ID token (or skip and let server fall back to anonymous)
5. **`web/src/App.tsx`**: wire AuthPanel into top-right region; subscribe to auth state changes
6. **Firebase config**: read public project ID from `.env` (Vite `VITE_FIREBASE_*` vars); document in README
7. **Visual integration**: AuthPanel uses the same panel styling as existing Cases/Layers panels (subtle background, rounded corners, dark-theme aware)

**Tests** (Vitest):
- AuthPanel renders "Sign in" buttons when not authenticated
- AuthPanel renders user info when authenticated (mock Firebase Auth state)
- ws.ts auth-token emission on connect (mock WebSocket + Firebase token)
- App.tsx subscribes to auth changes (rerenders AuthPanel)
- Anonymous fallback: no auth-token sent → connection proceeds

**Live verification** (Playwright):
- Boot web dev server, drive UI: anonymous flow shows "Continue as anonymous" → click → auth-token NOT sent but session proceeds
- Screenshot: AuthPanel in top-right, signed-out state
- Screenshot: AuthPanel after anonymous click

### File ownership (exclusive)

- `web/package.json` — add firebase dep
- `web/src/auth.ts` (NEW)
- `web/src/components/AuthPanel.tsx` (NEW)
- `web/src/AuthPanel.test.tsx` (NEW)
- `web/src/ws.ts` — extend to send auth-token (~20 lines)
- `web/src/App.tsx` — mount AuthPanel + auth state subscription (~30 lines additive)
- `web/.env.example` — document VITE_FIREBASE_* vars
- `web/README.md` — Firebase setup note
- `reports/inflight/job-0123-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 2 job's exclusive files; `reports/complete/**`; `docs/SRS_v0.3.md` monolith (regenerated only); all Wave 1/1.5 atomic tool files (additive use only — don't modify their signatures).

### Concurrency note (Wave 2 fan-out — 16 parallel)

Same idempotent-append pattern + `git pull --rebase` pre-commit mitigation as Wave 1.5. Files all land correctly in HEAD; only commit-message labels may drift. Use marker commits if your changes get swept into a sibling's commit hash.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: verify against real geography, not URL/render consistency.
2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: ALL CRUD goes through `Persistence.*`. Do NOT design custom collection wrappers. If your job needs a new method on Persistence, ADD it (additive) rather than bypassing.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness / behavioral-correctness verified
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

