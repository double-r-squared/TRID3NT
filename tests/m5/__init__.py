"""GRACE-2 M5 acceptance suite (job-0043).

Closes sprint-07 / SRS §7 M5 (SFINCS engine v0 + ``model_flood_scenario``
workflow capstone): re-verifies the M5 substrate end-to-end against the
deployed Cloud Run substrate by driving a real "Hurricane Ian / Fort Myers"
flood-modeling demo through the agent's ``run_model_flood_scenario``
workflow-wrapper tool.

Substrate gate: tests in this suite are marked ``live_m5`` and are opt-in
via ``-m live_m5`` (or invoked by ``make test-m5``). The default
``make test`` collection does NOT pick them up — they touch the live cache
bucket, the deployed Cloud Workflows orchestrator, public APIs (Nominatim
/ 3DEP / MRLC WCS / NHDPlus HR / NOAA Atlas 14), and may submit a real
Cloud Workflows execution.

Two acceptable outcomes (per kickoff §1):

1. SUCCESS — the workflow returns an ``AssessmentEnvelope`` with a
   populated ``flood_depth`` LayerURI pointing at a real COG in the
   runs bucket; the M5 milestone moment.
2. HONEST FAILURE — the chain runs through to ``build_sfincs_model`` /
   ``run_solver`` / ``wait_for_completion``; the workflow returns a typed
   failed envelope (zero-valued ``FloodMetrics`` + ``flood.metrics.solver_version =
   "failed:<ERROR_CODE>"``). The substrate verification is the M5
   criterion — NOT SFINCS scientific output success.

On the current Debian dev box ``hydromt_sfincs`` is intentionally NOT
installed (heavyweight dep that the production SFINCS container has but
the agent dev venv does not — see job-0042 evidence): the workflow exits
through ``build_sfincs_model`` with ``HYDROMT_UNAVAILABLE`` as the typed
error code. The test asserts this outcome class is observed honestly —
the chain ran through the fetcher cache + NLCD gate (which now PASSES
after job-0044's WCS hotfix), built a ``ForcingSummary`` from real Atlas
14 11.9-inch / 100-yr / 24-hr Fort Myers data, and surfaced a typed
failure at the SFINCS-deck build step. This is the substrate-verification
outcome the kickoff explicitly accepts.

Failure-naming discipline (per testing.md): every assertion message
attributes the failing layer in the M5 substrate set:
``web client | agent | workflow | atomic tool | cache shim | QGIS Server
| SFINCS | Cloud Workflows | network | upstream API``.
"""
