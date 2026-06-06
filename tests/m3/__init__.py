"""GRACE-2 M3 acceptance suite (job-0028).

Closes sprint-05 / SRS §7 M3 (Web client skeleton): re-verifies every M3 exit
criterion against the live substrate — deployed QGIS Server WMS (Cloud Run,
image @sha256:57d0f43 after job-0029 CORS fix) + local Vite dev server +
headless Chromium / Firefox via Playwright.

Test split (kickoff §Scope item 2 + cross-browser scope clarification):

* tests/m3/playwright/test_wms_tiles.py        — Chromium + Firefox
* tests/m3/playwright/test_layer_panel.py      — Chromium (visual smoke)
* tests/m3/playwright/test_pipeline_strip.py   — Chromium
* tests/m3/playwright/test_camera_lock.py      — Chromium (Decision I)
* tests/m3/playwright/test_no_gs_uri.py        — no browser (build grep)

The simulated WS server / direct dev-seam state injection is permitted ONLY
for LayerPanel + PipelineStrip seeding because the agent does not yet emit
populated session-state with loaded_layers or pipeline-state envelopes in M3
(M4 work — testing.md "mocks ONLY at external boundaries", with this internal
seam documented in the report's Open Questions). The WMS-tile test hits the
real deployed Cloud Run QGIS Server with no simulation; the cancel-envelope
test rides the real M1 cancel chain (job-0015 verified end-to-end at 502ms).
"""
