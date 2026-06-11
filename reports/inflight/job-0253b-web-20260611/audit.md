# job-0253b — auth wire-order fix (auth-token FIRST) + re-sign-in reconnect + tsc hygiene (FROZEN KICKOFF)

**Specialist:** web
**Sprint:** 13.5 Stage 1 carry-over (panel-job-0253 majors; both prod-only, dev-invisible)
**Model:** Opus
**Opened:** 2026-06-11
**Depends on:** job-0253 DONE (panel 4/4 — read the verdict section at the end of `reports/inflight/job-0253-web-20260611/report.md` FIRST; the panel's probe files under that job's `verify/` are your starting evidence).

## Finding 1 (MAJOR, blocks prod connect entirely) — session-resume races ahead of auth-token
`web/src/ws.ts:679` sends `session-resume` synchronously in the "open" handler; `maybeSendAuthToken` (:684) awaits `getIdToken()` (:947) before emitting `auth-token` (:981). The agent's gate dispatches in arrival order and rejects the FIRST non-auth-token frame under `AUTH_REQUIRED=true` (`server.py:4047-4063` → `_ensure_auth_handshake` → 4401 "auth-token envelope required before any other message"). Net: a signed-in prod user's valid token is never read; every prod connection 4401s.

**Fix:** `auth-token` must be the FIRST envelope on every connection — await `maybeSendAuthToken()` (or restructure the open handler) so `session-resume` (and anything else queued at open) is emitted only AFTER the auth-token send completes. Preserve: (a) dev/anonymous behavior — the auth-token envelope is ALREADY always sent (even with empty token, job-0172 Part C sticky-anon hint), so ordering-only change keeps dev byte-equivalent at the protocol level; verify no test depends on resume-first; (b) failure of getIdToken must not wedge the open handler (timeout/fallback to empty-token send — keep whatever guard exists); (c) both GraceWs instances (App + Chat) go through the same code path — fix once in ws.ts.

## Finding 2 (MAJOR) — re-sign-in renders over a terminally dead socket
`handleAuthFailure`'s give-up branch (`ws.ts:1032-1035`) emits `onStatus("disconnected")` + `onAuthExpired` and never schedules a reconnect (correct — don't hammer the gate). But nothing reconnects LATER: App's ws effect deps (`App.tsx:567`) are all stable, `onAuthChanged` (:233-238) only clears `authExpired`, Chat's effect keys on `[wsUrl, bump]` (`Chat.tsx:1276`). After re-sign-in the guard renders children over dead sockets until a page reload.

**Fix (design yours, properties non-negotiable):** when a fresh non-anonymous user lands while/after `authExpired`, BOTH GraceWs instances reconnect (e.g. App tracks an `authEpoch` incremented on recovered sign-in, threaded to both effects' deps — or explicit `wsRef.current?.connect()` calls; remember the panel proved `connect()` resets the latches at ws.ts:424-427, so a plain connect() suffices per instance). Reconnect must NOT fire in disabled/dev mode (no Firebase → no authExpired ever — keep it that way) and must not double-connect an already-open socket. Also close OQ-0253-CHAT-WS-4401 while you're here: Chat's instance must participate in the recovery (it already latches/suppresses; it needs the reconnect trigger).

## Finding 3 (minor, report hygiene) — tsc
The new `AuthGuard.test.tsx` added 6 mock-typing errors (`vi.fn<[],Promise<...>>()` two-type-arg form; lines ~26-84). Fix them (33 → 27 total; zero in production files before and after). Do NOT touch the 27 pre-existing errors in untouched files.

## Tests
- Wire-order: a test that captures the envelope sequence on a fresh connection and asserts `auth-token` strictly precedes `session-resume` (both with a token and with the empty-token anonymous path); run the agent-side gate semantics in your head against it — under the gate the first frame must be auth-token.
- Re-sign-in: 4401 → give-up → simulate fresh non-anon auth state → BOTH instances open new sockets exactly once (count via the existing fake-socket harness `openedSockets()` used by the panel probes); disabled mode → no reconnect machinery engages; already-open socket → no duplicate connect.
- Existing 706 stay green (never weaken; the panel's 12 probe files under job-0253's verify/ should also pass against your fix — run them).
- `npx tsc --noEmit` → 27 errors, none in changed/new files.

## Hard constraints
- NO Gemini/Vertex calls. Do NOT restart/disturb the dev agent or the :5173 dev server (read-only checks fine; own vite instances on other ports reaped).
- Files owned: `web/src/ws.ts`, `web/src/App.tsx`, `web/src/Chat.tsx` (reconnect trigger only — this file is large and demo-critical; keep the hunk minimal), `web/src/components/AuthGuard.tsx` / `web/src/hooks/useAuth.ts` if the design needs them, tests. NOTHING under `infra/` or `services/agent/` (parallel jobs run there).
- Dev-mode behavior byte-identical (the live tailnet demo runs disabled-mode — load-bearing).
- `git add` only files you touched; never `git add -A`. Commit `job-0253b: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
`reports/inflight/job-0253b-web-20260611/{report.md,STATE=IN_REVIEW}`; report shows the before/after envelope sequence and the reconnect trigger design. Orchestrator folds verification into the Stage-2 panels (the wire-order claim gets re-proven live against a gate-ON agent there).
