# Audit: M4 acceptance — Fort Myers demo end-to-end + sprint-06 close

**Job ID:** job-0036-testing-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** testing

**Prerequisites (ALL APPROVED — required):**
- job-0030 schema (PipelineStepSummary + AtomicToolMetadata)
- job-0031 infra (cache bucket + lifecycle rules)
- job-0032 agent (tool registry + cache shim + pass-through stubs)
- job-0033 engine (4 data-fetch tools + mongo_query DI binding)
- job-0034 engine (2 QGIS discovery tools + qgis_process DI binding)
- job-0035 agent (PipelineEmitter + real envelope emission — closes OQ-T-28-SIM-WS-BOUNDARY)
- Orchestrator hotfix commit `ca48256` (cache.py customTime datetime fix for OQ-33)

**Read before starting:** all 6 prior approved reports — focus on the OQs surfaced (especially OQ-33-CACHE-CUSTOMTIME-TYPE-BUG to write a regression test).

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — §3.9 FR-DC, FR-CE-8, FR-TA-2 tools surface
- `docs/srs/A-websocket-protocol.md` — envelope shapes for real emission verification
- `docs/srs/07-milestones.md` — M4 exit criteria

### Environment
The deployed substrate is operational end-to-end: Cloud Run QGIS Server (`@sha256:57d0f43`), PyQGIS worker (`@sha256:fffd7e0f`), cache bucket (`grace-2-hazard-prod-cache`), agent service runnable locally with all 8 tools registered. Tests run from Linux Debian dev host via Playwright + pytest.

### Scope

1. **Fort Myers demo — end-to-end live test.** Single integration test `tests/m4/test_fort_myers_demo.py::test_fort_myers_population_below_3m_elevation`:
   - User-equivalent query: "what's the population of Fort Myers below 3m elevation?"
   - Drive the agent service (real `services/agent/src/grace2_agent/server.py`, NOT a mock) via WebSocket using the `/invoke` debug directive job-0035 landed (OR via a full agent message if reachable in M4)
   - Tool chain: `geocode_location("Fort Myers, FL")` → bbox → `fetch_dem(bbox, 10)` → `fetch_population(bbox)` → `qgis_process('native:reclassifybytable', { mask < 3m })` → `qgis_process('native:zonalstatistics', mask × population)` → assert returned envelope carries a population total field
   - Verify the cache writes landed: each fetcher's GCS object at the expected `cache/<ttl-class>/<source-class>/<hash>.<ext>` path with `customTime` set (regression test for OQ-33)
   - Capture screenshot of the rendered map layer in the M3 web client (use the Playwright AFK loop) — proves the envelope-to-layer chain works end-to-end
   - Evidence: WS transcript + GCS object listings + final screenshot

2. **OQ-33-CACHE-CUSTOMTIME-TYPE-BUG regression test.** New unit test in `services/agent/tests/test_tools_cache.py` (NOT in tests/m4/) using a higher-fidelity GCS fake (or skipped-by-default integration test against real GCS):
   - Assert `blob.custom_time` is set to a `datetime` instance (not str)
   - Assert the SDK accepts the assignment without raising
   - Document the bug class: "type-fidelity of cache-side blob attributes"
   - Goal: prevent the FakeStorageClient-accepts-anything regression class

3. **M3 dev-injection seam → real-agent rewrite (closes OQ-T-28-SIM-WS-BOUNDARY).** Rewrite `tests/m3/playwright/test_pipeline_strip.py::test_pipeline_strip_sequence_with_framesent_capture` to drive the M3 PipelineStrip from the **real agent emission path** (job-0035) instead of `window.__grace2InjectPipelineState`. Keep the `state_colors` parametrized test as-is (it's a pure-rendering test of the 5 color states; doesn't need the agent).

4. **Full regression preservation.** `make test` + `make test-m2` + `make test-m3` + new `make test-m4` (add target) all green. Baseline counts after M4:
   - Contracts: 131/131
   - Agent service: 69/69 (post-hotfix)
   - M1 protocol/integration: 30 (29 + 1)
   - M2: 7
   - M3: 10
   - M4: ≥3 new tests (Fort Myers demo + OQ-33 regression + rewritten test_pipeline_strip)
   - Combined: ~250 invocations green

5. **Sprint-06 close.** Once all the above is green, populate the sprint-06 retrospective in `reports/sprints/sprint-06.md` (the orchestrator-direct sign-off after this audit) covering: M4 milestone achievement, the cost-discipline shift between sprint-05 and sprint-06 (with token telemetry from `reports/cost_tracking.json`), the OQ-33 lesson learned, OQs carried forward.

### File ownership (exclusive)

- `tests/m4/` (NEW directory: `__init__.py`, `conftest.py`, `test_fort_myers_demo.py`, `test_oq33_customtime_regression.py` OR integrated into existing test_tools_cache.py)
- `tests/m3/playwright/test_pipeline_strip.py` — ONLY the `_with_framesent_capture` rewrite; do not touch the `_state_colors` variant
- `services/agent/tests/test_tools_cache.py` — ONLY the new OQ-33 regression test
- `Makefile` — `test-m4` target add (mirror the `test-m3` pattern)
- `tests/pyproject.toml` — `live_m4` marker if needed
- `tests/m4/fixtures/` if any (likely: a pre-computed expected_bbox.json for Fort Myers)
- `reports/inflight/job-0036-testing-20260606/`

### FROZEN — no edits in this job

- `services/agent/**` other than the new regression test (specialists own their code)
- `packages/contracts/**`, `infra/**`, `web/src/**`, `services/workers/**`, `styles/**`
- `docs/srs/**`, `docs/SRS_v0.3.md` (housekeeping passes go to v0.3.16+)
- `reports/complete/**` (immutable)
- The other M3 tests (only `test_pipeline_strip._with_framesent_capture` may be rewritten; `_state_colors` stays)

### Cross-cutting principles in force

- **Invariant 1, 5, 8, 9:** preserve. M4 demo exercises Tier separation (Invariant 5 — fetched COGs land in the cache bucket, rendered tiles come from QGIS Server) and Cancellation chain (Invariant 8 — cancel mid-zonalstatistics test); both must be verified.
- **Replace-not-reconcile (A.7):** verified by the rewritten `test_pipeline_strip` against real emission (M3 + job-0035).
- **Diagnose before fix:** if the live demo fails, capture the failing tool's WS frames + GCS state + agent log before changing the test assertion.
- **Failure-naming discipline:** every assertion attributes failure to `web client | agent | tool registry | cache shim | QGIS Server | network | upstream API`.

### Acceptance criteria (reviewer re-runs)

- [ ] `tests/m4/test_fort_myers_demo.py::test_fort_myers_population_below_3m_elevation` PASSES end-to-end against the live substrate.
- [ ] All four cache writes verified at `cache/<ttl-class>/<source-class>/<hash>.<ext>` paths with `customTime` set as a datetime (not str) — OQ-33 regression.
- [ ] Rendered map layer screenshot captured + committed under evidence dir.
- [ ] OQ-33 regression test in `test_tools_cache.py` asserts datetime type-fidelity on `blob.custom_time`; fails the test if a string is assigned instead.
- [ ] Rewritten `test_pipeline_strip_sequence_with_framesent_capture` drives the real agent path (no `window.__grace2InjectPipelineState`); OQ-T-28-SIM-WS-BOUNDARY definitively closed.
- [ ] `make test-m4` target added; mirrors `make test-m3` opt-in pattern.
- [ ] Full regression: `make test` + `make test-m2` + `make test-m3` + `make test-m4` all green; baseline counts preserved.
- [ ] Sprint-06 retrospective populated in `reports/sprints/sprint-06.md`.
- [ ] PROJECT_LOG appended with sprint-06 close + cost telemetry summary.
- [ ] No edits to any FROZEN path.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: whether `test_fort_myers_demo` should run by default in `make test` or be opt-in via `live_m4` marker (TENTATIVE: opt-in — it touches live cache bucket + Nominatim + deployed Cloud Run worker; cost + rate-limit risk); how to handle Nominatim's "one request per second" policy in CI; whether to mock `qgis_process` or hit the local subprocess (TENTATIVE: local subprocess for M4 — production-substrate test lands when Cloud Run Jobs v2 submitter ships); whether the OQ-33 regression test should hit real GCS or a beefed-up fake (TENTATIVE: beefed-up fake that mirrors the real SDK's type-checking discipline; real GCS opt-in via `live_gcs` marker).
