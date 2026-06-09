# Audit: Wave 4.8 Stage B — restart + Playwright full demo verify

**Job ID:** job-0174-testing-20260608, **Specialist:** web/testing (Opus, gated)

## Scope
After Stage A:
1. Restart local agent backend (find PID via `ss -lntp | grep 8765`; setsid + nohup launch with full env: GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_LOCATION + GOOGLE_CLOUD_PROJECT + GOOGLE_GENAI_USE_VERTEXAI + GDAL_NUM_THREADS=1 + GRACE2_DEV_PERSISTENCE=1)
2. Playwright drive:
   - AuthGate → anonymous → app
   - Create Case "Test 4.8"
   - "Show me radar over America" — verify NEXRAD WMS layer renders on map (raster path)
   - "Show me weather alerts across America" — verify polygon overlay renders (vector path)
   - "Show me protected areas in Fort Myers" — verify multi-tool chain works (geocode → fetch_wdpa → render)
   - Trigger error (corrupted prompt) — verify chat input returns to idle, card transitions red
   - Try to pan/drag map — verify it moves
   - Switch to a new Case — verify empty state, no stale layers
   - Switch back to "Test 4.8" — verify layers + chat restored
3. Capture 7 screenshots to evidence/

## File ownership
- `reports/inflight/job-0174-testing-20260608/`

## FROZEN
Single commit prefix `job-0174:`.
