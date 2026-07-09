# TRID3NT Local - living bug list
Updated: 2026-07-09 (Claude maintains; statuses: FIXED-VERIFIED / FIXED-UNVERIFIED / IN-PROGRESS / OPEN / WONTFIX)

| id | item | status | evidence |
|----|------|--------|----------|
| F1 | No mid-turn visibility (no cards/narration during turn) | FIXED-VERIFIED | Playwright 2026-07-09: thinking els at T+15s, cards 1->7 during turn |
| F2 | Model selector generic / no hot-swap | FIXED-UNVERIFIED | /api/local-models + selector committed 8deff81; not yet driven live |
| F3 | Layer stuck in loading loop | ROOT-CAUSED | = unanswered payload gate (107MB esri fetch) + no timeout; gate-UX fix in flight |
| F4 | Landcover invisible | ROOT-CAUSED | fetch never ran (blocked on same gate); E2E-with-gate-click in flight |
| F5 | Auth surfaces in local build | FIXED-VERIFIED | Playwright: no gate, straight to app; Settings account section hidden |
| F6 | Gates time out locally | FIXED-UNVERIFIED | no-timeout committed 8deff81; side effect = F3 waits forever (see gate UX) |
| F8 | Thinking tokens stream (web, grey foldable, toggle) | FIXED-UNVERIFIED | plumbing committed 8deff81; thinking ELEMENTS seen at T+15 but full stream + toggle unverified |
| F9 | Thinking stream in QGIS dock | IN-PROGRESS | Sonnet batch a31f9569 |
| F10 | No activity signifier between cards (running-tool spinner) | IN-PROGRESS | Sonnet batch a31f9569 |
| F11 | Payload gate missable (auto-scroll/pulse/mobile visibility + coarser option) | IN-PROGRESS | Sonnet batch a31f9569 |
| F12 | Client WS drop killed turn + misreported LLM_UNAVAILABLE | FIXED-UNVERIFIED | c93a809, 268 tests; live phone-drop scenario not re-tested |
| F13 | Cases ephemeral per-device | FIXED-UNVERIFIED | single local user + adoption c93a809; E2E second-context check in flight |
| F14 | LAN/Tailscale access | FIXED-VERIFIED | http://100.92.163.46:5173 -> 200; all services bound; NATE reached it on phone |
| OPEN-1 | Esri landcover fetch is 107MB (needs default coarsening/COG windowing) | OPEN | gate makes it survivable; real fix = smarter fetch |
| OPEN-2 | Styled exports: 0-transparency only when ramp vmin==0 exactly | OPEN | noted in styled-export lane |
| OPEN-3 | QGIS remote mode: exports do not download GeoTIFFs | OPEN | pre-existing, noted |
| OPEN-4 | 22 unusable tools list (post-remediation re-measure pending) | OPEN | usability re-run not yet scheduled |
