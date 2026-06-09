# Audit: Wave 4.7 Stage B — restart agent + Playwright full demo

**Job ID:** job-0167-web-20260608, **Sprint:** sprint-12-mega Wave 4.7 Stage B (gated)

## Scope
After Wave 4.7 Stage A fixes land:
1. Kill existing agent (find PID via `ss -lntp | grep 8765`); restart with env vars: `GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json GOOGLE_CLOUD_LOCATION=us-central1 GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod GOOGLE_GENAI_USE_VERTEXAI=True GDAL_NUM_THREADS=1 GRACE2_DEV_PERSISTENCE=1`
2. Drive Playwright full Fort Myers demo:
   - AuthGate → anonymous
   - Create Case
   - Send "Model peak flood depth from a 100-year design storm in Fort Myers, FL"
   - Verify Gemini's invented kwargs don't crash (harness fix from 0164)
   - Verify zoom-on-area-first fires <5s
   - Verify single transitioning llm_generation card
   - Verify font consistent
   - Verify map renders flood layer ~5min later
   - Trigger an error (corrupted prompt) — verify card turns RED + animation STOPS
3. Capture 5 screenshots to evidence/

## File ownership
- `reports/inflight/job-0167-web-20260608/`

## FROZEN
All implementation files. Single commit prefix `job-0167:`.
