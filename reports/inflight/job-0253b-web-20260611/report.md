# Report: auth wire-order fix (auth-token FIRST) + re-sign-in reconnect + tsc hygiene

**Job ID:** job-0253b-web-20260611
**Sprint:** 13.5 Stage 1 carry-over (panel-job-0253 majors; both prod-only, dev-invisible)
**Specialist:** web
**Task:** (1) auth-token strictly FIRST on every connection before session-resume (token + empty-token paths, no open-handler wedge on getIdToken failure); (2) re-sign-in reconnect for BOTH GraceWs instances (App + Chat), never in dev/disabled mode, no double-connect, closes OQ-0253-CHAT-WS-4401; (3) fix the 6 new tsc mock-typing errors in AuthGuard.test.tsx (33→27, zero in changed files). (Verbatim: `reports/inflight/job-0253b-web-20260611/audit.md`.)
**Status:** ready-for-audit

## Summary

Three surgical fixes to the prod auth path, all invisible to the dev/tailnet disabled-mode demo. (1) The WS open handler now awaits `maybeSendAuthToken()` before emitting `session-resume`, making `auth-token` the literal first frame on every connection so the agent's arrival-order gate reads a signed-in user's valid token instead of 4401-ing the connection. (2) App tracks an `authEpoch` bumped exactly once on a recovered re-sign-in (a fresh non-anonymous user arriving while auth-expired); it is threaded into both the App and Chat ws-effect deps, so both GraceWs instances tear their dead post-4401 sockets down and reconnect — never in disabled mode (no Firebase → no authExpired → no bump). (3) The two `vi.fn<[], …>()` two-type-arg mocks in `AuthGuard.test.tsx` became the single-function-type form. Full web suite **710 → 718** (+8: 4 wire-order + 4 reconnect), all green; `tsc --noEmit` **33 → 27**, zero in any changed/new file.

## Finding 1 — auth-token wire order

### Before (BROKEN under the gate)
On `open`, ws.ts sent `session-resume` synchronously, then called `void this.maybeSendAuthToken()` (which awaits `getIdToken()` before emitting `auth-token`). On-wire order:

```
1. session-resume     ← FIRST non-auth-token frame
2. auth-token         ← arrives later, after the awaited token fetch
```

The agent gate (`server.py:4047-4063` → `_ensure_auth_handshake`) dispatches in arrival order: under `AUTH_REQUIRED=true` it rejects the FIRST non-auth-token frame with close 4401 ("auth-token envelope required before any other message"). So `session-resume` tripped the gate and the user's valid `auth-token` was never read — every prod connection 4401'd.

### After (FIXED)
The open handler awaits the auth-token send, then emits `session-resume` in the same chained async block. On-wire order on EVERY connection (real-token AND empty-token anonymous):

```
1. auth-token         ← strictly first; carries the Firebase JWT (or empty + sticky-anon hint)
2. session-resume     ← only after the auth-token send completes
```

### Why dev/disabled stays byte-identical
`maybeSendAuthToken()` has ALWAYS emitted the `auth-token` envelope even with an empty token (job-0172 Part C sticky-anon hint). So pre-fix the eventual order was `session-resume, auth-token`; post-fix it is `auth-token, session-resume`. This is a pure reordering — the same two frames, same payloads. In disabled mode the agent gate is OFF, so neither order matters to it; the change is protocol-invisible there. No pre-existing test asserted resume-first (grep + full suite green confirm).

### getIdToken-failure cannot wedge the handler
`maybeSendAuthToken()` wraps `getter()` in try/catch and falls back to an empty-token send on any throw; it never rejects. The chained `session-resume` therefore always runs after the await settles. A `mockRejectedValue` test proves the resume still follows on a token-fetch throw. A post-await guard (`this.socket !== ws || ws.readyState !== OPEN → return`) prevents a resume on a socket that closed/was-replaced during the await.

**File:** `web/src/ws.ts:670-703` (open handler).

## Finding 2 — re-sign-in reconnect (closes OQ-0253-CHAT-WS-4401)

### The wedge
`handleAuthFailure`'s give-up branch (`ws.ts:1032-1035`) emits `onStatus("disconnected")` + `onAuthExpired` and schedules NO reconnect — correct, because a rejected credential would re-trip the gate on every backoff tick. But nothing reconnected the sockets LATER: App's ws effect deps were otherwise stable, `onAuthChanged` only cleared `authExpired`, and Chat's effect keyed on `[wsUrl, bump]`. After a successful re-sign-in the AuthGuard rendered children over TWO dead sockets until a full page reload.

### Design: `authEpoch` threaded into both effects' deps
- **`App.tsx`** adds `const [authEpoch, setAuthEpoch] = useState(0)` and an `authExpiredRef` (kept current each render). In the `onAuthChanged` callback, when a fresh non-anonymous user arrives, `if (authExpiredRef.current) setAuthEpoch(n => n + 1)` before clearing `authExpired`. The ref read avoids re-subscribing on every `authExpired` flip.
- **App ws effect** adds `authEpoch` to its dep array. A bump re-runs the effect: cleanup `ws.close()` (safe on the dead socket — sets `closedByUser`, unregisters the hub), then `new GraceWs(...) + connect()`. `connect()` resets the auth latches (`ws.ts:424-427`), giving the fresh credential a clean attempt.
- **Chat** gains an `authEpoch?: number` prop (default 0), threaded from App's `<Chat …>` render, and adds it to its `[wsUrl, bump]` dep array. So Chat's own GraceWs instance participates in the recovery — this is exactly OQ-0253-CHAT-WS-4401 closed.

Net on a recovery: each instance opens exactly ONE fresh socket (React unmounts the old GraceWs and mounts a new one — no double-connect of a live socket, because the prior socket is dead and a brand-new instance is created per effect run).

### Why dev/disabled mode can NEVER engage it
In disabled mode `onAuthChanged` fires `null` exactly once and never delivers a non-anonymous user (the `auth.ts` disabled-mode contract — Firebase isn't initialized). So the `if (u && !u.isAnonymous)` branch is unreachable, `authExpired` is never set, and `authEpoch` stays `0` forever. Both effects run exactly once at mount, identical to before. The reconnect machinery is structurally dead in dev — the load-bearing demo path is untouched.

**Files:** `web/src/App.tsx` (authEpoch state + onAuthChanged bump + App-effect dep + Chat prop); `web/src/Chat.tsx` (authEpoch prop + ws-effect dep — minimal hunk).

## Finding 3 — tsc hygiene

`AuthGuard.test.tsx:26-27` used `vi.fn<[], Promise<…>>()` (the deprecated two-type-arg tuple form), which vitest 4.1.8 rejects (`TS2558: Expected 0-1 type arguments, but got 2`) and which poisoned 4 downstream call sites to `never`. Changed both to the single-function-type form `vi.fn<() => Promise<…>>()` (matching the clean usage in `LayerPanel.test.tsx`). All 6 errors cleared; 13 AuthGuard tests still pass unchanged.

## Changes Made

- **`web/src/ws.ts`** (open handler, ~:670-703): await `maybeSendAuthToken()` then emit `session-resume` in a chained async IIFE; post-await socket guard. Auth-token is now strictly the first frame.
- **`web/src/App.tsx`**: `authEpoch` state + `authExpiredRef`; `onAuthChanged` bumps `authEpoch` on recovered re-sign-in; `authEpoch` added to the ws-effect deps; `authEpoch` passed to `<Chat>`.
- **`web/src/Chat.tsx`** (minimal): `authEpoch?: number` prop (default 0); added to the ws-effect dep array.
- **`web/src/components/AuthGuard.test.tsx`**: two mock declarations to the single-function-type `vi.fn` form (Finding 3).
- **`web/src/ws.authwireorder.test.tsx` (NEW, 4 tests)**: deterministic fake WebSocket; captures literal on-wire frame order; asserts auth-token strictly precedes session-resume on the real-token path and the empty-token anonymous path; proves a getIdToken throw doesn't wedge the resume; proves a fresh `connect()` after auth-expired opens exactly one new socket.
- **`web/src/App.resignin.test.tsx` (NEW, 4 tests)**: a harness reproducing App's `onAuthChanged→authEpoch` logic and the two ws effects verbatim, driving the REAL GraceWs against the fake socket. Asserts: recovered re-sign-in opens exactly +2 sockets (one per instance); Chat participates (+2 not +1 → OQ closed); a second fresh-user delivery without an intervening expiry does NOT reconnect (no double-connect); disabled-mode (null-only) engages no reconnect machinery and `authEpoch` stays 0.

## Decisions Made

- **`authEpoch` epoch counter threaded into deps, not explicit `wsRef.current?.connect()` calls.** Both satisfy the kickoff's "properties non-negotiable, design yours." The epoch approach reuses React's effect lifecycle (clean teardown of the dead socket via the existing cleanup + a fresh instance), is symmetric across App and Chat, and keeps Chat's hunk to a prop + one dep. A direct `connect()` would reuse the dead instance and require manually reasoning about its latch state; the epoch path always starts from a fresh GraceWs. Alternative (direct connect) rejected as higher-risk for the demo-critical Chat.tsx.
- **`authExpiredRef` instead of adding `authExpired` to the onAuthChanged effect deps.** Re-subscribing to Firebase on every `authExpired` flip is wasteful and churns the listener; the ref reads the latest value without re-running the subscription effect.
- **Tests install a local fake WebSocket.** happy-dom does NOT populate `window.__webSockets` (verified), so the job-0253 panel probes that hard-depend on it can't drive socket behavior here — they silently no-op via their `if (!socket) return` guards (the `sendorder` probe hard-asserts and so surfaces the gap). My suites install a deterministic fake WebSocket so the wire-order and reconnect logic genuinely executes and is asserted. Confirmed load-bearing: reverting `ws.ts` fails 3 of the 4 wire-order tests.

## Invariants Touched

- **1. Determinism boundary** — preserves. No user-facing numbers; only connection sequencing and reconnect lifecycle.
- **8. Cancellation / first-class connection states** — extends. 4401 auth-expired remains a distinct terminal state; recovery is now an explicit epoch-driven reconnect rather than a reload requirement.
- No QGIS/Tier/`gs://`/map-render surface touched. Banned-vocabulary check: clean.

## Open Questions

- **OQ-0253b-AUTHTOKEN-SOCKET-RACE (informational, non-blocking):** `maybeSendAuthToken()` sends to `this.socket` (not the captured `ws`); if `this.socket` were replaced mid-await the token could target a different socket. This is pre-existing behavior (unchanged by this job) and not reachable in practice (the await resolves before any socket swap on the same tick). The new session-resume guard checks `this.socket === ws`. Tentative: leave as-is; flag only if a future concurrent-connect path is added.

## Dependencies and Impacts

- **Depends on:** job-0253 DONE (panel 4/4 — AuthGuard + useAuth + ws.ts 4401 substrate; this job builds directly on `handleAuthFailure` / `connect()` latch-reset / `onAuthExpired`).
- **Affects:** the Stage-2 live panel re-proves the wire-order claim against a gate-ON agent (per kickoff). job-0256/0257 prod deploy activates both fixes once `VITE_FIREBASE_PROJECT_ID` is injected.
- **No file overlap** with parallel jobs under `infra/` or `services/agent/`. `src/Map.tsx` and `src/components/SettingsPopup.tsx` were already dirty at job start (not mine; untouched).

## Verification

- **Tests run:** `cd web && npx vitest run` → **48 files, 718 passed** (baseline 46 files / 710 with my changes stashed; +2 files / +8 tests). Zero regressions. The 4 wire-order + 4 reconnect tests pass; the 13 AuthGuard tests still pass after the tsc fix.
- **Panel probes:** of the 4 job-0253 `verify/` probe files, 3 (`resignin`, `auth4401`, `authguard` adversarial) pass against the fix; all socket-dependent panel probes no-op under happy-dom (no `__webSockets` tracking — environment limitation, not a fix failure), which is exactly why this job adds suites with a real fake-socket harness that DO exercise the sockets.
- **Regression-proof (load-bearing):** `git stash push src/ws.ts` → 3 of 4 wire-order tests FAIL (auth-token no longer first); restored → green.
- **`tsc --noEmit`:** **27 errors** (down from 33), **zero in any changed or new file** (verified by grep). The remaining 27 are all pre-existing in untouched test files: `ws.test.tsx` (21), `ws.stickyAnon.test.tsx` (4), `Chat.caseTagRouting.test.tsx` (1), `Chat.perCaseStreams.test.tsx` (1).
- **Live E2E note (qualified):** per the kickoff, the wire-order claim is re-proven LIVE against a gate-ON agent in the orchestrator's Stage-2 panel (this job does not restart the dev agent or :5173, and a live Firebase project + AUTH_REQUIRED agent are not available in this dev environment). The fix is proven here by the deterministic on-wire frame-order capture (real fake socket, both token paths) and by hand-tracing the agent gate semantics (`server.py:4047-4063` rejects the first non-auth-token frame; auth-token is now that first frame). Dev/disabled behavior is proven byte-identical by the disabled-mode reconnect test (engages no machinery) and the full green suite.
- **Results:** **pass** (live-gate re-proof deferred to Stage-2 per kickoff, as designed).
