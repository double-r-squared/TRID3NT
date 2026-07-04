# Scale-to-Zero Architecture: Read Pass + Assessment
2026-07-04. Read pass = 5 parallel subsystem audits (broker/isolation, agent server, Batch/workers, always-on infra, web contract) with live AWS verification. Account 226996537797, us-west-2.

---

# PART 1 -- WHERE THE ARCHITECTURE IS NOW

## 1.1 The tiers

```
Browser (Vercel SPA)
  |-- static: Vercel CDN (web app), CartoDB basemaps, S3 cold tool-catalog
  |-- serverless cold paths: API GW 9ib093sis6 -> Lambdas -> DynamoDB/S3
  |     (/case-list, /case-view-url, /case-export-url, /demo-token, /wake)
  |-- tiles: CloudFront /cog* /tiles* -> TiTiler EC2 t3.small (always-on)
  '-- live session: CloudFront /ws* -> ALB -> broker Fargate (always-on, 0.5vCPU/1GB)
        '-- RunTask per session -> agent Fargate task (2vCPU/8GB, ephemeral)
              |-- Bedrock converse_stream (LLM turns)
              |-- DynamoDB (trid3nt_* tables) + S3 (runs/cache buckets)
              '-- AWS Batch grace2-solvers (Spot-first, min vCPU 0)
                    '-- per-engine workers: build -> solve -> postprocess -> S3
                        (completion.json + publish_manifest.json)
```

## 1.2 What already scales to zero (verified live)

| Tier | Mechanism | Idle cost |
|---|---|---|
| Batch compute | Both CEs min/desired vCPUs = 0; Spot-first queue, on-demand fallback CE at order 2 | $0 (ECR storage only, ~$3-5/mo) |
| Per-session agents | No ECS service; broker RunTasks on demand. Teardown = reaper (5-min tick, idle streak 3 = 15 min, 90-min max-age) + agent self-idle-exit (30 min) + route-row TTL (24 h) | $0 at zero users; ~$0.34/hr per active session at 8 GB |
| Heavy build/postprocess | Offloaded to Batch workers (SFINCS build, MODFLOW build+plume, 6 MODFLOW archetypes; GeoClaw/SWMM/OpenQuake/Landlab postprocess manifest-gated with in-agent fallback). Live-verified 2026-07-03 | $0 between jobs |
| Serverless reads | case-list / case-view signer / export / demo-token Lambdas; DynamoDB on-demand; CloudFront per-use; Cognito free tier | ~$0 |
| Web | Vercel (its own tier), S3 static web bucket | ~$0 |

The client is ALREADY BUILT for a scale-to-zero backend: cold case-list + snapshot case-view + always-on tile URLs render box-off; identity restores from a localStorage refresh token with no agent; the wake overlay + 1.5-5 s capped-jitter reconnect + outbound message queue absorb a 60-90 s cold provision. First-connect cold provision is the designed-for path, not an exception.

## 1.3 What does NOT scale to zero -- the idle bill (~$92/mo at zero users)

| Component | $/mo | Why it exists |
|---|---|---|
| ALB grace2-agent-broker | ~17 | WS entry; chosen over API GW WS for the 4000 s idle timeout (API GW caps 2 h / 10 min idle, kills long solve turns) |
| Broker Fargate (0.5 vCPU/1 GB, desired 1) | ~11 | Long-lived WS proxy: token verify -> route lookup -> RunTask cold provision -> bidirectional frame pump. CANNOT be request-scoped (holds two sockets open for the session lifetime) |
| ECS + Batch Interface VPC endpoints | ~29 | Exist ONLY because the reaper Lambda is VPC-attached (must HTTP-probe agent tasks' private IP :8766) and a VPC Lambda has no other route to AWS APIs (no NAT by design) |
| TiTiler EC2 t3.small + EIP + EBS | ~20 | COG tile rendering; EC2-not-Lambda because of the job-0314 wedge history + watchdog restart loop + warm GDAL /vsis3 connections |
| Broker task public IPv4 | ~3.65 | All-public-subnet VPC (no NAT -- saves ~$35-40/mo but every task pays $0.005/hr per IP) |
| LEGACY: stopped agent box EIP + 30 GiB EBS | ~6 | i-0251879a278df797f STOPPED but not decommissioned |
| S3 storage | ~2-5 | Runs/cache/web/bundle buckets |
| DynamoDB / Lambda / EventBridge / CloudFront / Cognito | ~0 | All on-demand |

## 1.4 Broker deep facts (from source)

- All routing state in DynamoDB grace2_session_routes (PAY_PER_REQUEST, TTL 24 h, PITR). In-process state = provisioning locks + JWKS cache only. A broker restart loses nothing; reconnects re-resolve.
- Cold provision: RunTask -> DescribeTasks poll (2 s) -> RUNNING+ENI -> health-probe :8766 poll (2 s) -> route PutItem. ~40-48 s documented; 8 s DATA-frame keepalive to the client defeats the 10 s pong watchdog during the wait.
- Dual-socket convergence via per-(user, session) in-process lock -- CORRECT at desired_count=1, documented double-RunTask race at >=2 brokers (conditional PutItem guard not yet implemented).
- Image is ~1.4 GB (installs the full agent package for zero-drift cognito_verify reuse) -- vastly oversized for what the process does.
- Known fragilities: stale route row after task crash (up to 5 min of failed dials until reaper tick -- broker does not delete route on failed upstream dial); provision timeout 90 s vs agent health startPeriod 120 s mismatch; G3 Batch guard is GLOBAL (any in-flight solve pins ALL idle sessions alive, not just the owner's).

## 1.5 Agent server deep facts (from source)

- One asyncio process: WS :8765 + catalog HTTP :8766; 12 s per-connection DATA heartbeat; 30 s idle-exit monitor (os._exit(0) after 30 min idle); coldview backfill at boot.
- Startup imports ~130-160 tool modules; several eagerly import rasterio/GDAL + numpy; google-genai SDK imported at module level (types lingua franca) even on Bedrock. GDAL alone ~300-500 MB RSS. This -- not the routing -- is the memory floor.
- Durable state: DynamoDB (cases, chat, sessions, users, secrets, telemetry) + S3 (case-views snapshots, manifests, run outputs). In-memory-only: pending confirmation/credential/region/spatial Futures, live-turn registry, session->case pointers (rehydrated on session-resume). Mid-turn process death loses the in-flight LLM turn but NOT the Batch job (worker completes independently to S3).
- Post-offload in-agent heavy remainder: the 38-tool _ALWAYS_OFFLOAD_SYNC_TOOLS fetcher set (rasterio/xarray, 50-200 MB per active thread), vector densify (shapely), geopandas county fetch, and the in-agent postprocess FALLBACKS (fire only when a worker manifest is absent).
- Portability split: ~60% of server.py is pure I/O orchestration (WS framing, Bedrock call, DynamoDB CRUD, auth, heartbeats, gates) -- portable to any language. ~40% is the geospatial tool seam -- Python-locked (rasterio, GDAL, xarray, shapely, geopandas, flopy, hydromt).

## 1.6 Batch tier deep facts

- One queue (grace2-solvers): Spot CE order 1 (SPOT_CAPACITY_OPTIMIZED, 100% bid, max 96 vCPU), on-demand CE order 2 (max 64). 20 instance types x 4 sizes x 4 AZ after the 70-min RUNNABLE-stall incident.
- Agent sizes every job via containerOverrides from the compute-class ladder (small 4/8 G -> xlarge 48/96 G) selected by estimated mesh elements.
- Worker contract: outputs to s3://runs/<run_id>/; publish_manifest.json written BEFORE completion.json (Spot-reclaim atomicity); agent polls completion.json every 10 s off-loop + DescribeJobs for phase/early-fail.
- Fail-fast submit (5 s connect / 15 s read / 3 attempts) after the 3-min-hang incident. Per-turn ContextVar tracks in-flight jobs; turn-cancel terminates them (orphan-job guard).
- All images :latest mutable tags -- digest pinning is a flagged production TODO.

## 1.7 Web contract facts

- Endpoint classes: (a) live-agent WS (chat, case commands, gates); (b) serverless cold (case-list, view signer, export, demo-token, wake -- all 10 s AbortController); (c) static/CDN (basemaps, tile URLs, cold catalog). Tiles bypass the agent entirely once the URL is stored -- but only http(s) URIs are cold-renderable; bare s3:// layer handles are dropped box-off.
- Keepalive: client session-resume every 25 s; ANY inbound frame resets a 10 s pong deadline; a silent server -> force-reconnect within 35 s. The agent's 12 s data heartbeat satisfies this.
- HARDENING GAPS (found in ws.ts): credential-provided / region-choice-provided / spatial-input-response / tool-payload-confirmation / layer-delete / secret-add / secret-revoke are NOT queued -- silently dropped if the socket is not OPEN (mid-wake answers lost; server re-emits on resume but UX breaks). Dev-only: cases spinner can spin forever if no cold-list URL configured. Long token refresh during a 60-90 s wake handled (one 4401 retry).

## 1.8 Legacy / dead weight (found live)

- Old agent box i-0251879a278df797f (t3.xlarge) STOPPED since 2026-06-29 but still provisioned: EIP 54.185.114.233 + 30 GiB EBS = ~$6/mo. CloudFront origin-agent-ws + origin-catalog still point at its EIP (dead origins; /ws* already moved to origin-broker-ws).
- autostop idle-check Lambda still polls the stopped box 288x/day (no-op); wake Lambda still starts THE OLD BOX if tapped -- a t3.xlarge nobody uses ($0.166/hr if a user ever taps Wake). Architecturally superseded by the reaper + broker provision.
- grace2_* DynamoDB tables are pre-rename orphans (live prefix is trid3nt_): grace2_chat 1735 items, grace2_cases 167, grace2_users 209, grace2_sessions 182 -- zero fixed cost but dead data to archive/delete.
- VPCE SG still allow-lists the old box's SG; SG description stale.
- No S3 Gateway endpoint (free) despite TiTiler's continuous /vsis3 range reads.

---

# PART 2 -- ASSESSMENT: WHERE IT CAN GO

## 2.1 Classification

| Component | Verdict | Note |
|---|---|---|
| Batch compute | DONE -- true zero | Nothing to do |
| Per-session agents | DONE -- zero at idle | Shrink 8->4 GB later (usage-confirm); lazy-import + Phase-2 fetcher offload can push toward ~2 GB |
| Serverless read paths, DynamoDB, S3, CloudFront, Cognito | DONE -- effectively zero | Keep |
| Broker + ALB (~$32/mo) | CAN GO NEAR-ZERO | Consolidate onto the tiny always-on box; ALB deletable (see 2.2) |
| Interface VPC endpoints (~$29/mo) | CAN GO TO ZERO | Make the reaper VPC-free via DynamoDB heartbeats (see 2.3) |
| TiTiler EC2 (~$20/mo) | KEEP (the one always-on) | Matches the north star: ONLY tiny TiTiler always-on. Lambda-TiTiler is a later refinement, not now (wedge history, GDAL cold start) |
| Legacy box + Lambdas + orphan tables | DELETE | ~$6/mo + risk (wake starts a dead-end t3.xlarge) |

## 2.2 Target architecture (Option A -- recommended)

ONE tiny always-on box (the existing TiTiler t3.small) runs BOTH always-on daemons as systemd units:

```
CloudFront E2L74AS56MVZ87
  /cog* /tiles*  -> EC2 t3.small :8080  (titiler.service, existing watchdog)
  /ws*           -> EC2 t3.small :8081  (broker.service, NEW home)
  /*             -> web bucket / Vercel (unchanged)
API GW 9ib093sis6 -> Lambdas (unchanged serverless cold paths)
```

- The broker is a ~50 MB-RSS proxy; the t3.small has ~1 GB headroom next to TiTiler's 2-worker ~500 MB. Same SPOF count as today (both singletons already), fewer moving parts, each unit systemd-auto-restarted + watchdogged.
- CloudFront -> EC2-origin WS is the PROVEN pre-broker pattern (the old box ran exactly this for weeks; the 12 s data heartbeat keeps the connection alive through CloudFront).
- Deletes: ALB (~$17), broker Fargate (~$11), broker public IP (~$3.65). The ALB's one unique feature (4000 s idle timeout) is not needed on a direct EC2 origin.
- Everything downstream (RunTask provision, routes table, reaper, agents, Batch) unchanged -- this move relocates the broker, it does not redesign it.

Idle bill after Option A + cleanups: TiTiler+broker box ~$15 + EIP ~$3.65 + EBS ~$1.60 + S3 ~$2-5 = ~$22-25/mo (from ~$92). Everything else literally zero. At-scale cost is unchanged (sessions + solves bill identically).

Option B (maximal, NOT recommended now): TiTiler -> Lambda-TiTiler and broker -> wake-on-demand (client wake pattern sets desired-count). Gets idle to ~$5/mo but adds cold-start latency to first tile + first WS, reintroduces the wedge-class risk Lambda can't watchdog, and multiplies edge cases -- against the reliability/simplicity goal. Revisit only if the ~$20 floor matters later.

## 2.3 Kill the interface endpoints: heartbeat-reaper redesign

Today: reaper is VPC-attached ONLY to HTTP-probe each agent task's private :8766 -> which forced the ECS+Batch interface endpoints (~$29/mo) and caused one outage (VPCE private-DNS blackhole).

Redesign: the agent ALREADY tracks busy/active_connections for its own idle-exit. Have it WRITE that as a heartbeat to its route row every ~60 s (UpdateItem: last_seen, busy, in-flight-Batch count). The reaper then:
- reads ONLY DynamoDB (no VPC attachment, no endpoints needed),
- reaps on stale last_seen (missed heartbeats) instead of failed HTTP probes,
- gains a PER-SESSION Batch guard for free (the agent reports its own in-flight jobs -- fixes the G3 global-pin defect where one user's solve keeps every idle session alive),
- and PASS-2 orphan/max-age enumeration via ECS APIs works fine from a non-VPC Lambda.

Deletes both interface endpoints (-$29/mo), removes the VPCE SG allow-list coupling, and makes the reaper simpler and less outage-prone. Agent tasks and broker keep reaching AWS APIs via their public IPs (the pre-endpoint path).

## 2.4 The Go question -- answered honestly

Would rebuilding the server (websockets/endpoints) in Go get us to scale-to-zero better? **No -- the idle bill is architectural, not linguistic.** Python is not why the ALB, endpoints, TiTiler box, or broker task bill at idle; topology is. Rewriting does not remove one always-on component that the consolidation above does not already remove.

Where Go IS the right tool, scoped tightly: **the broker.** It is ~600 lines of pure I/O (JWT verify, DynamoDB get/put, ECS RunTask, two-socket frame pump) with zero geospatial coupling. As a Go static binary it is ~15 MB image / ~20-40 MB RSS / ms-startup -- ideal as the systemd unit on the shared t3.small, and it removes the absurd 1.4 GB Python broker image. Cost: reimplement Cognito JWKS verify (standard library territory) and port the drift-guard tests; the zero-drift-by-import argument is replaced by contract tests. This is OPTIONAL for Option A (the Python broker also runs fine on the box) -- recommended as a fast-follow, not a blocker.

Where Go is the WRONG tool: **the agent.** ~40% of it is the Python geospatial seam (rasterio/GDAL/xarray/shapely/geopandas/flopy/hydromt) and every one of the ~160 tools is Python. A Go agent would still need Python sidecars for all real work -- more processes, more contracts, more bugs, for a component that already costs $0 at idle. The agent's real optimization is Python-native: lazy-import the eager GDAL/numpy tool imports + drop the genai SDK types dependency + finish the Phase-2 fetcher offload -> the 8 GB task shrinks toward 4 GB/2 GB, cutting the per-ACTIVE-session rate (idle is already zero).

## 2.5 Reliability + responsiveness hardening (fold into the same effort)

1. Client: route the 7 non-queued frame types through sendOrQueue (mid-wake answers currently vanish) -- small ws.ts change, big UX edge-case kill.
2. Broker: delete the route row on failed upstream dial (closes the 5-min stale-route reconnect-fail window after a task crash).
3. Broker: conditional PutItem (attribute_not_exists) on route write -- makes multi-broker scale-out safe before it is ever needed.
4. Broker: raise provision timeout 90 s -> 150 s (matches agent startPeriod 120 s + margin; the live-drive verified ~90 s real cold provisions).
5. Reaper: per-session Batch guard (comes free with 2.3 heartbeats).
6. Batch: pin worker images by digest (kills the :latest drift class).
7. Publish: enforce http(s) tile URLs at publish time (bare s3:// layers are invisible box-off today).

## 2.6 Phased migration (bug-minimizing order; each phase independently shippable + revertible)

- **Phase 0 -- pure deletion (no behavior change):** decommission the old agent box (snapshot EBS, terminate, release EIP), retire idle-check Lambda + grace2-autostop-state, remove dead CloudFront origins, retarget or disable legacy /wake (client treats wake-unconfigured as "connecting" -- verified in ws.ts), archive grace2_* orphan tables, add the free S3 Gateway endpoint. Saves ~$6/mo + removes the tap-Wake-starts-a-t3.xlarge trap. Risk: near zero.
- **Phase 1 -- heartbeat reaper (2.3):** agent writes heartbeats; reaper reads DynamoDB; run both probe modes in parallel one day (reaper logs agreement) then flip; detach reaper from VPC; delete both interface endpoints. Saves ~$29/mo. Risk: low (agents also self-idle-exit; TTL backstop remains).
- **Phase 2 -- broker onto the box (2.2):** add broker.service to the TiTiler box IaC (port 8081, systemd + watchdog), add CloudFront origin, TEST via the ready-made headless flood smoke on a parallel path, then flip /ws* origin; drain + delete ALB and the broker ECS service. Saves ~$32/mo. Rollback = flip the CloudFront origin back (minutes). Optional fast-follow: replace the Python broker unit with a Go binary (2.4) once behavior-parity contract tests pass.
- **Phase 3 -- client hardening (2.5.1 + 2.5.7):** queue the non-queued frames; enforce tile-URL publishing. Independent of the infra phases.
- **Phase 4 -- agent diet (2.4):** lazy imports, genai-types removal, Phase-2 fetcher offload, then 8->4 GB (later ~2 GB) on live evidence. Cuts the per-active-session rate ~2-4x.

Every phase gates on the standing post-change check: a small-AOI SFINCS pluvial flood driven via Haiku end-to-end (headless driver exists) + cold-view box-off verification.

## 2.7 End state

Idle: S3 + DynamoDB storage, Lambdas asleep, Batch at zero, zero agent tasks, ONE t3.small (tiles + session entry) behind CloudFront -- ~$22-25/mo, ~75% below today's ~$92 idle. Load: unchanged burst model -- per-session Fargate tasks + Spot Batch scale with demand and tear down on their own, with the same (now-hardened) reliability seams: heartbeats, reaper, self-idle-exit, TTL, manifest-gated fallbacks, wake overlay.
