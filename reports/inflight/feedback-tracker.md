# Live-feedback tracker (NATE driving, 2026-06-24 -> ) - single audit log

> DEPLOY 2026-06-26: web pushed origin/main 766157f (Vercel auto-deploy, all web fixes). Agent deployed via SSM bundle (sha256-verified swap over /opt/grace2, grace2-agent restarted, 140 tools, service active; box then stopped for clean baseline). server.py + GOES + Sentinel-2 fixes live on next wake. NOTE: post-restart health showed stale busy:true at 0 connections - a persisted marker a clean wake clears.

Status of every item from the live-testing feedback. DONE = committed + deployed (commit). QUEUED = not started/landed. Source detail: live-feedback-2026-06-25.md + live-feedback-2026-06-26-desktop-nova.md + tools-backlog/.

## DONE - deployed
### Agent (box, via SSM)
- [x] z_index stamping on layers (b673686)
- [x] cold box-off: snapshot-on-open + box-wake backfill (7080917 + cc7233c)
- [x] snap-to-AOI independent of geolocate (cc7233c)
- [x] satellite-animation fetch offloaded off the loop -> no connecting-loop (8e9c5c7)
- [x] runaway-agent guard: step cap + wall-clock + loop watchdog + stale-busy auto-clear (7e91b79); reconciled with circuit-breaker/loop-exhausted (3080f9c)
- [x] Bedrock "must start with user message" crash + GRACE2_LOG_LLM_INPUT preview (0a4667e)
- [x] deterministic raster AUTO-PUBLISH (layers render without the LLM calling publish_layer) + auto_publish flag (3080f9c)

### Web (Vercel)
- [x] memory crash, random layer reorder, spurious autoplay, cold-raster client gates (c2303bc)
- [x] 3D bbox stable (no size pulse) + grid-only loading, zero-layers-gated (fd2f4f1); ping-pong grid (c1912bd)
- [x] mobile: bbox clears on case-exit; scrubber/legend dock to chat-panel top (18cc0da); snap-AOI-above-sheet + legend window-clamp (d17bfef)
- [x] mobile: scrubber bbox-snap vs dock-when-shrunk rule; hide-animation STOPS frames; frames stop escaping into the layer list (cc7233c)
- [x] desktop: chat panel bottom lifted + aligned w/ settings button; history padding so nothing hides behind composer (a90b2aa)
- [x] building VERTICES outline-only (no dots/circles) - for footprints routed through the deck.gl spike (58762ac)
- [x] deck.gl interleaved-overlay spike: footprints on GPU, lazy-loaded (58762ac)

### Scrubber/overlays (NATE direction CHANGED -> static) - LANDED 1bd4024
- [x] **STATIC scrubber pinned at the bottom of the screen** (supersedes the snap/dock saga) - dropped AOI-snap/dock/width-tracks-bbox; only stable desktop gutter-centering remains (1bd4024)
- [x] hiding an animation layer ALSO hides the scrubber (controller setGroupHidden re-points active group -> scrubber unmounts; verified)
- [x] autoplay: scrubber HANDLE now tracks the live frame index (slider value <- controller.frameIndexFor via useAnimationState re-render) (1bd4024)
- [x] scrubber<->chat padding subsumed by the static bottom placement (mobile safe-area+clearance offset; desktop gutter)

## QUEUED - not landed
### Web UI
### Web UI - LANDED (queue-implement workflow, 2026-06-26)
- [x] mobile: DRAWN-AOI button now drops BELOW the settings gear (top 64); Settings tappable (371caa3)
- [x] tool cards appear LIVE (no refresh): Chat ROOT->case stream self-heal (adoptRootInto) + agent always-emit case-open (2bff862 + 9bfd409)
- [x] mobile REFRESH stays in the open case: localStorage grace2.activeCaseId persist + restore-on-mount (371caa3)
- [x] TOOLS CATALOG no longer hangs box-off: 10s timeout + honest "agent may be asleep" error (371caa3)
- [x] "sfincs solve" -> "SFINCS solve" (all solvers); active solve card dropdown removed (2bff862)
- [x] python sandbox cards: fold-after-resolve + FAILED red + status accent + glow-while-running (371caa3)
- [x] "Layer statistics ready" -> "Code completed" (2bff862)

### Rendering/data - LANDED (queue-implement workflow, 2026-06-26)
- [x] GOES animation frames now render: composer emits each published frame to loaded_layers (honesty floor) (c6cbfb1)
- [x] deleting one layer no longer drops many: re-inline surviving vectors before the delete echo (9bfd409)
- [x] true-color deletion no longer deletes both: _restamp mints unique ids for list-returning tools (9bfd409)
- [x] 3D terrain softened (exaggeration 2.0->1.4) + linear resampling in 3D (kills ~9px pixelation) (f2f472a)
- [x] Sentinel-2 guardrail 0.5->1.0 deg^2 + honest auto-coarsening (c6cbfb1)
- [x] low output image resolution -> option (b) DONE (d929430, deployed): user-resolution gate extended to fetch_dem [1,3,10,30]m + fetch_topobathy [3,10,30]m via the #154 ResolutionPickerCard (opt-in finer, finest-allowed-by-area cap; NAIP/NDVI/GOES source-capped left as-is; SFINCS/SWMM solver defaults confirmed not coarse + super-linear -> no floor raise).
- [x] building-footprint METADATA thinning: ID-only inline GeoJSON (osm_id/osm_type/fid) + /api/building-detail click-to-enrich (sidecar -> Overpass-by-id fallback); MVT geometry tiling DEFERRED as the #165 follow-up (0d6a35a + 7e3aa7f, deployed web+agent 2026-06-27)

### Engine
- [x] SFINCS compute-domain #183 (NATE direction: compute ONLY within bbox, NO COG clip): engine already builds the grid from the AOI with no padding (locked by a guard + test); closed the residual #159 server boundary -- expensive AREAL solvers now snap a drifted/wider re-entry solve DOWN to the active AOI (_maybe_default_solver_bbox_to_pinned_aoi, gated to flood-depth/swmm-depth so MODFLOW plume solvers are untouched). #194 archetypes byte-identical (f4e0f9f + 7e3aa7f, deployed)

### Delegated -> tools session (tools-backlog/goes-tool-desc-feedback-2026-06-25.md)
- [x] goes18 -> goes-18 identifier explicit in tool desc (landed earlier in the fetcher pass d45d841: hyphenated enums + _normalize_satellite coercer)
- [x] "Fetch Goes Archive Animation" label -> honest GOES/GLM-cased card labels (3 HUMANIZED_STEP_NAMES entries in PipelineCard.tsx; archive label says "frames") (2d82910, deployed Vercel)
- [x] tool descriptions carry all selection info, no extraneous (GOES family already satisfies this per the scope)

## Rule reminders (in memory)
- Opus on Claude's subagents; Haiku = the in-site agent model; Claude does NOT drive the live prod agent (NATE verifies).
- Concise responses. Accumulate + wait for go; don't get carried away past the queue.

## QUEUED - NATE UI notes 2026-07-19 (local QGIS plugin chat, next UI batch)
- [ ] SimCard foldable ANY time (today it only collapses at terminal state) - user-driven fold/unfold while running
- [ ] Nested tool calls as a DIRECTORY-TREE layout: parent tool on its own line, children indented with a tree connector (run_model / |-> fetch_dem style, nicer arrow ok) - replace the chip "circles" which read cluttered; KEEP the accent color
- [ ] Tool chip color = STATE: green success / grey in-progress / red failed (not a fixed blue)
- [ ] Collapsed SimCard shows PROGRESS on the right, next to the collapse toggle (pct/elapsed at a glance)
- [ ] Sim/gate card ORDERING bug: card sits at the BOTTOM while the streaming text fills ABOVE it - card must land inline chronologically (same class as the BUG-4 GateCard fix: close the pending entry when the card inserts)
- [ ] Cards get a FILL (subtle background), not outline-only
