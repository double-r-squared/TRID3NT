# job-0258-web-20260610 — kickoff (frozen, verbatim)

You are a Fable-5 fix agent. Job job-0258-web-20260610 — LAYER CONTROLS DEAD: in the live demo, the LayerPanel's per-layer features (stack/ordering and opacity) do not work.

## Rules (Fable-5 critical batch — maximum rigor)
Working dir: /home/nate/Documents/GRACE-2
You are running as the strongest available model on a user-demo-blocking bug. Diagnose to TRUE root cause with file:line evidence before fixing — no symptom patches. The Stage 3 campaign just found 13 bugs where mocks mirrored assumptions; distrust assumptions, verify against reality (live GCS / real DOM / real persisted files where possible).
- NO Gemini/Vertex generate calls. Do NOT restart the agent on :8765 or touch the running web dev server's behavior beyond HMR-safe edits — the USER is actively demoing; web/src edits are acceptable (HMR) but keep them atomic.
- gcloud at /home/nate/tools/google-cloud-sdk/bin (ADC live). GCP/network Bash: dangerouslyDisableSandbox:true. Python venv: services/agent/.venv.
- Commit ONLY your owned files: git add <files> && git commit -m "job-02XX: <title>". index.lock: wait 5s retry 5x.
- Write reports/inflight/<job-id>/{audit.md (kickoff verbatim), report.md, STATE}.
Return StructuredOutput.

## Diagnosis leads
1. web/src/components/LayerPanel.tsx (+ Map.tsx): find the opacity slider + stack/order controls. Are handlers wired to MapLibre (map.setPaintProperty('raster-opacity') / moveLayer)? Common classes: handler updates React state but never calls the map; layer id mismatch (panel uses layer_id, MapLibre uses prefixed source/layer ids); controls render but pointer-events blocked by CSS.
2. Reproduce in vitest/jsdom where possible + a Playwright DEV-SEAM check against the RUNNING dev server WITHOUT driving Gemini: inject a layer via the existing __grace2Inject seams or directly add a raster source via page.evaluate on the maplibre instance, then click the controls and assert map paint properties changed. Do NOT send chat messages.
## Fix + prove
Fix in web/src (LayerPanel/Map wiring), vitest for the handler->map calls, and a Playwright screenshot pair (before/after opacity change) as evidence.
