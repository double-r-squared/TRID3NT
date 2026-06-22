# Cold-view "no layers" (box off) - root cause + minimal fix

Status: design (SYNTHESIS lane). Date: 2026-06-21.
Symptom (live, repeated): with the agent box asleep, opening a Case shows the
case in the rail and the chat history, but the map shows "no layers loaded".
Source: 3 read-only traces (snapshot-write, layer-renderability, web cold-render).

---

## 1. PRECISE root cause

It is NOT a web cold-render drop, NOT a seatbelt eviction, and NOT a
layers-empty-at-build bug. The web faithfully decodes whatever the snapshot
carries (chat and layers come from the SAME `case-views/{case_id}.json` payload
through two independent decoders - `Chat.tsx` for chat, `Map.tsx`/`App.tsx` for
`session_state.loaded_layers`), and the `#158` empty-frame guard
(`web/src/lib/layer_cache.ts:304-311`) cannot drop a layer-bearing cold frame.
So "chat shows, layers do not" can ONLY mean the S3 snapshot object itself has a
populated `chat_history` but an empty/stale `loaded_layers`.

The decisive root cause is a STALE / LOST snapshot object, driven by a
fire-and-forget write that is never flushed before the box stops:

ROOT CAUSE A (primary - lost write at box-stop). All three snapshot write
sites stamp the write as a DETACHED background task and never wait for it:
- dispatch finally-block, `server.py:7205-7209`
- `publish_layer` WMS-string wrap-site, `server.py:7394-7400`
- turn-close, `server.py:8167-8171`
Each does `asyncio.create_task(_persist_case_view_snapshot(...))` into
`_BG_SNAPSHOT_TASKS` (defined `server.py:875`). Grep confirms that set only ever
gets `add()` / `discard()` - there is NO `gather`, NO FastAPI `lifespan` /
`on_event("shutdown")`, NO SIGTERM handler that drains it anywhere in
`server.py` or `main.py`. Meanwhile the auto-stop gate is blind to these tasks:
`is_busy()` (`server.py:1267`) = `inflight_turn_count() > 0 or
solve_in_flight_count() > 0` - it tracks in-flight TURNS and solves only, and
flips false the instant the turn returns. The idle Lambda
(`infra/aws-autostop/lambda/idle_check/handler.py`) polls `/api/health`'s `busy`
flag and, after its consecutive-idle streak, calls `ec2.stop_instances`
(handler.py:216) - an immediate EC2 halt, not a graceful uvicorn drain. So the
box can stop after the turn returns but BEFORE the detached snapshot S3 PUT
lands; the task dies with the box and `case-views/{case_id}.json` is left at its
PRIOR (often the empty create-time) contents. Chat survives because per-turn
chat is persisted SYNCHRONOUSLY (awaited) before the box sleeps; layers do not,
because only the layer DURABILITY persist (`_persist_case_loaded_layers`,
awaited at `server.py:7190` / `7383`) is synchronous - the snapshot REBUILD that
turns those durable summaries into the cold S3 object is the unflushed
fire-and-forget step.

ROOT CAUSE B (secondary - vector refs are agent-only handles). Even when the
snapshot DOES land, vectors can still paint blank cold because their snapshot
`uri` is an agent-only `s3://...fgb|geojson` DATA handle the browser holds no
creds to read (Invariant-5). Vectors only paint cold if `inline_geojson` was
embedded, and that embed happens ONLY when the live emitter held it AND the
mutated case == the open case on this connection (`server.py:7597-7602`). So a
cross-case mutation (rename Case B while Case A open) writes a vector-URI-only
snapshot, and a cold reopen has no emitter to run `reinline_vector_layers`
(`pipeline_emitter.py:1072`, open-socket only) to self-heal. Rasters are
immune: their snapshot `uri` is already the resolved TiTiler
`/cog/tiles/.../{z}/{x}/{y}.png?url=<s3 COG>` template
(`publish_layer.py:1981-2001`), served by the always-on TiTiler+CloudFront box,
so rasters paint cold whenever the snapshot carries the entry.

ROOT CAUSE C (compounding - no list-side fallback). The cold case-LIST envelope
already carries `loaded_layer_summaries`
(`infra/aws-autostop/lambda/case_list/handler.py:108-109`), but `onCaseList`
(`web/src/hooks/useCases.ts:155-184`) only `setCases()` the rail and drops those
summaries. `onCaseOpen` (useCases.ts:186-206) is the ONLY path that feeds layers
to the map, and it reads only the per-case snapshot. So there is no second
source that could paint layers when the snapshot is stale/empty/404 - the single
durable source of cold layers is `case-views/{case_id}.json`.

Net: the snapshot CONTENT model is sound (rasters carry a cold-renderable tile
template; the merge path embeds inline GeoJSON for vectors). What fails is the
snapshot WRITE's durability (A) plus two narrower content gaps (B cross-case
vectors, C no fallback). This is a targeted server-side flush + content fix, NOT
evidence that the snapshot model is fundamentally inadequate, so it does NOT
require the full `#165` rebuild.

---

## 2. MINIMAL fix (targeted server fix; not the #165 rebuild)

Keep the per-case `case-views/{case_id}.json` snapshot as the cold source of
truth. Three changes, in priority order:

FIX A (mandatory - make the layer-bearing write durable). The
layer-publish snapshot write must NOT be raced by box-stop. Two parts:
- A1: at the layer-publish site (`server.py:7205-7218`), AWAIT
  `_persist_case_view_snapshot` (and `_persist_case_manifest`) inline instead of
  detaching them. A layer publish is exactly the mutation whose cold-refresh
  must be durable; the original detach was a latency optimization for the
  per-turn / resume hot path, but the publish path already awaits
  `_persist_case_loaded_layers` immediately above, so adding the snapshot await
  there does not introduce a new class of blocking. (Per-turn and turn-close
  sites at `7167` and `8167` MAY stay fire-and-forget - they refresh chat, not
  the layer set - but should be drained by A2.)
- A2: drain `_BG_SNAPSHOT_TASKS` before the process can be stopped. Add a
  flush coupled to the busy gate so the autostop Lambda cannot stop the box
  while a snapshot PUT is outstanding: include `len(_BG_SNAPSHOT_TASKS) > 0` in
  `is_busy()` (`server.py:1267`) so `/api/health` reports `busy=true` until the
  detached writes land, AND add a FastAPI `lifespan`/shutdown drain
  (`await asyncio.gather(*_BG_SNAPSHOT_TASKS)` with a short timeout) so a
  graceful stop flushes. A1 alone fixes the publish race; A2 closes the
  per-turn/turn-close write race and any future fire-and-forget site.

FIX B (vectors carry cold-renderable DATA, cross-case included). Ensure every
vector layer summary in the snapshot carries `inline_geojson`, not just the
agent-only handle. The emitter already re-reads artifacts at write time
(`reinline_vector_layers` / `_read_vector_uri_as_geojson`); the gap is that the
cross-case branch (`server.py:7597-7602`) writes URI-only when
`target_case != open_case`. Resolve inline GeoJSON for the snapshot from the
persisted layer summaries' object-store URIs at write time regardless of which
case is open (read the `.fgb|.geojson` from S3 and embed), so a cross-case
mutation no longer strands vectors. This is the layer-handle->data-URI lesson:
persist the resolved DATA, not a handle the agent must later resolve.

FIX C (list-side fallback - defense in depth). Have `onCaseList`
(`web/src/hooks/useCases.ts`) surface `loaded_layer_summaries` so the map can
paint raster layers (which are already cold-renderable tile templates) from the
case-list payload when the per-case snapshot is 404/stale. This makes a missing
snapshot degrade to "rasters paint, vectors pending wake" instead of "no layers
loaded". Lower priority than A/B; it is a safety net, not the primary fix.

Why not the full `#165` rebuild: the `#165` thin manifest is already being
dual-written at the SAME call sites and via the SAME `_BG_SNAPSHOT_TASKS`
(`server.py:7214-7218`, `7405-7411`, `8174-8178`), and its
`_manifest_layer_from_summary` (`persistence.py:980-1032`) already projects a
cold-renderable `asset_url` per layer. But the manifest shares the identical
fire-and-forget durability bug and the web does not consume it. Switching the
cold path to the manifest does not fix the box-stop race - FIX A is required
either way. So the minimal path is A+B (+C), reusing the existing snapshot,
rather than a cold-path rewrite onto the manifest.

---

## 3. Ordered, file-disjoint fix jobs

J1 (agent: snapshot write durability) - `services/agent/src/grace2_agent/server.py`
  - Await the snapshot + manifest writes at the layer-publish site (7205-7218).
  - Add `len(_BG_SNAPSHOT_TASKS) > 0` to `is_busy()` (1267) and a lifespan
    shutdown drain (`asyncio.gather` with timeout) of `_BG_SNAPSHOT_TASKS`.
  - Owner: agent. Tests: `services/agent/tests/` new - assert the publish path
    awaits the snapshot and that `is_busy()` is true while a snapshot task is
    pending.

J2 (agent: cross-case vector inline) - `services/agent/src/grace2_agent/persistence.py`
  - In `build_case_view_snapshot` / `write_case_view_snapshot`, resolve inline
    GeoJSON for vector summaries from their persisted object-store URIs at write
    time (read `.fgb|.geojson` from S3), independent of which case is open, so a
    cross-case snapshot is no longer vector-URI-only.
  - Owner: agent. Tests: extend `services/agent/tests/` snapshot tests - assert a
    snapshot built for a NON-open case still carries `inline_geojson` for vectors.
  - File-disjoint from J1 (server.py vs persistence.py); the
    `server.py:7597-7602` open-case guard can stay (it becomes a fast path) so no
    server edit collides with J1.

J3 (web: list-side raster fallback) - `web/src/hooks/useCases.ts`
  - `onCaseList` surfaces `loaded_layer_summaries` to the map channel so raster
    layers paint from the list when the per-case snapshot is missing/stale.
  - Owner: web. Tests: `web/src/hooks/` or `web/src/*.test.tsx` - assert a
    case-list with raster summaries pushes a session-state frame the map paints.
  - File-disjoint from J1/J2 (web vs agent).

Ordering: J1 and J2 are independent agent files and can run in parallel. J3
(web) is independent of both. J1 is the load-bearing fix (it makes the layer
write durable); J2 closes the vector cross-case gap; J3 is defense in depth.

---

## 4. Deploy surface + clean-window constraint

Deploy surface:
- J1, J2 => AGENT box (`i-0251879a278df797f`). New build deployed via the
  SSM file-swap / container path on the agent box; restarting the agent process
  DROPS all live WebSockets. No Lambda or web change is required for J1/J2.
- J3 => WEB (S3 + CloudFront `E2L74AS56MVZ87`). Static build; an invalidation,
  no WS impact, deployable independently.
- No idle_check / view_sign / case_list Lambda change is required for the
  minimal fix (the case_list payload already carries `loaded_layer_summaries`;
  J3 is web-side consumption only).

Clean-window constraint: the agent restart for J1/J2 tears down every open WS,
so per the per-Case durability + continuous-deploy norms, deploy the AGENT
change BETWEEN NATE's live sessions (a clean window with no active turn/solve),
never mid-demo. After restart, verify deployed == HEAD on the box (commit/
env-flip is not deploy). The WEB change (J3) can ship continuously as it lands
green; it does not drop the socket. Standing say-so: deploy continuously as work
lands green, one permission ask at the END before the live agent-box mutation.

---

## 5. Acceptance

PRIMARY acceptance (the reported flow): agent box asleep + browser refresh shows
the Case WITH its layers (rasters painted; vectors painted from inline GeoJSON),
chat intact, no "no layers loaded".

Self-verifiable without driving the live agent (the decisive proof - snapshot is
durable + cold-renderable):
1. Warm: open a case, publish at least one raster AND one vector layer; let the
   turn return.
2. Force the durability race: immediately stop the agent box (simulate the
   autostop) - `aws ec2 stop-instances --instance-ids i-0251879a278df797f` (or
   send SIGTERM to the agent process if testing the lifespan drain locally).
   With FIX A, either `is_busy()` keeps `/api/health` `busy=true` until the PUT
   lands (the Lambda would not have stopped it), or the lifespan drain flushes
   on SIGTERM.
3. Fetch the SIGNED snapshot the SAME way the web cold path does: call the
   `view_sign` signer for the case (hop 1), then GET the pre-signed S3 URL (hop
   2) to download `case-views/{case_id}.json`.
4. Assert on the downloaded JSON body (NOT the live agent):
   - `session_state.loaded_layers` is NON-empty and contains the published
     layers.
   - every raster entry's `uri` is a resolved TiTiler `.../cog/tiles/.../{z}/{x}
     /{y}.png?url=...` template (cold-renderable via always-on CloudFront).
   - every vector entry carries non-empty `inline_geojson` (cold-renderable
     without the agent) - INCLUDING a vector published while a DIFFERENT case was
     open (FIX B regression: rename another case, re-fetch, assert vectors still
     inline).
   - `session_state.chat_history` is present (regression: chat still rides).
5. Cold-list fallback (FIX C): with the per-case snapshot deleted/absent, fetch
   the cold case-list and confirm the web maps `loaded_layer_summaries` so raster
   layers still paint (signer 404 degrades to "rasters paint" not "no layers").

Test-suite gates (no live turn needed): agent tests assert the publish path
awaits the snapshot write and `is_busy()` is true while a snapshot task is
pending (J1); a snapshot built for a non-open case carries vector
`inline_geojson` (J2); a raster-bearing case-list pushes a paintable
session-state frame (J3). Reviewers re-run these plus the signed-snapshot fetch
in step 3-4 rather than trusting the report.
