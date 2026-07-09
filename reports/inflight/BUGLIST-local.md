# TRID3NT Local - living bug list
Updated: 2026-07-09 (Claude maintains; statuses: FIXED-VERIFIED / FIXED-UNVERIFIED / IN-PROGRESS / OPEN / WONTFIX)

| id | item | status | evidence |
|----|------|--------|----------|
| F1 | No mid-turn visibility (no cards/narration during turn) | FIXED-VERIFIED | Playwright 2026-07-09: thinking els at T+15s, cards 1->7 during turn; batch E2E C1 4 tool/running els mid-turn |
| F2 | Model selector generic / no hot-swap | FIXED-UNVERIFIED | /api/local-models + selector committed 8deff81; not yet driven live |
| F3 | Layer stuck in loading loop | FIXED-VERIFIED | = unanswered payload gate; batch E2E C5: after gate click layer row appears, loading-els=0 |
| F4 | Landcover invisible | FIXED-VERIFIED | batch E2E: landcover prompt -> gate -> layer row rendered, no stuck loading |
| F5 | Auth surfaces in local build | FIXED-VERIFIED | Playwright: no gate, straight to app; Settings account section hidden |
| F6 | Gates time out locally | FIXED-VERIFIED | batch E2E C4: gate clicked at ~50s and still live (cloud would have expired); no-timeout 8deff81 |
| F8 | Thinking tokens stream (web, grey foldable, toggle) | FIXED-VERIFIED | e2e_thinking_stream.mjs 3/3 PASS: block streams (120 chars), answer follows, toggle collapses (visible true->false). Root cause found+fixed 2026-07-09: server.py never forwarded ThinkingDeltaEvent / never read payload show_thinking (F8 agents were cut by limits mid-work); wired dispatch loop -> agent-thinking-chunk. Proof docs/proof/44 |
| F9 | Thinking stream in QGIS dock | FIXED-VERIFIED | headless_thinking_proof.py vs LIVE agent: thinking=True answer=True at 18s, collapse works. Proof docs/proof/45 |
| F10 | No activity signifier between cards (running-tool spinner) | FIXED-VERIFIED | batch: running-tool card at dispatch start (pipeline-state running already fires; gap was unwired onAgentThinkingChunk + card); E2E C1 running els mid-turn |
| F11 | Payload gate missable (auto-scroll/pulse/mobile visibility + coarser option) | FIXED-VERIFIED | batch E2E C2 gate isVisible after force-scroll, C3 amber pulse border present |
| F12 | Client WS drop killed turn + misreported LLM_UNAVAILABLE | FIXED-UNVERIFIED | c93a809, 268 tests; live phone-drop scenario not re-tested |
| F13 | Cases ephemeral per-device | FIXED-VERIFIED | batch E2E C6: second browser context sees the shared case list (single local user + adoption c93a809) |
| F14 | LAN/Tailscale access | FIXED-VERIFIED | http://100.92.163.46:5173 -> 200; all services bound; NATE reached it on phone |
| F15 | Mobile: spatial-input banner/toolbar/slider overlap (NATE live report 2026-07-09) | FIXED-VERIFIED | flex top-stack on mobile (overlap impossible by construction, structural test); desktop byte-identical, verified no overlap |
| F16 | Landcover over a state fails (guardrail dead-end + purpose='aoi' rejected + server rewrote purposes to 'barrier') | FIXED-VERIFIED | live E2E post-restart: WA prompt -> resolution card -> Proceed -> landcover layer renders, loading resolves. NLCD auto-coarsen (30-600m ladder, 4000px budget) + esri 10m tiled to 8 deg2 + aoi purpose end-to-end |
| OPEN-1 | Esri landcover fetch is 107MB (needs default coarsening/COG windowing) | FIXED | superseded by F16: NLCD auto-coarsen + esri tile/mosaic with honest payload estimate |
| OPEN-2 | Styled exports: 0-transparency only when ramp vmin==0 exactly | OPEN | noted in styled-export lane |
| OPEN-3 | QGIS remote mode: exports do not download GeoTIFFs | OPEN | pre-existing, noted |
| OPEN-4 | 22 unusable tools list (post-remediation re-measure pending) | OPEN | usability re-run not yet scheduled |
| OPEN-5 | Local model first token can take ~75s cold (qwen3:8b context load); no UX regression (thinking block covers it) but worth an Ollama keep-alive | OPEN | observed in e2e_thinking_stream (block at T+76s cold vs 18s warm in QGIS proof) |
| OPEN-6 | Old flood case replay: depth layers absent + tile 404s on the pre-existing Chattanooga case (stale MinIO artifacts from old runs suspected, possibly pruned) | OPEN | e2e_flood_proof 2026-07-09: first run has_depth_layers=false; SECOND run (post landcover-fix restart) has_depth_layers=true + animation group - may be intermittent or case-pick dependent, downgraded to flaky |

| OPEN-7 | Test-suite hygiene: test_active_aoi_repair_job2::test_bare_followup_refetch_short_circuits (5-min solver-confirm gate timeout for fetch_dem, then FAIL - fetch_dem swept into a confirm gate the test never answers) + test_modflow_archetype_offload.py collection error (imports 'services', needs repo-root run; from offload commit 46b08c7) | OPEN | both PRE-EXISTING: reproduced identically on pre-change commit d59b402 in a clean worktree |

Notes 2026-07-09 (AFK batch close-out):
- F8 was the real find of the night: the 2026-07-08 F8 commit shipped adapter+contract+UI but the server dispatch loop hooks were missing (both building agents were cut by session limits mid-work and the note said UNVERIFIED). Symptom was thinking-els=0 in the batch E2E; fixed in server.py (payload show_thinking -> stream_events_with_contents; ThinkingDeltaEvent -> agent-thinking-chunk, gated on the per-turn toggle, bubble-id shared with the answer).
- Verification drivers kept: web/tools/e2e_thinking_stream.mjs, web/tools/e2e_f9f10_gate_thinking.mjs, qgis-plugin/tests/headless_thinking_proof.py.
