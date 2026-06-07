"""GRACE-2 M4 acceptance suite (job-0036).

Closes sprint-06 / SRS §7 M4 (Agent tool registry + 7 atomic tools):
re-verifies every M4 exit criterion against the live substrate — local
agent service (real ``grace2_agent.server`` + ``PipelineEmitter`` + 8 tool
registry from sprint-06 Stages B/C), real GCS cache bucket
``gs://grace-2-hazard-prod-cache/`` (provisioned by job-0031), Nominatim
public REST endpoint, USGS 3DEP via py3dep.

Substrate gate: tests in this suite are marked ``live_m4`` and are opt-in
via ``-m live_m4`` (or invoked by ``make test-m4``). The default
``make test`` collection does NOT pick them up — they touch the live cache
bucket, public APIs, and (for the demo) the local ``qgis_process`` binary.

Failure-naming discipline (per testing.md): every assertion message
attributes the failing layer in the M4 substrate set:
``web client | agent | tool registry | cache shim | QGIS Server | network |
upstream API``.
"""
