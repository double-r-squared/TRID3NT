# Session Durability Fix — Synthesis Plan

Status: design / scope (not yet jobbed)
Author: SYNTHESIS lane (orchestrator-dispatched)
Date: 2026-06-22
Inputs: 4 read-only root-cause traces (auth-persist, WS lifecycle + active-case flap, composer send-blocker, loading-state)

## One-paragraph problem

A clustered "session feels broken" symptom from live use on a full browser close+reload
and on mobile navigate-out/back: the user is bounced to the Sign-in screen despite a valid
durable session; WebSocket connections pile up toward ~20 for a single browser session;
the displayed case's layers briefly vanish and resettle ("flap"); the composer sometimes
sticks as a Stop button so a typed prompt never reaches the server (zero user-message in
the journal); and while a case is opening the panel shows "No layers loaded yet" instead
of a loading affordance. These are four distinct defects but they reinforce each other:
the auth bounce forces a fresh full reload (more sockets), the dual-socket design feeds
the active-case flap, and the flap mis-routes the recovery frame that would otherwise
unstick the composer. The keystone is auth-persistence — fixing the cold-reload restore
removes the most common trigger for a fresh full reload, which is the largest single
source of new sockets and re-resume churn.

## Keystone

Auth cold-reload restore race (web/src/auth.ts). On a full close+reload only the durable
localStorage refresh token survives; restoring the session requires an async refresh_token
grant inside `initAuth()`. But `initAuth()` sets the module-level `initialized = true` latch
SYNCHRONOUSLY (auth.ts:329) before `await refreshTokens(...)` (auth.ts:359), and there is no
in-flight-promise sharing. The app mounts TWO concurrent `onAuthChanged` subscribers
(App.tsx:345 and AuthGuard via useAuth, AuthGuard.tsx:161). The first runs the real async
refresh; the second sees `initialized === true`, short-circuits at auth.ts:328, and fires
its `.then()` callback (auth.ts:391-393) with `cachedUser` still null. That flips
resolved/authResolved true with a null user, so the gate paints the Sign-in screen before
the refresh resolves. Fixing this single race quiets the most symptoms: it stops the
spurious full reloads (the dominant source of new sockets) and removes the most common
path that lands the user on a freshly-mounted, freshly-resuming pair of sockets.

Fix shape: replace the boolean latch with a memoized in-flight promise
(`let initPromise: Promise<void> | null`) so the SECOND caller awaits the SAME promise the
first is running. Keep `initialized` as a fast-path flag set only once the promise resolves.
Both `onAuthChanged` `.then()` (auth.ts:391-393) and `getIdToken()`'s `await initAuth()`
(auth.ts:416) then observe the restored non-anonymous user before resolved goes true.
The existing durable-mirror seed (commits 16976ce / c1c0999) is correct and stays; it does
not cover the full-close path because that path is gated on the async refresh, which the
synchronous latch lets a racing subscriber observe pre-refresh.

## Jobs (ordered by leverage, file-disjoint)

The five jobs touch disjoint file sets so they can run in parallel after the keystone seam
is understood. The only shared file is ws.ts (jobs 2, 3) and App.tsx (jobs 2, 5) — see the
Collision note below; those two pairs are sequenced, not parallel, within their shared file.

### Job A (keystone) — auth cold-reload restore race [web specialist]
- Owner: web
- Files: web/src/auth.ts, web/src/hooks/useAuth.ts, web/src/components/AuthGuard.tsx,
  web/src/EntryRouter.tsx, web/src/auth.coldrestore.test.ts
- Change: memoize the in-flight `initAuth()` promise; both concurrent subscribers await the
  same settle before reporting resolved. `initialized` becomes a post-resolve fast-path flag.
  Mirror the same await in `getIdToken()` and `handleRedirectCallback` so no path wins the
  latch before the durable refresh completes.
- Test: extend auth.coldrestore.test.ts with TWO concurrent `onAuthChanged` subscribers
  seeded ONLY from LS_REFRESH (no sessionStorage set); assert neither callback ever observes
  a null user once a durable refresh token is present.
- Note: App.tsx is NOT owned here even though it consumes authUser/authResolved — the App.tsx
  change surface belongs to Job E (loading stub). Job A touches only the auth seam + the two
  gate consumers (useAuth/AuthGuard) + the router + the auth test.

### Job B — WS connection accumulation: eager per-session reaping [agent specialist]
- Owner: agent
- Files: services/agent/src/grace2_agent/server.py
- Change: add an eager per-session connection registry (session_id -> set of live
  connections) and, on a new connection's session-resume handshake, proactively close any
  PRIOR socket of the same session that is no longer the client's live one (or cap
  connections-per-session). Tie into `_register_active_connection` (server.py:8765) and
  `_handle_session_resume` (server.py:2730). Optionally tighten ping_interval/ping_timeout
  (server.py:9565) so orphaned mobile sockets are reaped faster than navigate cycles open
  new ones. This retires zombies that today only the ~20s websockets ping reaps.
- Test: a server-side unit/integration test that opens N connections for one session_id and
  asserts the prior socket is closed (or the per-session count is capped) when a new resume
  lands; assert _ACTIVE_WS_CONNECTIONS does not grow unbounded across simulated reconnects.

### Job C — active-case flap: single-writer active-case authority [agent specialist]
- Owner: agent
- Files: services/agent/src/grace2_agent/server.py
- Change: stop letting BOTH per-socket 25s keepalive resumes rebind the shared
  `_SESSION_ACTIVE_CASE` pointer (server.py:867). Only rebind on an EXPLICIT case-select /
  user-message (server.py:4080) and on the FIRST resume of a connection — NOT on the
  keepalive resume. Distinguish a keepalive ping from a genuine fresh resume (gate the
  rebind at server.py:2778 behind `not state.did_fresh_resume` or a dedicated
  `is_keepalive` flag on the resume payload). This removes the ping-pong where App's socket
  and Chat's socket fight over the shared pointer every keepalive and each rebind drives an
  authoritative layer replay (server.py:2846-2850) that clobbers the displayed layers.
- Test: simulate two connections for one session that send keepalive resumes stamped with
  DIFFERENT case ids; assert the shared pointer is NOT rebound by a keepalive and no layer
  replay is triggered; assert an explicit select still rebinds.
- COLLISION with Job B: both edit server.py. Sequence B then C (same file, same owner) as
  one agent thread or two serialized commits; do not parallelize across them.

### Job D — composer stuck-as-Stop after a lost completion frame [web specialist]
- Owner: web
- Files: web/src/Chat.tsx, web/src/components/ChatInput.tsx, web/src/ws.ts
- Change: the send/stop control derives solely from pipeline state
  (`inputState = shouldShowCancel(pipeline) ? 'in-flight' : 'idle'`, Chat.tsx:3819) with no
  independent isStreaming flag; `shouldShowCancel` (Chat.tsx:1018-1024) latches on
  `currentPipelineFromSession !== null` and is cleared only by an inbound terminal frame.
  When a turn completes server-side but the completion/close frame is lost on a dropped
  socket, the latch stays true forever and the composer renders Stop — a tap routes to
  cancel and Enter early-returns (ChatInput.tsx:597), so the prompt never sends.
  (1) Add a client-side watchdog: a turn in-flight past a bounded interval with no inbound
  activity force-dispatches turn-complete into the VISIBLE stream (clears
  currentPipelineFromSession + running steps), independent of owning-case routing.
  (2) On every successful reconnect/session-resume open (ws.ts:1316-1372), have
  routeSessionState/routeTurnComplete fall back to the visible/targetKey stream when the
  carried case_id matches no live in-flight stream, so a re-emitted clear cannot settle the
  wrong stream.
  (3) Route sendCancel through sendOrQueue (ws.ts:949-957 currently bare sendEnvelope) so a
  tap on a stuck Stop button is not silently no-op'd mid-reconnect.
- Test: vitest — drive a session-state with non-null current_pipeline, then drop the socket
  before the terminal frame; assert the watchdog clears the latch into the visible stream
  and the composer returns to idle (send-enabled); assert sendCancel is queued when the
  socket is not OPEN.
- COLLISION with Job D's ws.ts vs Job A/E: ws.ts is also touched by nothing in A/E; ws.ts is
  shared only with Job B's CLIENT counterpart? No — Job B is server.py only. So ws.ts is
  Job D exclusive among web jobs. Safe to parallelize Job D with Jobs A and E.

### Job E — loading-state: three-way layer-panel split [web specialist]
- Owner: web
- Files: web/src/App.tsx (the loading-stub render blocks only)
- Change: the two empty-layer stubs (desktop App.tsx:1515-1534, mobile App.tsx:1839-1862)
  are a hard binary on `layers.length === 0` with no loading branch. Derive a memoized
  `layersLoading` near the activeCase derivation (App.tsx:1354) from signals that already
  exist: `caseSelectedButUnsettled = activeCaseId !== null && (activeSession === null ||
  activeSession.case.case_id !== activeCaseId)` (already compared at App.tsx:1126) plus
  state-backed wsStatus ('connecting' | 'reconnecting', App.tsx:460). Split each stub into
  three branches: loading -> "Loading layers..." + spinner (new testid
  grace2-case-view-loading-layers); settled-empty -> existing "No layers loaded yet..."
  (keep grace2-case-view-empty-layers); populated -> LayerPanel. Genuinely-empty ==
  `layers.length === 0 && !layersLoading`. If the cold-fetch-empty window must also spinner,
  promote coldLoadedCaseRef (App.tsx:1113, a ref so it won't re-render alone) to useState
  set alongside the ref at App.tsx:1130 and cleared in the resolve/cancel paths
  (App.tsx:1145-1163); otherwise rely on the state-backed wsStatus + caseSelectedButUnsettled.
- Test: vitest — render with a selected case and no layers while wsStatus='connecting' /
  activeSession mismatched; assert the loading testid renders; then settle with zero layers
  and assert the empty testid renders; then with layers assert LayerPanel.
- COLLISION with Job A (App.tsx): Job A does NOT edit App.tsx (its App.tsx consumption is
  read-only via authUser/authResolved props already wired). Job E owns the App.tsx render
  blocks. If Job A must touch App.tsx's onAuthChanged effect (App.tsx:345), sequence A then
  E within App.tsx. Default plan: A keeps App.tsx untouched, E owns App.tsx — parallel-safe.

## File-disjointness map (collision control)

- auth.ts, useAuth.ts, AuthGuard.tsx, EntryRouter.tsx, auth.coldrestore.test.ts -> Job A only
- server.py -> Jobs B + C (SAME owner, SERIALIZE B then C; not parallel)
- Chat.tsx, ChatInput.tsx -> Job D only
- ws.ts -> Job D only (no other job touches ws.ts)
- App.tsx -> Job E only (Job A stays out of App.tsx by design; if unavoidable, A before E)

Net: A, D, E run in parallel (3 web threads on disjoint files); B+C run as one serialized
agent thread on server.py. No two parallel jobs share a file.

## Deploy surface

- Jobs A, D, E (web): web build + S3 sync + CloudFront invalidation. The web build needs
  VITE_GRACE2_PUBLIC_BASE (per AWS deploy facts). Continuous-deploy as work lands green
  (NATE standing say-so, no per-deploy gate); ask ONE permission at the END before the live
  CloudFront mutation.
- Jobs B + C (agent): server.py change -> SSM file-swap deploy to the agent box
  (i-0251879a278df797f) then process restart. MUST land in a clean autostop window — the
  restart drops every live WS, so do it when no live solve/turn is in flight. Grep the box
  to confirm deployed == HEAD after the swap (commit/env-flip != deploy).

## Risk

- Auth (Job A) and WS lifecycle (Jobs B, C, D) are CORE: a regression signs everyone out or
  drops every socket. Handle with belt-and-suspenders tests and a refute-by-default review
  panel before landing. The auth promise-memoization must preserve the disabled-mode path
  (env vars absent -> single null fire, anonymous-only) and the injected-test seam
  (injectedActive at auth.ts:327/388).
- Job B's eager reaping must not close the CLIENT'S OWN live socket — close only PRIOR
  sockets of the same session that are not the resuming connection. Mis-targeting kills the
  active tab.
- Job C's keepalive-vs-resume distinction must not also suppress the legitimate first-resume
  layer replay (the cold-resume seed at server.py:2846-2850 must still fire once per
  connection); gate strictly on the keepalive flag, not on resume in general.
- Job D's watchdog interval must be long enough not to fire mid-legitimate-long-turn (a real
  multi-minute solve shows running steps); key the watchdog on NO inbound activity for the
  bound, not on elapsed time alone.
- Agent restart (B, C) is disruptive by nature; schedule in the clean window and confirm WS
  reconnect health post-restart.

## Live acceptance (re-run by reviewers, not trusted from report)

- Job A: sign in, fully CLOSE the browser, reopen the app URL -> lands signed IN on the case
  view (NOT the Sign-in screen), with no manual re-auth. Repeat across several full closes.
- Jobs B + C: from one mobile browser session, navigate OUT of the app and BACK ~10 times;
  active_connections for that session does NOT pile up (stays low, prior sockets reaped); the
  displayed case's layers do NOT flap/vanish-and-resettle on the 25s keepalive cadence while
  two sockets are live.
- Job D: start a turn, force a socket drop at the instant of completion (or kill the box
  mid-turn); after reconnect the composer returns to a send-enabled state and a freshly typed
  prompt SENDS and reaches the server (a user-message appears in the journal) — the composer
  is never permanently stuck as Stop.
- Job E: select a case; while its layers are loading the panel shows a "Loading layers..."
  spinner (testid grace2-case-view-loading-layers), NOT "No layers loaded yet"; a genuinely
  empty settled case still shows the empty stub.
