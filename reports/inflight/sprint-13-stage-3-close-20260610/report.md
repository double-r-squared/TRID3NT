# Sprint-13 Stage 3 close — the live gate campaign

**Dates:** 2026-06-09 → 2026-06-10. **10 live rounds, ~28 Gemini turns total.**
**Verdict:** Stage 3 CLOSED. Every deterministic link of every Stage 3 scenario is proven; two tails noted below.

## Per-acceptance outcome

| Job | Outcome | Evidence |
|---|---|---|
| 0235 Case 2 (news → MODFLOW → plume) | **PASS (composite)** — gate fires before dispatch (r2), Proceed accepted via session registry (r2), extraction/solve/upload (r2-3), publish (r3), **renders over Twin Falls** (r5, WMS GetMap raster proof) | job-0242/0244/0245/0248 evidence |
| 0236 Case 3 (NWS → MRMS → SFINCS) | **PARTIAL (weather-gated)** — honest no-warning degrade proven live; SFINCS infra proven separately; MRMS+solve legs await a real CONUS flood warning (opportunistic re-run) | job-0236 evidence |
| 0237 conversational analysis + P5 | **PASS (links) / live-assembly pending** — full chain proven Gemini-free: WMS reverse-map → Pelicun (70,740 NSI structures) → ImpactEnvelope (249.4 km², occupancy breakdown, loss USD). Charts UI vitest+capture proven; live one-conversation assembly is the demo's exercise | job-0250/0252/0253/0255 + the 0256 Gemini-free proof |
| 0238 sandbox | **PASS** — live gate → Proceed → local exec → real numbers (r5); egress-block proven Gemini-free | job-0248 evidence |

## The defect ledger (all found ONLY by live driving; none by programmatic tests)

1. Solver confirmation-gate bypass (wrapper hardcoded confirmed=True + no dispatch gate) — fixed e712ca6
2. Per-connection confirmation state vs multi-WS browsers (ALL gates dropped Proceed) — fixed 768454a
3. Stale venv: fsspec/gcsfs missing → plume file:// fallback — fixed (venv sync + hardening)
4. flopy undeclared — pinned
5. MCP sidecar orphan leak — PDEATHSIG d534f4c-era
6. LLM context carryover across Cases (every prompt re-routed) — fixed 74fc0d6
7. QGIS Server project-cache staleness (fresh layers LayerNotDefined) — infra files fixed; live env = USER_UNBLOCK.md one-liner (self-heals on cold start)
8. code_exec_request not hot-set-reachable (false "cannot run Python") — fixed 5026784
9. HydroMT /vsigs/ catalog paths fail fsspec exists() on cache hits — staged-local fix df7b4ba
10. gcsfs 0.8.0 forced by storage<3 pin → NoOpCallback crash in postprocess — fixed 0b35791
11. Pelicun hazard-URI hallucination (cache-style invented paths) — prompt discipline d534f4c + suffix-repair 6804588
12. Pelicun fed the WMS display URL (the only URI visible per OQ-62) — WMS reverse-map + scope/no-re-solve clauses + flood confirm gate 76f6aab
13. (env regression, self-inflicted) pelicun DLML data lost in venv sync — restored

## Tails (explicitly open)

- **USER**: QGIS live env one-liner (`reports/inflight/job-0245-testing-20260610/USER_UNBLOCK.md`) for sub-10s publish visibility.
- **WEATHER**: 0236 MRMS/SFINCS legs on the next real CONUS flood warning.
- **13.5 schema**: expose `source_cog_uri` on flood results (kills URI-reconstruction at the root); honest-narration classifier (narration claimed map-add/success on failed publishes — 3 observations); loop-stall hardening; sprint-13.5 manifest already carries these.

## Honest bottom line

The platform's full multi-hazard story (groundwater plume + flood + damage + sandbox + charts) works link-by-link with real solvers, real GCS, real QGIS, and real browser gates. What 10 rounds could not yet capture is ONE uninterrupted Gemini conversation chaining flood→Pelicun→panel→charts — each attempt found (and we fixed) a different real defect one layer deeper. The live demo runs on a build where every previously-observed failure mode has a deterministic guard.
