# Audit: regenerate flood COG with 0071 fixes baked in + publish + headline re-screenshot (the visible-corrections verification)

**Job ID:** job-0075-engine-20260607, **Sprint:** sprint-10 (Stage 2 follow-up; the visible-corrections verification), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites (ALL APPROVED):**
- **job-0071 (commits 0cbfa43 + 2eb98bc):** postprocess_flood rotation fix (dim-name inspection + transpose) + transparency belt+suspenders (NODATA_DEPTH_M=0.05) + QML lowest-stop alpha=0 + publish_layer auto-dispatch overrides kwarg fix
- **job-0074 (commit 344de30):** worker rebuilt with new QML + DOUBLE-MNT + EPSG:4326 fixes baked into the deployed image; App.tsx production routing complete

**SRS references:** none beyond what's in force.

**Required reads:**
- `reports/complete/job-0070-engine-20260607/report.md` + `evidence/smoke_demo.py` — the smoke-harness pattern you'll re-run
- `reports/complete/job-0074-engine-20260607/evidence/screenshot_driver.py` — the Playwright dev-injection pattern
- `reports/complete/job-0071-engine-20260607/report.md` — what the visible corrections should look like

### Why this job exists

User direction 2026-06-07 after seeing job-0074's screenshot: "I need to confirm it works before we move forward." Job-0074 re-used job-0070's pre-fix COG to save M5 runtime, so structural improvements were live but the *visible* rotation + transparency corrections weren't demonstrated. This job regenerates the COG end-to-end so the user can see the visible improvements before sprint-10 closes.

It's also the first end-to-end test of the publish_layer auto-dispatch fix from job-0071 (job-0074 used manual `gcloud run jobs execute`; this job exercises the agent-side auto-dispatch).

### Scope — 3 parts in one commit

#### Part 1 — Re-run M5 smoke harness end-to-end

Copy `reports/complete/job-0070-engine-20260607/evidence/smoke_demo.py` to `reports/inflight/job-0075-engine-20260607/evidence/smoke_demo.py` and run:

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json \
  CPL_GS_USE_GOOGLE_AUTH=YES \
  PYTHONPATH=services/agent/src:packages/contracts/src \
  .venv-agent/bin/python reports/inflight/job-0075-engine-20260607/evidence/smoke_demo.py
```

Capture stdout to `evidence/smoke_demo_log.txt`, envelope to `evidence/smoke_demo_envelope.json`. Expected: another reproducible SUCCESS — new run_id, new COG GCS URI.

**Verify the new COG has the 0071 fixes applied:**
```
.venv-agent/bin/python -c "
import rasterio, numpy as np
src = rasterio.open('gs://grace-2-hazard-prod-runs/<NEW_RUN_ID>/flood_depth_peak.tif')
print('CRS:', src.crs, 'bounds:', src.bounds)
print('shape:', src.shape, 'nodata:', src.nodata)
arr = src.read(1)
nan_count = np.isnan(arr).sum()
total = arr.size
print(f'NaN cells: {nan_count}/{total} ({100*nan_count/total:.1f}%)')
# The 0071 transparency fix should make many low-depth cells NaN; expect significantly more NaN cells than the job-0070 COG had
"
```

Compare NaN count to job-0070's: job-0070 had `arr > 0.0` so only literally-zero cells were NaN (probably ~0% of the grid). Job-0071's `arr > 0.05` should make many more cells NaN (anywhere depth < 5cm). Capture the NaN ratio in the report.

**Verify rotation is corrected** — if the COG bounds + shape make geometric sense (Fort Myers area, ~16km × 16km, 527×540 cells ≈ 30m resolution), and the CRS is EPSG:32617, the geometry is right. Optional: open both the job-0070 COG and the new one in rasterio side-by-side and compare orientations of a known feature (e.g., the Caloosahatchee River should run roughly E-W).

#### Part 2 — Publish-raster via auto-dispatch (first live test of 0071's fix)

DO NOT use manual `gcloud run jobs execute` this time. Drive the publish through the agent's `publish_layer` atomic tool to exercise the auto-dispatch path that job-0071 fixed:

```
.venv-agent/bin/python -c "
from grace2_agent.tools.publish_layer import publish_layer
wms_url = publish_layer(
    layer_uri='gs://grace-2-hazard-prod-runs/<NEW_RUN_ID>/flood_depth_peak.tif',
    layer_id='flood-depth-job-0075-demo',
    style_preset='continuous_flood_depth',
)
print('WMS URL:', wms_url)
" 2>&1 | tee evidence/publish_layer_auto_dispatch.log
```

(Adjust to the actual `publish_layer` signature in `services/agent/src/grace2_agent/tools/publish_layer.py`.)

Expected: returns WMS URL successfully (not the previous `JobsClient.run_job() got unexpected keyword argument 'overrides'` failure). Capture the auto-dispatch log.

If auto-dispatch fails for a NEW reason, honestly disclose as OQ-75-* and fall back to manual `gcloud run jobs execute` to unblock Part 3 (still useful to capture the live screenshot).

#### Part 3 — Playwright headline re-screenshot

Mirror job-0074's screenshot pattern (look at `reports/complete/job-0074-engine-20260607/evidence/screenshot_driver.py`). Update the injected session-state with the new WMS URL + run_id's bbox. Drive zoom-to. Wait 5 seconds. Screenshot.

Save to `evidence/headline_fort_myers_VISIBLE_CORRECTIONS.png` — the user-facing deliverable.

**What this screenshot should show that job-0074's didn't:**
- (a) Flood layer correctly oriented (no 90° rotation; rivers running E-W look E-W)
- (b) Dry land showing the basemap unobstructed (no faint blue tint over the whole bbox)
- (c) Only actually-flooded areas appearing as the blue overlay

In the report, narrate the visible difference vs job-0074's screenshot — what looks different. If the corrections don't visibly appear in the screenshot (e.g., still rotated, still tinted), diagnose why and surface as OQ-75-*. Don't claim corrections that aren't visible.

### File ownership (exclusive)

- `reports/inflight/job-0075-engine-20260607/`

### FROZEN

- ALL source files. This is a pure regeneration + verification job; no code changes. (Sprint-10's code-level changes all landed in 0071/0072/0074; this job just produces a fresh COG and re-screenshots.)
- `reports/complete/**`

### Acceptance criteria

- [ ] Fresh M5 smoke run → AssessmentEnvelope.outcome=SUCCESS; new run_id + new COG
- [ ] New COG has more NaN cells than job-0070's COG (confirms 0.05 threshold landed)
- [ ] CRS still EPSG:32617 (job-0063 fix still in place)
- [ ] publish_layer auto-dispatch succeeds (or honest fallback to manual gcloud)
- [ ] Playwright screenshot captured
- [ ] Report narrates the visible difference vs job-0074's screenshot — honest assessment of whether the corrections appear or not
- [ ] No edits to FROZEN paths
- [ ] Single commit
