# Report: MODFLOW groundwater solver on AWS (job-0292b)

**Commit:** `00befd5` (7 files, +1445/−97). **Status:** DONE — live-verified by orchestrator.

*(Placed by orchestrator from the runner's returned content; harness blocked the runner's write. Full design in the runner's final message — key points below.)*

- `LocalSolverSpec` (solver.py:607) generalizes the job-0291 local backend: SFINCS = docker spec, MODFLOW = image-less local-exec spec (`GRACE2_MF6_BIN`, killpg cancel, mfsim.lst divergence classifier). Shared staging/supervisor/completion/cancel; `wait_for_completion` dispatches on workflow_name ∈ {local-docker, local-exec}.
- Deck assembly + UCN read + plume COG upload scheme-aware via boto3; plume publish wired through the TiTiler template path (`rescale=0,10&colormap_name=reds`; typed RASTER_PUBLISH_UNAVAILABLE fallback).
- mf6 6.5.0 SHA-pinned binary installed on the instance by orchestrator (sha256 0fac0021… verified).
- Suite 4434 → 4451 (+17, `tests/test_modflow_local_backend.py`); sanctioned 5 failures byte-identical.
- **Orchestrator live verification (2026-06-12):** full WS chain on the AWS deployment — geocode → confirm gate → MODFLOW complete (~14s) → plume COG → publish_layer complete (one transient failure auto-retried by the model per job-0177) → narration with peak TCE 32.4 mg/L vs EPA MCL + plume area; red-gradient raster published. `GROUNDWATER SIM COMPLETED ON AWS`.
- Deploy note: the agent bundle now MUST include `services/workers/` (run_modflow.py resolves `parents[5]/services/workers/modflow` repo-relative — first live run failed dispatch without it).
- OQs: OQ-292B-PLUME-RESCALE (static 0–10 mg/L band saturates near-source), OQ-292B-EXEC-CANCEL-AFTER-RESTART (accepted v0.1).
