# Live-feedback tracker (NATE driving, 2026-06-24 -> ) - single audit log

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
- [ ] mobile: DRAWN-AOI button renders OVER the settings button -> put it UNDER (currently inaccessible)
- [ ] tool cards do not appear until a refresh
- [ ] mobile REFRESH navigates to the cases LIST instead of staying in the current case
- [ ] cannot see the TOOLS CATALOG to test
- [ ] "sfincs solve" -> capitalize SFINCS; active solve needs NO dropdown
- [ ] python sandbox cards: fold inline after completion/approval; FAILED -> red; acceptance gates fold inline (desktop+mobile); folded reuse dims+accent + GLOW while running
- [ ] humanize "Layer statistics ready" -> "Code completed" for sandbox steps

### Rendering/data
- [ ] GOES not always displayed, esp. during ANIMATION runs (single-frame works)
- [ ] low output image resolution
- [ ] deleting one layer momentarily drops MANY others (refresh restores)
- [ ] true-color raster deletion deletes BOTH rasters (recurring)
- [ ] 3D terrain too aggressive -> extreme pixelation (~9 px)
- [ ] Sentinel-2 guardrail 0.5 deg^2 too strict for ~0.77 deg^2; offer a resolution gate; (deck.gl unify - spike DONE)
- [ ] building-footprint METADATA thinning: ID-only GeoJSON + fetch-on-click enrich (= #165 MVT data-island)

### Engine
- [ ] SFINCS sim computes full domain while AOI shrinks (display-only) - #183 (low-pri per NATE)

### Delegated -> tools session (tools-backlog/goes-tool-desc-feedback-2026-06-25.md)
- [ ] goes18 -> goes-18 identifier explicit in tool desc
- [ ] "Fetch Goes Archive Animation" label -> "Fetched GOES file"; capitalize GOES
- [ ] tool descriptions carry all selection info, no extraneous

## Rule reminders (in memory)
- Opus on Claude's subagents; Haiku = the in-site agent model; Claude does NOT drive the live prod agent (NATE verifies).
- Concise responses. Accumulate + wait for go; don't get carried away past the queue.
