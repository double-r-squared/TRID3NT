# Agent Isolation Spike: per-user/session agent isolation -> Fargate-per-session now, AgentCore Runtime later

Status: decision spike (live incident driven). Author: agent specialist (orchestrator-dispatched).
Date: 2026-06-22. Grounded against the live GRACE-2 agent on disk
(`services/agent/src/grace2_agent/server.py`, `auth_handshake.py`, `web/src/ws.ts`,
`infra/aws-autostop/`) AND current AWS Bedrock AgentCore Runtime GA docs
(incl. the Dec 18 2025 bidirectional-WebSocket GA that revises the prior eval).

ASCII only. No em/en dashes, no unicode arrows; "->" is the literal text arrow.
This doc is design + verdict only -- no code lands here.

---

## 0. Verdict

**HYBRID_FARGATE_NOW_AGENTCORE_LATER.**

Build **Fargate-per-session** isolation NOW (a thin connection broker + a
per-user/session ECS-on-Fargate task running the EXISTING WS agent unchanged),
and keep **AgentCore Runtime** as a deliberate LATER target -- now genuinely
viable for the first time because AgentCore GA'd a persistent bidirectional
WebSocket on Dec 18 2025, but still requiring a real four-front re-architecture
of the WS layer that the Fargate path does NOT.

The crux question -- "is AgentCore Runtime a WebSocket HOST or invoke/stream
only?" -- now resolves to **YES, it can host a long-lived bidirectional WS**, which
FLIPS the 2026-06-17 eval's assumption (that eval predated the WS GA and treated
Runtime as effectively invoke/stream-only). So AgentCore is no longer disqualified
on protocol. But "can host a WS" is not "can host OUR WS today." Our agent owns its
own listener (`websockets.asyncio.server.serve`, server.py:9839), runs a 12s
server-push DATA heartbeat, and carries a dual-socket-per-tab session model -- and
AgentCore OWNS the listener, imposes a HARD 60-minute cap on any single WS
connection, mandates ARM64 + a 2 GB image, and terminates the socket at
`wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/ws` (not our
CloudFront origin). Those are not blockers in the TUFLOW sense (legal/architectural
dead-ends); they are real engineering work that must be done and proven before a
cutover. The incident on the table -- shared-box cross-user WS interference and
kill-collateral -- needs a fix NOW, and the path that delivers isolation with
ZERO agent rewrite is Fargate-per-session.

Decision shape, in one line each:

- **NOW (the incident fix):** Fargate-per-session. The existing WS agent runs
  byte-identical inside a per-user task; a thin Cognito-verifying broker routes
  each session to its own task; idle-stop reaps to zero. Hard per-user blast-radius
  isolation, long-lived streaming preserved completely, ~no agent code change.
- **LATER (the strategic target):** AgentCore Runtime, gated on proving the WS
  re-architecture (the four fronts in section 6) against a real >60-min Batch
  solve with hourly client reconnect. If it passes, AgentCore gives the same
  per-session microVM isolation with AWS owning the listener/TLS/scaling/lifecycle
  -- less infra for us to run than the broker. Lock-in stays LOW (the
  `/invocations + /ping + /ws` container ports straight back to Fargate/EC2).
- **NOT:** STAY_SHARED is rejected -- it is the thing that caused the incident.
  Pure-AGENTCORE-now is rejected -- the WS re-architecture is unproven and the
  incident needs a fix this sprint, not after a spike.

Cost is NOT the deciding factor (it never is for this tier -- single-digit $/mo at
dev volume, and the forced TiTiler/catalog origin split erases AgentCore's
idle-cost edge in us-west-2). The decision is made on **isolation + ops + keeping
the streaming model intact**, exactly as the prior AgentCore eval concluded the
decision must be.

---

## 1. The incident, and why the current architecture causes it

The live agent is a **single-box, session-multiplexed** server. The truth on disk:

- ONE EC2 box (`i-0251879a278df797f` @ 54.185.114.233, us-west-2) runs ONE Python
  process. `main.run()` -> `server.run_server()` (server.py:9765) opens ONE
  `serve(handler, host, 8765, ping_interval=20, ping_timeout=20, close_timeout=10)`
  on a SINGLE asyncio loop (server.py:9839), plus a sibling catalog/health HTTP
  listener on 8766 (`serve_catalog_http`) and TiTiler on 8080. CloudFront
  E2L74AS56MVZ87 single-origins `/ws*->8765`, `/api->8766`, `/cog+/tiles->8080`.
- **Per-session state is in-process module dicts, NOT isolation.** Every session's
  `SessionState` (server.py:1356), live turns (`_SESSION_LIVE_TURNS`,
  server.py:1118), active-case pointer (`_SESSION_ACTIVE_CASE`, server.py:867),
  anon identity (`_SESSION_ANON_ID`, server.py:1018), and socket set
  (`_SESSION_WS_CONNECTIONS`, server.py:8959) live in module-level dicts keyed by
  `session_id` on the SAME heap. All sessions share ONE Python heap, ONE Bedrock
  client, ONE tool registry, ONE event loop.

That sharing IS the incident:

1. **Cross-user WS interference.** A poison turn, a runaway tool, a slow/blocking
   call, or an OOM in ONE user's session degrades or stalls the SINGLE asyncio
   loop -- starving every other user's heartbeat and turn. (This is the same
   class of failure the "no sync-blocking on the asyncio loop" norm exists to
   prevent, but the norm cannot cover a genuine crash/OOM.)
2. **Kill-collateral.** The single-box autostop reaper (`infra/aws-autostop`,
   idle_check Lambda -> `ec2:StopInstances`) stops the ONE box. Any manual or
   automated stop/restart of that box to clear ONE wedged session takes DOWN
   EVERY session on it. There is no per-user blast radius -- one bad session and
   one corrective kill both hit everyone.

Owner-scoping (`owner_user_id`, Decision H.2) already isolates DATA by user in
DynamoDB and S3. What is NOT isolated is COMPUTE: the agent loop, heap, and
process. **This spike is about isolating compute by session, with no change to
the already-correct data isolation.**

---

## 2. The hard gate: does the isolation path PRESERVE the long-lived WS model?

This is the make-or-break test, identical in spirit to the engine spikes'
"can it run headless on Linux" gate. The GRACE-2 agent is not a request/response
service -- it is a **long-lived, bidirectional, server-pushable WebSocket** with
load-bearing properties that any isolation path must keep:

- **12s server-push DATA heartbeat** (`HEARTBEAT_INTERVAL_SECONDS = 12.0`,
  server.py:1625; `_heartbeat_loop`, server.py:1628). This exists to defeat the
  browser's ~30s control-frame-blindness reconnect storm (see the WS-30s
  heartbeat fix). It MUST keep firing, and every network hop MUST tolerate a
  connection idle of >12s without severing it.
- **Server-initiated push at any time** -- streamed agent chunks, pipeline cards,
  mid-turn confirmation envelopes (payload / granularity / credential / region /
  spatial-input), per-Case layer replay on resume.
- **Dual-socket-per-tab** -- the web mounts TWO `GraceWs` sockets (App.tsx +
  Chat.tsx) sharing ONE `session_id` (ws.ts SESSION_KEY 'grace2.session_id',
  ws.ts:402), fanned by SESSION_HUB (ws.ts:609). Both MUST land on the SAME
  compute instance for the in-process convergence to work.
- **Detached-turn survival** -- `_SESSION_LIVE_TURNS` keeps a turn running across
  a socket drop so a long solve is not lost on a transient disconnect.
- **Resilient reconnect / session-resume** -- on reopen the client replays the
  same `session_id` + `auth-token`; the server replays active-Case layers.

The two candidate paths split exactly on this gate:

| Property | Fargate-per-session | AgentCore Runtime (GA) |
|---|---|---|
| Long-lived bidirectional WS | YES, UNCHANGED (own listener) | YES, but on AgentCore's `/ws` handler, NOT our `serve()` |
| 12s server-push heartbeat | YES, byte-identical | YES (resets idle on any frame incl. ping/pong) |
| Max single WS connection | unbounded (ALB idle up to 4000s) | **HARD 60 min, not adjustable** -> hourly reconnect required |
| Who owns the listener/TLS | WE do (agent binds 8765) | **AWS does** (we implement `@app.websocket`) |
| WS endpoint origin | our CloudFront `/ws` (unchanged client) | `wss://bedrock-agentcore...amazonaws.com/runtimes/<arn>/ws` |
| Agent code change to get there | ~NONE (containerize + broker) | rebuild ARM64 <=2GB + move off `serve()` + 8080/`/ping` |
| Frame size cap | none meaningful | **64 KB/frame, 250 frames/s** -> chunk larger payloads |

**Fargate-per-session passes the gate cleanly and immediately** -- the
`websockets.asyncio.server` runs as-is, every transport semantic is identical,
the ONLY new constraint is that every hop (CloudFront, ALB) must keep the
connection alive past the 12s heartbeat (trivially satisfied; AVOID API Gateway
WebSocket's 2h/10min caps).

**AgentCore Runtime passes the gate IN PRINCIPLE** (this is the flip from the
prior eval) -- the docs explicitly model voice agents streaming audio while
listening and async tasks streaming over minutes/hours, and idle resets on any
frame so a 12s heartbeat keeps the session warm. But it CANNOT run as-is; the
60-min connection cap and the listener-ownership change force a real
re-architecture (section 6).

So: isolation NOW without touching the streaming model -> Fargate. AgentCore is
the destination once the WS re-architecture is proven.

---

## 3. Recommended architecture (NOW): Fargate-per-session + thin broker

One ephemeral Fargate task per active session (per Cognito user, since both of a
tab's sockets share one `session_id` and must co-locate), launched on
first-connect, idle-stopped to zero. The image is the agent VERBATIM:
`server.run_server` already honors `GRACE2_AGENT_HOST=0.0.0.0` (server.py:9786,
added for phone/LAN demos) and already runs the sibling `/api/health` listener --
so the container is the agent with no app rewrite. One task = one Python process
serving one user's two sockets, so today's module-level per-session dicts now hold
EXACTLY ONE session -- turning today's accidental sharing into real isolation by
construction, with zero agent-code change.

```
                         CloudFront E2L74AS56MVZ87  (client URL unchanged:
                              |   wss://d125yfbyjrpbre.cloudfront.net/ws )
            /cog,/tiles ------+------ /ws*               /api
                |             |        |                  |
          TiTiler tiny box    |   [ BROKER ]         (catalog/health, per task)
          (always-on, 24/7)   |   verify JWT -> ULID
                              |   resolve route
                              |   RunTask if miss
                              |   proxy WSS  <----- grace2_session_routes (DynamoDB)
                              |        |
                              |   +----+----+----+ ...
                              |   |task A|task B|task C|   <- one Fargate task
                              |   +------+------+------+      per active session
                              |   (each = UNCHANGED server.py, 8765 WS + 8766 health)
                              |
                      Shared external state (UNCHANGED, owner-scoped by ULID):
                      DynamoDB (users/cases/chat) | S3 (runs+cache) |
                      Bedrock Converse+cachePoint | AWS Batch grace2-solvers (Spot)
```

Key property: a wedged tool, a memory leak, a poison turn, or an OOM blasts ONLY
that user's task -- never the shared box. That is the strongest argument for this
model over the status quo, and it is the direct fix for the incident.

The heavy compute (SFINCS/MODFLOW/SWMM) stays on AWS Batch Spot scale-to-zero
exactly as today -- the per-session task only mints a `run_id`, `batch.submit_job`
to `grace2-solvers`, and polls S3-completion. Heavy compute was never on the agent
box and is untouched. TiTiler stays on its own tiny always-on box
(`i-06cfdd3d6c66b2126`) so the map serves 24/7 regardless of agent state.

---

## 4. The router + auth-session-routing design (the only real new work)

The new work is entirely the routing tier; the agent does not change. The client
also does not change beyond (optionally) lifting `session_id` and the Cognito
token into the connect handshake so the broker can route BEFORE the WS upgrade.

### 4.1 Session affinity: a CUSTOM BROKER is required (ALB stickiness does NOT solve this)

Research-confirmed limitation: ALB duration/app-cookie stickiness only guarantees
"the same target as last time," not "THIS specific per-user task." AWS docs state
cookie stickiness is BYPASSED once the WS HTTP-101 upgrade completes -- the
upgraded target stays for the connection's life, but the INITIAL target selection
is still load-balanced across the target group, not addressed to one user's task.
NLB is worse (flow-hash, random target). So stickiness gives
pinning-within-a-target-group, not user->task addressing. The working pattern
(matching the AWS apigw-alb-fargate / CloudFront-scale-to-zero references) is a
tiny always-on broker that does, per new WSS connection:

1. **Read identity FIRST.** Today the Cognito ID token rides IN-BAND as the
   `auth-token` envelope POST-connect. For routing it must move EARLIER -- a
   connect query-param / `Sec-WebSocket-Protocol` subprotocol / first-frame the
   broker can read pre-routing. The broker verifies it against the SAME Cognito
   JWKS the agent uses (reuse `auth_handshake.cognito_verify` -- RS256 vs pool
   JWKS, iss/aud/`token_use == "id"`/exp; this exact logic is already duplicated
   into the wake/view-sign Lambdas, so it cannot drift).
2. **sub -> User ULID.** Call the same `Persistence.get_user_by_firebase_uid`
   the agent uses, auto-provisioning on first connect, so the canonical owner id
   stays the internal ULID (Decision 10); the Cognito `sub` is only the lookup
   key on `User.firebase_uid`. For anonymous (gate OFF, the current default),
   key on the client-replayed anonymous ULID exactly like the sticky-anon path.
3. **(user_ulid, session_id) -> target.** ConsistentRead `grace2_session_routes`
   (DynamoDB, hash `user_ulid`, range `session_id` -> `{taskArn, privateIp,
   port, state, last_seen}`; reuses the existing autostop DynamoDB pattern).
4. **Miss -> PROVISION.** `ecs:RunTask` (launchType=FARGATE) for that user, wait
   for RUNNING + an `:8766` health-green probe (mirrors the deploy-time
   `aws ec2 wait instance-status-ok` discipline), write the route row.
5. **Proxy the WSS stream** task <-> client for the connection's life.

The agent's in-band Cognito handshake (`auth_handshake._ensure_auth_handshake`)
is PRESERVED as the second, authoritative check; the broker's verify is only for
routing. Per-task ALB target-group registration is impractical at this churn --
the broker proxies/tunnels the WS to the task's private IP, which is what most
container-per-session designs do.

### 4.2 Routing chain (at the broker, before the WS upgrade completes)

```
client opens wss://.../ws
   |  presents Cognito ID token (subprotocol or short-lived query token)
   v
[broker] cognito_verify(token) -> claims{sub}                 (reuse auth_handshake)
   |  sub -> User ULID via Persistence.get_user_by_firebase_uid (auto-provision)
   v
ConsistentRead grace2_session_routes (user_ulid, session_id)
   |                                   |
   HIT                                 MISS
   |                                   |  ecs:RunTask -> wait :8766 health-green
   v                                   |  write route row
proxy WSS -> task:8765 <--------------- (then) proxy WSS -> task:8765
   |
2nd socket (Chat, same session_id) -> ConsistentRead HIT -> SAME task (no split-brain)
```

Both dual sockets of one tab carry the SAME `session_id`, so the second
ConsistentRead hits the just-written row and lands on the SAME task -- preserving
the `_SESSION_WS_CONNECTIONS` / SESSION_HUB / `_SESSION_LIVE_TURNS` convergence the
code depends on, now scoped to ONE task per session instead of one box for all.

### 4.3 Lifecycle: provision-on-connect / stop-on-idle (reuses the autostop primitives)

- **PROVISION-ON-CONNECT:** broker `RunTask` on a route miss, gate on `:8766`
  health, write the row, then proxy. Cold-start UX reuses the EXISTING wake
  overlay / edge-shimmer the web already shows when the box is asleep -- the
  "provisioning your agent" state is the same client affordance, now per-session.
- **STOP-ON-IDLE:** generalize the single-box `idle_check` to per-task. Each task
  already serves `GET /api/health` with the authoritative `busy` (`is_busy()`,
  server.py:1296 = OR of `inflight_turn_count` + `solve_in_flight_count` +
  outstanding S3 writes; `liveness_snapshot()`, server.py:1331). The reaper polls
  each task (or each task self-reports / self-exits) and `ecs:StopTask` after the
  same `IDLE_THRESHOLD_CHECKS` consecutive not-busy streak. The "never stop a busy
  box" streak logic, the Stage-3 "idle-open tab is NOT busy" rule, and the G3
  Batch guard (any SUBMITTED..RUNNING on `grace2-solvers` keeps the task up so it
  can poll the solve to completion) ALL port directly from `ec2:StopInstances` to
  `ecs:StopTask` with the same shape. The DynamoDB idle-streak store generalizes
  from one item to one item per `session_id` -- same conditional-write pattern.
- **WAKE = the broker.** A connect to an absent session re-provisions
  transparently; the explicit user "sleep" (POST action=stop, Cognito + not-busy)
  maps to `StopTask` on that one session's task.

---

## 5. Cost model

Cost is per-active-user, idle=0, vs today's per-box-while-awake. Cost is NOT the
deciding factor (matches the AgentCore eval) -- isolation + an unchanged streaming
model is the win. The numbers:

| Posture | Steady state (realistic: 0-2 concurrent sessions) | Fan-out (50 concurrent users) | Always-on floor |
|---|---|---|---|
| **Today: single autostop box** | ~1 intermittent box, shared across all users, auto-stopped after ~15 min idle (3 ticks x rate(5min)) | one fatter box could serve many -> efficient | TiTiler tiny box (24/7) + Batch pay-per-solve |
| **Fargate-per-session** | N x (0.5-1 vCPU / 1-2 GB, ~$0.02-0.04/hr/task), per-second, idle->0; mostly zero tasks running | 50 tasks billing concurrently = MORE than one shared box | + broker (Lambda@Edge ~0 OR a small always-on gateway) + DynamoDB registry (PAY_PER_REQUEST, negligible) + optional 1 warm-pool task |
| **AgentCore Runtime** | $0.0895/vCPU-hr + $0.00945/GB-hr, per-second, I/O-wait CPU FREE, 128 MB min memory billing; idle WS accrues memory-hours, ~zero CPU | scales per session, AWS-managed | NONE we run (AWS owns it) BUT forced TiTiler/catalog origin split is its own cost |

Trade: for a SMALL user base (current reality -- a handful, often just NATE),
Fargate is roughly cost-NEUTRAL-to-cheaper than one shared box, and you GAIN hard
per-user isolation. The cost crossover is fan-out -- 50 simultaneous users = 50
tasks billing at once, MORE than one fatter shared box could have served. So this
model trades shared-box efficiency for isolation and wins on cost only at LOW
concurrency. The main always-on add is the broker's ALB hours (a
TiTiler-box-class line item) if you use ALB instead of Lambda@Edge. Mitigation if
break-even matters: keep the broker a cheap Lambda-authorizer, share the ALB with
nothing else, and set the per-session idle threshold aggressively low.

AgentCore's cost edge (true scale-to-zero, idle CPU FREE) is real BUT erased for
us specifically because adopting it forces splitting TiTiler:8080 + catalog:8766
onto their own origin and repointing CloudFront (the WS terminates at AWS's
endpoint, not ours) -- a fixed cost that cancels the idle-CPU saving. This is
exactly why the prior eval said the decision must be made on ops, not cost.

The cost watch-item for Fargate is N-tasks-at-fan-out; this aligns with the
scale-to-zero island north star (the agent island becomes per-session
scale-to-zero instead of one shared box).

---

## 6. The AgentCore Runtime implementation path (LATER -- what it would take)

NATE asked for a concrete AgentCore path "if possible." It IS possible now (the WS
GA makes it so), and here it is -- gated on proving the WS-fit reality. This
materially revises the 2026-06-17 eval, which predated the bidirectional-WS GA and
correctly (for its time) ranked Runtime a SPIKE-ONLY behind three gates. The WS GA
removes the "is it even a WS host" disqualifier; the work below is what remains.

### 6.1 What AgentCore Runtime is (current GA, the part that changed)

A serverless, managed runtime for hosting agents. You package the agent as an
ARM64 container (or a code-zip) implementing a fixed HTTP/WS contract on
`0.0.0.0:8080`, register it as an `AgentRuntime`, and AWS provisions/scales/
ISOLATES it per session. AWS owns the listener, TLS, inbound auth (SigV4 /
OAuth-JWT), per-session compute lifecycle, scaling, observability; the container
only implements the handlers. Invocation surfaces: `InvokeAgentRuntime` (sync/SSE)
AND -- the GA flip, Dec 18 2025 -- `InvokeAgentRuntimeWithWebSocketStream` (a
persistent bidirectional WebSocket). Runtime is one of seven AgentCore building
blocks (Runtime, Memory, Identity, Gateway, Code Interpreter, Browser,
Observability); Runtime is the agent-loop host.

### 6.2 Session isolation (the reason it is the eventual destination)

Strong per-session isolation: every session gets its OWN dedicated Firecracker
microVM (same tech as Lambda/Fargate) with isolated CPU, memory, and filesystem.
On session end the entire microVM is terminated and memory sanitized -- no
cross-session contamination. Sessions are routed by the caller-supplied
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header (>=33 chars); the same
session_id routes back to the SAME live microVM (context preserved between
invocations), a different id gets a separate isolated context. Scale-to-zero is
per session: idle past `idleRuntimeSessionTimeout` (default 15 min) or past
`maxLifetime` (default 8 hr) reaps the microVM and you stop paying. Account
default: 5,000 active session workloads (us-east-1/us-west-2), 2,500 elsewhere,
adjustable. This is the SAME isolation property the Fargate broker builds by hand
-- but AWS owns it, so we run no broker, no registry, no idle reaper.

### 6.3 Protocol fit -- the four-front re-architecture (the real gate)

AgentCore now GA-supports a genuine long-lived, persistent, bidirectional WS: the
container implements a `/ws` endpoint on 8080 (HTTP 101 upgrade), `await
websocket.accept()`, a continuous receive loop, and `send_text()/send_bytes()` at
any time -- so server-initiated push works, and the idle timeout RESETS on any
frame incl. ping/pong, so our 12s heartbeat keeps the session alive indefinitely
up to the cap. So our pattern maps IN PRINCIPLE. But it cannot run AS-IS -- four
fronts of re-architecture:

1. **Listener ownership.** We own our listener via
   `websockets.asyncio.server.serve` (server.py:9839); AgentCore owns the listener
   and hands us an accepted connection via the `@app.websocket` handler contract
   (BedrockAgentCoreApp / Starlette-based). The `serve()` call must be REPLACED.
   This is the single most invasive change -- it touches the entry point of the
   whole WS server.
2. **The 60-MINUTE connection cap (the binding limit).** A HARD, non-adjustable
   60-min cap on any single streaming/WS connection -- NOT the 8-hr session
   lifetime. The client MUST reconnect at least hourly, reattaching to the same
   microVM by re-sending the same session_id. Our resilient-reconnect /
   session-resume work (ws.ts) makes this feasible, but it must be PROVEN against
   a real >60-min Batch solve: the connection drops at 60 min, the client redials
   the same session_id, lands back on the same warm microVM, and the in-flight
   solve (detached turn) survives. `/ping` must return HealthyBusy during the
   background solve so the idle reaper does not kill the microVM between reconnects.
3. **Dual-socket model.** Two WS per tab on one localStorage session_id maps
   ACCEPTABLY -- both carry the same session_id and land in the SAME microVM
   (matching intent) -- but the cross-socket SessionState reconciliation must be
   re-validated under AgentCore's routing, and two concurrent long-lived sockets
   per user roughly DOUBLE the per-session connection footprint (and the WS-stream
   TPS quota consumption).
4. **Frame + auth plumbing.** 64 KB WS frame-size cap and 250 frames/sec
   per-connection -- any larger payload must be CHUNKED (our pipeline/layer
   envelopes need an audit). The wss endpoint terminates at
   `wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/ws` (SigV4 or
   OAuth-JWT), NOT our CloudFront origin; the browser cannot set handshake headers,
   so the Cognito JWT must ride the `Sec-WebSocket-Protocol`
   `base64UrlBearerAuthorization` subprotocol (exactly the path the prior eval
   flagged). This forces splitting TiTiler:8080 + catalog:8766 onto their own
   origin and repointing CloudFront.

### 6.4 Deployment + the gates (run IN ORDER, stop at first fail)

Container contract: Host `0.0.0.0`, Port `8080`, Platform **ARM64 (mandatory)**.
Implement up to three paths: `/invocations` POST (JSON in, JSON/SSE out), `/ws`
WebSocket (persistent bidirectional), `/ping` GET (health: Healthy or HealthyBusy
+ time_of_last_update -- HealthyBusy is how a >60-min Batch solve survives idle
reaping). Deploy via the AgentCore CLI (`agentcore create/deploy`) or
`CreateAgentRuntime`; the `bedrock-agentcore` Python SDK (BedrockAgentCoreApp +
`@app.websocket` / `@app.entrypoint`) generates the contract.

Can the EXISTING container run as-is? NO -- three blockers:

- **GATE A (image):** rebuild for `linux/arm64`, fit the 2 GB image cap. The
  GDAL/QGIS geo deps are the risk -- BUT those are tool-EXEC concerns that should
  be split off the loop image (the loop only needs Bedrock + the 94 tool
  DEFINITIONS + the WS layer; heavy geo runs on Batch/QGIS-worker already). Split
  them out and the loop image is plausibly well under 2 GB.
- **GATE B (resource):** profile in-loop peak CPU/RAM during a real
  multi-turn + Batch-poll session vs the **2 vCPU / 8 GB HARD cap** (not
  adjustable). The agent loop is I/O-bound (Bedrock + Batch poll + S3), so this is
  likely fine, but must be measured.
- **GATE C (WS re-architecture):** the section-6.3 four fronts, proven end-to-end
  against a >60-min solve with hourly reconnect on the same session_id.

If A+B+C pass: BedrockAgentCoreApp wrapper + customJWTAuthorizer (Cognito
`.well-known`) + React WS sends the Cognito JWT via the `base64UrlBearerAuthorization`
subprotocol + SPLIT TiTiler:8080 & catalog:8766 onto their own origin + repoint
CloudFront + keep a thin-WS-proxy fallback. KEEP UNCHANGED: Bedrock
Converse+cachePoint, the 94 in-process tools, the Batch+S3 contract, DynamoDB,
the Cognito pool. Pricing: $0.0895/vCPU-hr + $0.00945/GB-hr per-second, idle CPU
free, 128 MB min memory. Full migration if the gates pass = ~8-15 eng-days.
Lock-in is LOW (the `/invocations + /ping + /ws` container ports back to
Fargate/EC2). Region: us-west-2 is supported.

**Why LATER, not NOW:** Gate C (the WS re-architecture + the >60-min/hourly-
reconnect proof) is unproven, and it is the highest-risk piece because it
re-plumbs the entry point and the client reconnect cadence simultaneously. The
incident needs a fix THIS sprint. Fargate delivers isolation with Gate C's risk
entirely AVOIDED (no listener change, no 60-min cap). So: ship Fargate, then run
the AgentCore gates as a time-boxed spike behind the now-stable isolated baseline.

---

## 7. Incremental migration plan (from the single EC2 box)

Dark-build then canary then origin-cutover then decommission -- the single box
stays instantly startable as rollback the whole way.

0. **READ-ONLY BASELINE.** Confirm deployed==HEAD on the box (grep the box per the
   SWMM-offbox lesson). Snapshot the working single-box `/ws` path. Capture
   today's `/api/health` `busy` semantics (`liveness_snapshot`) as the contract
   every per-session task must keep.
1. **CONTAINERIZE THE AGENT (no behavior change).** Build a per-session image from
   the existing on-box build path; it runs `main.run()` exactly as the systemd
   unit does, exposing `:8765` (WS) + `:8766` (/api/health). Container hygiene per
   the project norm (multi-stage, minimal base, .dockerignore -- the agent image
   is geo-heavy so the pull feeds cold-start). Prove one instance serves ONE
   session E2E (auth handshake -> turn -> Batch dispatch -> S3 publish) identically
   to the box.
2. **STAND UP THE ROUTE TABLE.** Create DynamoDB `grace2_session_routes` (hash
   `user_ulid`, range `session_id`) reusing the autostop DynamoDB module pattern.
   No traffic yet.
3. **BUILD THE THIN BROKER (dark).** ALB + a Lambda/ECS authorizer-proxy that runs
   `cognito_verify` (lift the exact logic already duplicated in the wake Lambda),
   resolves sub->User ULID (Persistence), reads/writes `grace2_session_routes`,
   and WSS-proxies to a target `:8765`. Unit-test JWT->ULID->target against the
   agent module's OWN `cognito_verify` so it cannot drift. (Use CloudFront->ALB,
   NOT API Gateway WS -- its 2h/10min caps would sever long SFINCS turns.)
4. **PROVISION-ON-CONNECT + READINESS.** Wire the broker to `RunTask`/start a task
   on a route miss, gate on `:8766` health-green, write the row, then proxy.
   Reuse the EXISTING wake overlay/edge-shimmer for the cold-start state -- no new
   client UI. Optional: a 1-task warm pool to buy down cold-start (costs 1 idle
   task).
5. **CANARY ONE NON-DEFAULT ROUTE.** Stand the broker up on a SEPARATE
   hostname/path (not the live `/ws` origin yet). Drive a full live session
   (NATE's claude.e2e account) through broker->task: dual-socket App+Chat converge
   on one task, turn + SFINCS Batch solve + publish, reconnect/heartbeat (12s) all
   green. **Verify isolation: a forced crash in one session's task does NOT touch a
   second concurrent session** (the direct incident proof).
6. **PER-TASK IDLE REAPER.** Generalize `idle_check` to poll each live task's
   `/api/health` and `ecs:StopTask` + delete-route after `IDLE_THRESHOLD_CHECKS`
   not-busy ticks, keeping the G3 Batch guard and the Stage-3 idle-open-tab rule.
   Keep the single-box autostop ARMED during canary as the fallback.
7. **CUT THE CLOUDFRONT /ws ORIGIN OVER.** Repoint E2L74AS56MVZ87 `/ws*` from
   EC2:8765 to the broker (ALB) origin. Client URL (`wss://.../ws`) UNCHANGED;
   invalidate. Watch live: existing sessions reconnect through the broker. Keep
   the old box startable as instant rollback (flip the origin back).
8. **DRAIN + DECOMMISSION THE SINGLE BOX.** Once per-session routing is proven over
   a real demo window, stop routing to `i-0251879a278df797f`, retire its single-box
   idle_check/wake Lambdas (or repurpose wake as the broker's provisioner), and
   downsize/terminate the box. TiTiler tiny box + Batch island + DynamoDB + S3 stay
   exactly as-is.
9. **COST + ISOLATION VERIFY.** Confirm steady-state agent cost ~unchanged at
   current load (mostly zero tasks), the new fixed broker/ALB line item is
   acceptable, and per-session blast-radius isolation holds under 2+ concurrent
   sessions. Log the workflow cost per the cost_tracking norm.

(LATER, post-Fargate, optional) **AGENTCORE SPIKE.** Run section-6.4 gates A->B->C
on a branch behind the stable isolated baseline; if all pass, cut the agent loop
to Runtime and retire the broker/registry/reaper (AWS owns isolation). Lock-in is
LOW, so this is reversible.

---

## 8. What carries over UNCHANGED vs what is net-new

UNCHANGED (the whole point -- isolation is an infra reshaping, not an agent rewrite):

- **THE AGENT CODE.** `server.py` / `main.py` / `auth_handshake.py` /
  `bedrock_adapter.py` / all 94 tools run byte-identical. The module-level
  per-session dicts (`_SESSION_WS_CONNECTIONS`, `_SESSION_LIVE_TURNS`,
  `_SESSION_ACTIVE_CASE`, `_SESSION_ANON_ID`, SESSION_HUB-paired sockets) hold ONE
  session per process instead of many -- the dual-socket App+Chat convergence is
  preserved because the broker co-locates both sockets. The 12s heartbeat,
  ping_interval=20/ping_timeout=20, and the Cognito handshake run as-is INSIDE
  the task. Per-task, the session model gets SIMPLER (one session owns the
  process), eliminating cross-session contention by construction.
- **THE BATCH SOLVER ISLAND.** `solver.py batch.submit_job` -> `grace2-solvers`
  (Spot, scale-to-zero) is already a separate island the agent only dispatches to
  + polls via S3-completion. Per-session tasks dispatch to the SAME shared Batch
  queue; no change. Heavy compute was never on the agent box.
- **DynamoDB** (users/cases/chat/session records, prefix `grace2_`) and **S3**
  (runs + cache buckets) -- shared, owner-scoped by User ULID; per-session tasks
  read/write exactly as today. Owner scoping (H.2) already isolates DATA by user;
  this work isolates COMPUTE by session.
- **TiTiler** -- already isolated to its own tiny always-on box
  (`i-06cfdd3d6c66b2126`) behind CloudFront `/cog+/tiles`; map serves 24/7.
  Untouched.
- **CloudFront E2L74AS56MVZ87 + the client's baked `wss://.../ws` URL** -- only the
  `/ws` ORIGIN changes (single EC2 -> broker); the client is unchanged.
- **`cognito_verify` logic, the wake overlay UX, the DynamoDB idle-streak
  pattern** -- all REUSED by the broker/reaper.

NET-NEW (the real cost -- an infra + routing-tier build around an unchanged agent):

- The thin BROKER/GATEWAY (ALB + Lambda/ECS authorizer-proxy) doing JWT verify ->
  ULID -> session->target resolve -> WSS proxy, replacing the single EC2:8765
  origin.
- The `grace2_session_routes` DynamoDB table.
- Making the Cognito token + session_id readable PRE-WS-upgrade
  (query-param/subprotocol) for routing.
- ECS task definition + IAM: broker needs RunTask/StopTask/DescribeTasks; the task
  role = today's agent instance role (Bedrock, S3 runs/cache, Batch
  submit/describe, DynamoDB).
- VPC/ENI/SG plumbing + per-task idle-stop orchestration.

No application/business-logic rewrite.

---

## 9. Blockers / watch-items (load-bearing, in priority order)

1. **Session affinity needs a custom broker -- ALB stickiness does NOT solve it.**
   (Section 4.1.) This is the central piece of real engineering; get it wrong and
   the dual sockets split-brain across tasks.
2. **The Cognito token must be readable PRE-WS-upgrade for routing.** Today it
   rides in-band post-connect. Move it to a subprotocol/query-param the broker can
   read at handshake. (Same plumbing AgentCore would need anyway -- so this work
   is not wasted if we later cut to Runtime.)
3. **Cold-start = the real UX cost.** Fargate task start is ~30-40s (provision +
   ENI + image pull); the geo-heavy image makes the pull dominate. Comparable to
   today's EC2 cold-wake the user already tolerates (the wake overlay covers it).
   Mitigate with a minimal multi-stage image and an optional 1-task warm pool.
4. **Fan-out cost watch-item.** N concurrent users -> N tasks billing at once;
   wins on cost only at LOW concurrency. Aggressive idle thresholds + Lambda@Edge
   broker keep the floor down.
5. **(AgentCore-LATER) the 60-min WS cap + listener ownership.** Gate C of the
   AgentCore path -- unproven, highest-risk, and the reason AgentCore is LATER not
   NOW. Avoided entirely by the Fargate path.

---

## 10. Recommendation

**HYBRID_FARGATE_NOW_AGENTCORE_LATER.** Build Fargate-per-session isolation now to
fix the cross-user-interference + kill-collateral incident: a thin Cognito-verifying
broker routes each session to its OWN ephemeral Fargate task running the EXISTING WS
agent UNCHANGED, idle-stopped to zero, with the autostop primitives generalized from
the box to the task. This preserves the long-lived dual-socket 12s-heartbeat
streaming model byte-identically and buys hard per-user blast-radius isolation with
no agent rewrite. Then, behind that stable isolated baseline, run the AgentCore
Runtime gates (ARM64 <=2GB image; <=2vCPU/8GB in-loop; and critically the WS
re-architecture proven against a >60-min solve with hourly same-session_id
reconnect) as a time-boxed spike -- and if they pass, cut the agent loop to Runtime
so AWS owns isolation/listener/scaling and we retire the broker. Lock-in is LOW
either way; cost is not the deciding factor; isolation + an intact streaming model
is the win.

Reject STAY_SHARED (it is the incident). Reject pure-AGENTCORE-now (its WS
re-architecture is unproven and the incident needs a fix this sprint).

---

## Sources

Primary (AWS Bedrock AgentCore Runtime, current GA):
- Runtime get-started WebSocket: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-websocket.html
- Runtime HTTP protocol contract: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html
- AgentCore limits: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html
- Runtime how-it-works: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-how-it-works.html
- Runtime sessions: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html
- Bi-directional streaming GA (Dec 18 2025): https://aws.amazon.com/blogs/machine-learning/bi-directional-streaming-for-real-time-agent-interactions-now-available-in-amazon-bedrock-agentcore-runtime/
- AgentCore pricing: https://aws.amazon.com/bedrock/agentcore/pricing/
- Securely launch + scale agents on Runtime: https://aws.amazon.com/blogs/machine-learning/securely-launch-and-scale-your-agents-and-tools-on-amazon-bedrock-agentcore-runtime/

Internal (live GRACE-2, the source of truth for the streaming model):
- `services/agent/src/grace2_agent/server.py` -- `run_server` (9765), `serve(...)`
  (9839, ping_interval=20/ping_timeout=20/close_timeout=10), `HEARTBEAT_INTERVAL_SECONDS=12.0`
  (1625), `_heartbeat_loop` (1628), `is_busy` (1296), `liveness_snapshot` (1331),
  the `_SESSION_*` dicts (867/1018/1118/8959), `GRACE2_AGENT_HOST=0.0.0.0` (9786).
- `services/agent/src/grace2_agent/auth_handshake.py` -- `cognito_verify` (RS256 /
  pool JWKS / iss / aud / token_use==id), `GRACE2_COGNITO_USER_POOL_ID` gate,
  `get_user_by_firebase_uid` auto-provision, internal ULID (Decision 10).
- `web/src/ws.ts` -- SESSION_KEY 'grace2.session_id' (402), dual-socket SESSION_HUB
  (609), capped-backoff reconnect + wake-on-reconnect hook, `auth-token` +
  `session-resume` handshake.
- `infra/aws-autostop/` -- idle_check Lambda (`IDLE_THRESHOLD_CHECKS`,
  `/api/health` busy probe, G3 Batch guard on `grace2-solvers` SUBMITTED..RUNNING,
  `ec2:StopInstances`), wake Lambda, DynamoDB idle-streak store.
- Memory: `project_agentcore_evaluation.md` (prior 2026-06-17 HYBRID eval, pre-WS-GA
  -- this spike revises its Runtime-protocol assumption);
  `project_scale_to_zero_island_architecture.md`;
  `project_execution_architecture_norm.md`; `project_aws_deploy_facts.md`;
  the WS-stability cluster (`project_ws_30s_heartbeat_fix.md`,
  `project_ws_flicker_rootcause_2026_06_20.md`).
