# Audit: pandas pin to resolve hydromt-sfincs 1.2.2 pandas-3 incompat (OQ-54 + OQ-55)

**Job ID:** job-0056-infra-20260607, **Sprint:** sprint-08 (mid-sprint follow-up #4 to migration chain), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra

**Prerequisites:**
- **job-0054 (APPROVED):** Comprehensive hydromt-sfincs 1.2.x API migration audit. Surfaced OQ-54-HYDROMT-SFINCS-PANDAS-3X-INCOMPAT — sfincs.py:1858 calls `pd.RangeIndex.is_integer()` which was removed in pandas ≥ 2.0.
- **job-0055 (APPROVED):** Dropped `setup_river_inflow` per OQ-54 rec (b); chain advanced past the `set_forcing_1d` path; new blocker OQ-55-PRECIP-FORCING-PANDAS3X-FREQ-ALIAS — sfincs.py:2456 calls `pd.date_range(..., freq="10T")` which pandas 3.0 removed (the `"T"` minute alias).
- job-0049 (APPROVED): hydromt-sfincs install in agent service.

**SRS references** (narrow file loading only):
- `docs/decisions/oq-4-hydromt-depth.md` — OQ-4 §4 paper pin context
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/complete/job-0054-engine-20260607/report.md` — established class of failure
- `reports/complete/job-0055-engine-20260607/report.md` — second pandas-3 incompat surfaced
- `services/agent/pyproject.toml` — current dep resolution context

### Why this job exists

Two consecutive hydromt-sfincs 1.2.2 calls hit pandas-3 incompats:
- OQ-54: `pd.Index.is_integer()` — removed in pandas 2.0
- OQ-55: `pd.date_range(freq="10T")` — pandas 3.0 removed `"T"` alias (use `"min"`)

Both routing recommendations converge: **pin pandas to a version where hydromt-sfincs 1.2.2 was authored against**. A single pandas downgrade-pin should unblock BOTH AND any other pandas-3 bugs lurking in hydromt-sfincs 1.2.2 itself.

This is infra rather than engine because: (a) it's a `pyproject.toml` dep-resolution change with potential cascade through geopandas / numpy / rasterio / etc.; (b) production Cloud Run container construction will need to consume the new pin; (c) the verification path is "re-resolve deps in `.venv-agent`, re-run M5 smoke, verify both upstream bugs disappear".

### Scope

1. **Determine the right pandas version** via empirical resolution:
   - **Path A (preferred):** `pandas >= 2.1, < 2.2` — sweet spot where `pd.Index.is_integer()` still exists (deprecated) AND `freq="T"` alias still works. Verify both upstream calls succeed under pandas 2.1.x.
   - **Path B (fallback):** `pandas >= 1.5, < 2.0` — older but maximally compatible with hydromt-sfincs 1.2.2's actual era. May cascade pin constraints on numpy / geopandas / rasterio.
   - **Path C (last resort):** if BOTH Path A and Path B break dependent packages, document the cascade with specific failures and route to a comprehensive pandas-3-incompat audit (job-0057).
   - Confirm choice via live `.venv-agent` reinstall + import test that exercises BOTH the `is_integer()` call AND the `freq="10T"` call (small synthetic Python script in evidence/).

2. **Update `services/agent/pyproject.toml`** with the chosen pandas pin. Add a comment block citing OQ-54 + OQ-55 + the chosen version + why. Note the v0.2+ migration path: when hydromt-sfincs upstream lands a pandas-3 fix (likely v1.2.3 or v2.0 RC), drop the pin.

3. **Re-resolve `.venv-agent`** and capture the actual resolved versions for ALL pandas-adjacent packages (numpy, geopandas, rasterio, xarray, pyproj, shapely). Document any constraint pins forced by the cascade.

4. **Re-run M5 smoke chain** using `reports/complete/job-0055-engine-20260607/evidence/smoke_demo.py` as the harness (copy to `reports/inflight/job-0056-infra-20260607/evidence/`). Outcomes:
   - **SUCCESS (the headline):** chain produces a real flood-depth COG. Capture comprehensive evidence — GCS URI of output; sample byte-read confirming COG is real (rasterio bounds + bands + small windowed read); AssessmentEnvelope JSON; full smoke log. **Explicitly call out "SCREENSHOT MOMENT — orchestrator captures via Playwright" in your final summary.**
   - **PARTIAL SUCCESS:** chain advances further but hits ANOTHER class of failure (e.g., solver dispatch, infrastructure). Document specifically.
   - **STILL BLOCKED:** new pandas issue, or different upstream bug. Document; this would route to either comprehensive pandas-3 audit (job-0057) or upstream patch.

5. **Tests** in `services/agent/tests/` — add a regression test that imports hydromt-sfincs + exercises BOTH `pd.Index.is_integer()` + `pd.date_range(freq="10T")` to guard against a future pandas re-bump.

6. **Honest disclosure**: if production Cloud Run container hasn't been re-deployed yet (Dockerfile didn't exist as of job-0049), document this gap — the pin is correct in pyproject.toml but doesn't propagate to deployed agent until OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING is resolved.

### File ownership (exclusive)
- `services/agent/pyproject.toml` — ONLY the pandas pin addition
- `services/agent/tests/` — additive regression test
- `reports/inflight/job-0056-infra-20260607/`

### FROZEN
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` — owned by engine; do not touch (concurrent-closed job-0055)
- `services/agent/src/grace2_agent/workflows/manning_mapping.csv` (OQ-4 §4 substrate)
- All other workflows/* and tools/* files
- packages/contracts/**, web/**, docs/srs/**, styles/**, services/workers/**, reports/complete/**

### Acceptance criteria
- [ ] `services/agent/pyproject.toml` includes a pandas pin with comment block citing OQ-54 + OQ-55
- [ ] `.venv-agent` re-resolved + actual resolved versions documented
- [ ] BOTH upstream bugs disappear in a synthetic Python test (live evidence)
- [ ] M5 chain re-run with honest disclosure of final outcome (SUCCESS / PARTIAL / STILL-BLOCKED)
- [ ] ≥1 new regression test
- [ ] No edits to FROZEN paths
- [ ] If SUCCESS, explicit "SCREENSHOT MOMENT" call-out so orchestrator captures it
- [ ] OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING noted as carry-forward if relevant
- [ ] Single commit
