# job-0263 — Fable-5 adversarial verify (correctness lens, refute-by-default)

Verifier: Fable-5, 2026-06-10. NO Gemini, NO Playwright. All commands run from
`/home/nate/Documents/GRACE-2/services/agent` with `.venv/bin/pytest`.

## Re-derived claims (all independently re-run)

| Claim | Result |
|---|---|
| `tests/test_uri_registry.py` 29/29 | REPRODUCED: `29 passed in 36.37s` |
| Seam-adjacent batch 152 passed / 1 skipped | REPRODUCED: `152 passed, 1 skipped` (12 files incl. payload-warning, publish_layer, system-prompt, multi-turn, tool-retry, pelicun, analytical QA, solver-confirm, tool-not-found, impact-envelope, case-layer write path, case-context reset) |
| I1 mangle verbatim | CONFIRMED at `reports/inflight/job-0253-testing-20260610/evidence/agent_restart_0253.log:475` (`runs/runs/01KTS5W9…` + 404 at :478) |
| I2 layer_id-as-basename | CONFIRMED same log line 475 (`usace-nsi--81.9126….fgb`); real `852a6cc379b18c865bf9d99ec1acaa35.fgb` at log:218 + ws_frames.json:225 |
| I3 hash-tail x3 | CONFIRMED at `reports/inflight/job-0257-engine-20260610/report.md:19-21` (all three real/mangled pairs match test constants) |
| I4 WMS-as-hazard / I5 invented hash | CONFIRMED at `reports/inflight/job-0255-testing-20260610/evidence/agent_log_p5_turn.txt:170` (exact args dict) |
| Wiring real | CONFIRMED: `server.py:2729-2730` (resolve), `:2738/2746` (activate/deactivate), `:2769` (register), `:1130-1137` (layer_handles surfacing); `publish_layer.py:869` step-8 hook post-validation; `adapter.py:274-294` SYSTEM_PROMPT contract; `_sync_case_context` seeding at `server.py:1493` |
| Typed-error contract | CONFIRMED: `adapter._classify_error` harvests `error_code`/`retryable` class attrs (adapter.py:951-952); seam test renders `{status:error, error_code:URI_HANDLE_UNRESOLVED, retryable:true}` |
| Full sweep 4275 passed / 5 pre-existing failures | see addendum at bottom |

## Break attempts — `fable5_adversarial_break_tests.py` (this dir)

`19 passed, 2 xfailed` — xfail(strict) = reproducible confirmed breaks.

### HELD (could not break)
- 11-char hash prefix + ambiguous dir → refuses (typed error), never guesses
- exactly-12-char prefix → matches (by design); two candidates tied ≥12 → refuses
- hash-prefix branch requires same extension
- invented ULID with TWO runs registered → segment-overlap tie → refuses
- WMS URL for unregistered layer → typed error (not silent pass)
- handle re-registration: latest wins, displaced URI still passes verbatim
- minted bare-URI handle collision (two runs' `flood_depth_peak.tif`): exact
  resolution of BOTH COGs still passes verbatim
- cross-session: other session's handle NOT substituted; other session's exact
  managed URI rejected when no same-basename candidate exists
- ContextVar isolation across concurrent asyncio tasks: each task's
  `observe_published_layer` lands in its own session registry only
- store eviction (4096 sessions): evicted session comes back EMPTY, not stale
- per-session record eviction (1024): `_uri_to_handle` stays consistent
- garbage values (`gs://`, `gs:///`, NUL bytes, bare bucket) never crash

### BROKE (confirmed flaws)

1. **Foreign-bucket hijack — contract violation (the report's "foreign-bucket
   URIs fail open" claim is FALSE under collision).**
   `_resolve_one` runs `_fuzzy_match` (branch 3) BEFORE the
   `_in_managed_bucket` gate (uri_registry.py:504-512), so fuzzy substitution
   applies to foreign buckets too:
   - `gs://my-own-bucket/myproject/flood_depth_peak.tif` (user-supplied) with
     one flood run registered → silently rewritten to the session's run COG
     (basename sub-branch b).
   - `gs://my-own-bucket/dem/090a4ff8d9a083deadbeef….tif` → hijacked to the
     registered hillshade via the ≥12-char prefix sub-branch (c).
   Reachable today: a user can paste a gs:// URI in chat and Gemini passes it
   through. Wrong data, success narration. **Fix is a one-liner: gate branch 3
   on `_in_managed_bucket(v)` — all five incident URIs live in managed
   buckets, so no incident coverage is lost.**

2. **Wrong-run silent substitution inside the managed bucket (design hazard,
   documented as passing test).** With only run A registered, run B's REAL
   exact URI (cross-session paste / seeding gap) is silently rewritten to
   run A via unique-basename matching — all SFINCS runs share the basename
   `flood_depth_peak.tif`. The design accepts this ("unregistered managed URI
   = invented or stale"); flagging because the substitution is silent
   (server-side WARNING only) and wrong-run results would narrate as success.

3. **Same-dir sub-branch (d) is extension-blind (minor).** Invented
   `…/usace_nsi/whatever.tif` in a dir whose only registered object is a
   `.fgb` hands the vector to a raster param. Suggest requiring extension
   match in (d).

4. **Descriptive-stem ≥12-prefix false positive (theoretical today).**
   `admin-boundaries-fl-county.fgb` → substituted to
   `…-fl-state.fgb` (20-char shared prefix). Low live risk: cache basenames
   are sha256 hex (`cache.py:160`) and run outputs are fixed names, but any
   future tool writing descriptive basenames into managed buckets arms this.

## Verdict

CONFIRM (severity of worst finding: major, with a one-line fix). The five
incidents are genuinely replayed with verbatim logged values, all four
branches behave as claimed, isolation holds incl. concurrent ContextVar use,
the seam wiring is real, and the regression batches reproduce. The
foreign-bucket fail-open contract is violated under basename/stem collision
and should be fixed in a follow-up (gate `_fuzzy_match` on
`_in_managed_bucket`), plus consider extension-matching in sub-branch (d).

## Addendum — full sweep

REPRODUCED EXACTLY: `5 failed, 4275 passed, 72 skipped, 1 xfailed in 346.33s`
with the SAME five failures the report names. Failure modes inspected and
confirmed NOT owned by this job:

- 3x `test_data_fetch.py` — `assert "Access pattern:" in doc` fails; caused by
  a concurrent job's in-flight docstring rewrite of `data_fetch.py` (dirty in
  worktree; not in job-0263's file list).
- 2x `test_model_flood_scenario.py` — job-0257's live-GCS
  `_validate_and_correct_layer_uri` gate 404s the test's random run_id against
  the REAL runs bucket (`LAYER_URI_NOT_FOUND … does not exist in GCS`), so
  publish falls back to gs:// and the WMS-URL assertion fails. No
  UriResolutionError anywhere in the trace — the registry is uninvolved
  (direct programmatic call; the observation hook is a no-op outside dispatch).
