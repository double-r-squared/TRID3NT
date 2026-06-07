# Audit: hydromt-sfincs install in agent service (closes OQ-43)

**Job ID:** job-0049-infra-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** approved

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

## Assessment

**Verdict:** approved.

The hydromt-sfincs install lands cleanly with honest disclosure of two real ecosystem realities:

1. **OQ-4 §4 pin reconciliation needed.** The decision doc pinned `hydromt-sfincs >= 1.1.2, < 2.0` + `hydromt >= 1.0, < 2`, but live install reveals: `hydromt-sfincs 1.1.2` doesn't exist on PyPI (sequence is 1.1.0 → 1.2.0); the stable 1.2.x line transitively constrains `hydromt < 1` (not `>= 1.0` as the decision claimed). Specialist corrected to `hydromt-sfincs >= 1.1.0, < 2.0` and let `hydromt` resolve transitively. Resolved versions: hydromt_sfincs 1.2.2 + hydromt 0.10.1 + fsspec 2026.4.0. **This is exactly the live-verification-beats-documentation discipline** sprint-7 trained. Surfaced as OQ-49-HYDROMT-SFINCS-PIN-RECONCILIATION for OQ-4 decision-doc amendment.

2. **M5 chain advances past HYDROMT_UNAVAILABLE; lands on a new HONEST blocker.** Re-running job-0043's smoke harness: fetchers cache-hit; **Invariant 7 NLCD validation gate passes** (canonical classes `[11, 21-24, 31, 41-43, 52, 71, 81, 82, 90, 95]`); Atlas 14 forcing loads (11.9 inches at Fort Myers); **HydroMT-SFINCS initialises successfully** (`Initializing sfincs model from hydromt_sfincs (v1.2.2)`); then fails with `HYDROMT_BUILD_FAILED` underlying `'str' object has no attribute 'keys'`. Root cause traced to `sfincs_builder.py:692` — passing raw YAML text to `SfincsModel.build(opt=...)` where hydromt-sfincs 1.2.x expects a parsed dict. **One-line engine fix** (`yaml.safe_load` before passing). Routes to a focused hotfix job. **OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE.**

**No agent Dockerfile change** — `services/agent/` has no Dockerfile today; the agent service is not yet deployed to Cloud Run (only QGIS Server + worker are). pyproject.toml is the only install surface. Honest disclosure: a future agent Cloud Run deploy will pick up the dep automatically. **OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING.**

**No flood-depth COG produced; no screenshot moment.** The chain is now one engine-side line-fix away from attempting a real SFINCS run.

`infra/THIRD_PARTY_LICENSES.md` (NEW) documents GPLv3 posture + the pin-correction record + the NFR-L MIT posture preservation rationale (out-of-process invocation for SFINCS binary; in-process Python import for HydroMT deck-building is Python source running as data-driven script, not linking against GPL'd binaries).

## Invariant Check

- **Invariant 1, 5, 7:** preserved. Invariant 7 NLCD gate verified PASS again on this re-run.
- **NFR-L MIT posture:** preserved + documented in THIRD_PARTY_LICENSES.md.
- **§F.1.1 access tier:** NLCD WCS continues to work as expected post-v0.3.20 housekeeping.
- **Diagnose before fix:** the HYDROMT_BUILD_FAILED diagnosis (sfincs_builder.py:692 line + 1.2.x API change) is exactly the right level of detail to hand to a small engine hotfix.

## Decisions Validated

- Pin correction: `hydromt-sfincs >= 1.1.0, < 2.0` over the decision-doc's `>= 1.1.2` — pragmatic; let pip resolve `hydromt` transitively.
- No Dockerfile change for agent service — there isn't one yet; honest disclosure rather than building one prematurely.
- NFR-L MIT posture preserved via documentation; in-process Python import doesn't link against GPL binaries.

## Open Questions Resolved

**Closes:** OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL.

Filed for triage:
- **OQ-49-HYDROMT-SFINCS-PIN-RECONCILIATION** — OQ-4 decision doc amendment to match the live PyPI reality.
- **OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE** — **CRITICAL** — one-line engine fix in `sfincs_builder.py:692` (yaml.safe_load before passing dict to SfincsModel.build). Routes to a focused engine hotfix job (next).
- **OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING** — informational; future deploy will pick up dep automatically.

## Follow-up Actions

1. **Open job-0052 (engine hotfix)** for OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE — one-line yaml.safe_load fix in sfincs_builder.py:692. Counter 51 → 52. After 0052 lands, M5 chain can attempt a real SFINCS run for the first time.
2. **OQ-4 decision-doc amendment** — bundle into v0.3.21 housekeeping or sprint-08 close pass.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.** Closes OQ-43. M5 chain unblocked at the HydroMT-import layer; one focused engine hotfix away from attempting a real SFINCS run.
