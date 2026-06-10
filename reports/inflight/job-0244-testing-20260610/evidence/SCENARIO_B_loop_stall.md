# Scenario B — flood+Pelicun BLOCKED: agent loop stalls on degraded flood envelope

## What happened (live, B+C harness, session 01KTRMGNNFHT2GD042DVP6ZDC6)
1. B1 prompt: "Model flood damage for Fort Myers... run a flood scenario... then run a Pelicun damage assessment."
2. Gemini called `run_model_flood_scenario` (Fort Myers geocoded, bbox emitted, DEM/landcover/river cache hits).
3. SFINCS deck-build read landcover via GDAL `/vsigs/`:
   `rasterio.open('/vsigs/grace-2-hazard-prod-cache/.../landcover/7dac3520...tif')` ->
   `InvalidCredentials: No valid GCS credentials found ... set GOOGLE_APPLICATION_CREDENTIALS ...`
   => `build_sfincs_model raised LANDCOVER_READ_FAILED — returning failed envelope`.
4. `function-response queued ... iter=1 tool=run_model_flood_scenario summary_keys=['result','status','tool']`.
5. **Then NOTHING.** No iter=2 generate call (no httpx POST to aiplatform), no narration, no terminal.
   Agent PID 3036624 at 0.0% CPU, sleeping, log frozen >210s. The loop did not advance past the
   degraded flood envelope. The harness saw sig=false / quietMs climbing past 215s and never got an
   ImpactPanel. B+C harness killed to avoid burning the 900s timeout on a dead session.

## Root cause (two stacked env/loop issues)
- ENV: GDAL `/vsigs/` cannot authenticate to the private cache bucket because `GOOGLE_APPLICATION_CREDENTIALS`
  is unset for the agent (ADC works for the google-cloud-storage Python client — that is why MODFLOW's
  COG upload + read succeeded — but GDAL's vsigs driver needs the env var / CPL_GS / .boto). So a FRESH
  flood scenario cannot build its SFINCS deck here. No flood-depth layer is produced -> Pelicun has nothing
  to assess -> no ImpactPanel.
- LOOP (new, secondary): after queueing the failed flood function-response, the agent loop did not issue
  the next Gemini generate call (iter=2). The expected behavior (job-0177 tool-retry / always-narrate) is to
  feed the error back so Gemini retries or narrates honestly; instead the loop went idle. Whether this is a
  hang or a swallowed exception needs an agent-specialist look — but it is downstream of the env gap and
  NOT the job-0243 confirmation fix.

## Impact on Scenario B/C verdicts
- p5_impact: BLOCKED (no flood layer -> no Pelicun -> no ImpactPanel; loop stalled).
- analysis_count / chart_emission / chart_replay: BLOCKED (depend on B1 producing an impact result).
- sandbox_gate_live: BLOCKED (same session dead; and the cloud sandbox path would hit the same run_v2 gap).

These are environment + a secondary loop-resilience issue — none is a regression in the job-0243 fix,
which Scenario A proved end-to-end (tool-payload-confirmation accepted -> mf6 solve -> plume COG in GCS).
