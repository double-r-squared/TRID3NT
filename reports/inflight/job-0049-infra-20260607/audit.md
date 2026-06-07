# Audit: hydromt-sfincs install in agent service (closes OQ-43)

**Job ID:** job-0049-infra-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra

**Prerequisites:**
- job-0042 OQ-4 §4 contract (`hydromt-sfincs >= 1.1.2, < 2.0`)
- job-0043 OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL (this job closes it)
- job-0040 SFINCS container substrate (the SFINCS container already has hydromt-sfincs; this job adds it to the agent service so `build_sfincs_model` can run in-process inside the agent for the workflow composition step)

**SRS references** (narrow files only):
- `docs/decisions/oq-4-hydromt-depth.md` — `hydromt-sfincs >= 1.1.2, < 2.0` pin; GPLv3 license posture
- `docs/srs/04-non-functional-requirements.md` — NFR-L (MIT posture preserved by out-of-process invocation; in-process import for build_sfincs_model is fine because GRACE-2 code doesn't link against hydromt-sfincs's GPL'd binaries — it imports a Python module + calls functions)
- DO NOT load `docs/SRS_v0.3.md` monolith

### Scope

1. **Add `hydromt-sfincs >= 1.1.2, < 2.0` to `services/agent/pyproject.toml`** runtime deps. Also add transitive deps that HydroMT needs: `hydromt >= 1.0`, `fsspec[gcs]` (already there from job-0033 / job-0037).
2. **Update agent service Dockerfile** (if one exists — check `services/agent/`) to install the new deps. If the agent service is currently deployed as a Cloud Run service with a Dockerfile, add the pip install. If it runs via Cloud Run's source-deploy or buildpacks, the pyproject.toml update may be sufficient.
3. **Update `infra/THIRD_PARTY_LICENSES.md`** to document `hydromt-sfincs` GPLv3 license per OQ-4 §4 contract. Out-of-process via Cloud Run Jobs for SFINCS itself was the original isolation; in-process Python import for HydroMT's deck-building doesn't link against GPL binaries (it's Python source running as data-driven script), but documenting clearly preserves the NFR-L MIT posture.
4. **Live verification:**
   - `pip install hydromt-sfincs` in the test venv `.venv-agent/` and verify `python -c "import hydromt_sfincs; print(hydromt_sfincs.__version__)"` succeeds
   - Re-run job-0042's smoke workflow OR job-0043's M5 demo against the local agent with hydromt-sfincs installed; capture the result — either `build_sfincs_model` now proceeds further (success or next-honest-blocker) or it fails for a reason that's not `HYDROMT_UNAVAILABLE`
   - If the agent service is deployed to Cloud Run, redeploy with the new pyproject + capture `gcloud run services describe grace-2-agent --format=...` post-deploy

### File ownership (exclusive)
- `services/agent/pyproject.toml`
- `services/agent/Dockerfile` (if exists; if Cloud Run buildpacks, surface that decision)
- `infra/THIRD_PARTY_LICENSES.md` (NEW or extend)
- `services/agent/uv.lock` or equivalent (if lockfile in tree; regenerate)
- `reports/inflight/job-0049-infra-20260607/`

### FROZEN
- `services/agent/src/**` (consumer; don't modify)
- All other `infra/*.tf` (this is a pyproject + Dockerfile change, not a tofu module change unless there's an agent-service Cloud Run module that needs digest bumping)
- `packages/contracts/**`, `services/workers/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**`
- Stage A concurrent jobs

### Acceptance criteria
- [ ] `hydromt-sfincs >= 1.1.2, < 2.0` installed in `.venv-agent` + verified import works
- [ ] Re-run of job-0042 / job-0043 M5 chain past the previous HYDROMT_UNAVAILABLE failure — capture the new outcome honestly
- [ ] `infra/THIRD_PARTY_LICENSES.md` documents the GPLv3 dep
- [ ] No edits to FROZEN paths
- [ ] Closes OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL with cited evidence
