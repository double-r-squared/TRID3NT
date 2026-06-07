# Report: OQ-4 HydroMT integration depth decision (research → decision doc)

**Job ID:** job-0038-engine-20260606
**Sprint:** sprint-07
**Specialist:** engine
**Task:** Research three HydroMT integration depths against the M5 SFINCS demo target (Hurricane Ian / Fort Myers, ≤200 km², ≤30 m, NFR-P-4 ≤15 min); recommend one depth; land the decision in `docs/decisions/oq-4-hydromt-depth.md`; establish `docs/decisions/README.md` convention doc.
**Status:** ready-for-audit

---

## Summary

Researched three HydroMT integration depths (Full HydroMT, Partial HydroMT, Custom config builders) against the M5 SFINCS demo target using direct web research against the `hydromt-sfincs` GitHub repo, PyPI metadata, and published changelogs. Selected **Full HydroMT (Option A)** as the integration depth for `build_sfincs_model` on Decision J tractability grounds: HydroMT-SFINCS eliminates ~1,500 lines of custom format logic (DEM conditioning, Manning's reclassification, SFINCS input writing) that would otherwise require independent maintenance and testing, and the published benchmark profile fits within the NFR-P-4 ≤15-min budget. Two deliverable files were authored: `docs/decisions/oq-4-hydromt-depth.md` (the OQ-4 resolution, five-section format) and `docs/decisions/README.md` (convention explanation, one paragraph). No production code was touched; no SRS prose was touched.

---

## Changes Made

- **`docs/decisions/oq-4-hydromt-depth.md`** (NEW)
  - Full five-section decision doc (Context, Options Considered, Decision, Consequences, References).
  - Covers all three integration depths with: code-surface estimates, full dependency footprint from `pyproject.toml` inspection (16 direct packages for the plugin; core HydroMT adds xarray, rasterio, dask, numba, zarr, fsspec, pydantic, etc.; total container ~900 MB+), wflow/Delft3D FM generalization analysis, silent-failure modes for upstream data surprises, and PipelineEmitter / cancellation compatibility notes.
  - Recommends Full HydroMT (Option A) with rationale against Decision J, NFR-P-4, and Invariant 2.
  - Surfaces 3 required open sub-questions (OQ-4a, OQ-4b, OQ-4c) plus one bonus (OQ-4d) with TENTATIVE resolutions.
  - Surfaces one new technical-debt item: a mandatory validation gate for the NLCD mapping CSV to prevent HydroMT's silent fallback from producing wrong Manning's grids.

- **`docs/decisions/README.md`** (NEW)
  - One-paragraph convention description: what this directory is, how it relates to SRS §6 OQs, the five-section format constraint, the file-per-OQ rule, and how downstream jobs should cite decision docs.

---

## Decisions Made

- **Decision: Full HydroMT (Option A) is the integration depth for `build_sfincs_model`.**
  - Rationale: Decision J (easy wins first) — HydroMT-SFINCS is the Deltares-recommended, peer-reviewed path for SFINCS setup; custom builders require ~1,500–2,500 lines of DEM conditioning + SFINCS format logic that duplicates Deltares engineering already tested at global scale. The dependency footprint (900 MB+ container, GPLv3 in a separate Cloud Run Job) is manageable and does not contaminate our license posture. Determinism is preserved (frozen YAML config + GCS-cached rasters → reproducible `sfincs.inp`).
  - Alternatives considered: Partial HydroMT (B) — rejected because it retains the full dependency weight without reducing it while adding ~300 lines of SFINCS input format code; Custom builders (C) — rejected as a multi-sprint investment that Decision J explicitly defers when a tractable alternative exists.

- **Sub-tradeoff 1: Generalization to other Deltares solvers.**
  - Full HydroMT generalizes: each additional Deltares solver (wflow, Delft3D FM) is a new `hydromt-<solver>` plugin reusing the same DataCatalog bridging pattern. Custom builders do not generalize — each solver requires a new 1,500+ line implementation.

- **Sub-tradeoff 2: Failure mode on upstream data surprise.**
  - HydroMT silently falls back to default `manning_land`/`manning_sea` values when NLCD class integers do not match the mapping CSV — a silent-wrong-answer failure mode. Mitigated by a mandatory validation gate in `build_sfincs_model` (check NLCD vintage class set against mapping CSV before calling HydroMT; raise `SFINCSSetupError("LULC_MAPPING_MISMATCH")` on mismatch). Custom builders (Option C) fail loudly (`KeyError`) — their one meaningful advantage for v0.1, but the validation gate achieves the same observable behaviour on top of Option A.

- **Sub-tradeoff 3: Progress reporting with PipelineEmitter.**
  - HydroMT's `@hydromt_step` decorator does not expose a progress callback compatible with `PipelineEmitter.update_progress`. Model setup runs as a single logical step in the pipeline strip (coarse-grained start/finish). Acceptable for v0.1 — model setup takes 2–4 min of the 15-min budget; the solver run dominates and emits Cloud Workflows-level progress. Custom builders (Option C) would allow `update_progress` calls between pipeline stages, but the benefit is cosmetic for v0.1.

---

## Invariants Touched

- **Decision J tractability (§2.3):** extends — this decision is an explicit application of Decision J. "Python shim via HydroMT" in the §2.3 v0.1 catalog is now concretely specified as full HydroMT end-to-end.
- **Invariant 2 (Deterministic workflows):** preserves — Full HydroMT with a frozen YAML build config and GCS-cached input rasters produces a deterministic `sfincs.inp`. No randomness is introduced.
- **Invariant 7 (No silent wrong answers):** extends — the mandatory NLCD mapping validation gate in `build_sfincs_model` is required by this decision to prevent HydroMT's silent fallback from violating Invariant 7. Job-0042 must implement this gate.
- **Invariant 8 (Cancellation is first-class):** preserves — Cloud Workflows `terminate` reaches the solver container; no change from the existing architecture.

---

## Open Questions

- **OQ-4a (HydroMT generalization scope) — TENTATIVE: defer generic bridge class to second solver.**
  Whether to design a reusable `HydroMTCatalogBridge` class now (anticipating wflow/Delft3D FM) or defer until the second solver is added. TENTATIVE: defer; document the pattern in docstrings so the second solver can extract it without a refactor.

- **OQ-4b (pin-or-vendor strategy) — TENTATIVE: PyPI pin at `>= 1.1.2, < 2.0`.**
  Whether to pin `hydromt-sfincs` to a tested PyPI release or vendor it as a git submodule. TENTATIVE: pin on PyPI at `>= 1.1.2, < 2.0`. Note: HydroMT v2.0.0-rc2 (released November 2025) has breaking `SfincsModel` API changes; the v2.0 migration guide exists but the RC is unstable.

- **OQ-4c (bbox unsupported — graceful degrade vs hard fail) — TENTATIVE: raise typed error.**
  Whether to raise `SFINCSSetupError` with structured error codes (`DEM_COVERAGE_GAP`, `FORCING_OUT_OF_RANGE`, `LULC_MAPPING_MISMATCH`) or attempt a partial setup and return a degraded `ModelSetup`. TENTATIVE: raise typed error — consistent with Invariant 7 and pipeline-strip observability (FR-WC-8).

- **OQ-4d (HydroMT GCS raster driver is experimental) — surfaced, not blocking.**
  HydroMT data catalog docs note that GCS support for the `raster` driver is "still experimental." If GCS-backed rasters cannot be read directly in the solver container, `build_sfincs_model` must download rasters to local temp storage before constructing the catalog. Job-0042 must verify at implementation time.

---

## Dependencies and Impacts

- **Depends on:** job-0035-agent-20260606 (PipelineEmitter — `update_progress` compatibility confirmed as M5+ opt-in, not required here).
- **Enables:**
  - **job-0039** (3 fetcher tools): `fetch_dem` must return GCS-accessible `LayerURI`; `fetch_landcover` must return NLCD vintage year alongside the URI; forcing tools must emit HydroMT-compatible NetCDF or CSV. See `docs/decisions/oq-4-hydromt-depth.md` §4 "Immediate (job-0039)" for specifics.
  - **job-0040** (infra/sfincs.tf + solver container): Dockerfile must include `hydromt-sfincs >= 1.1.2, < 2.0` and its conda-forge dependency stack. GPLv3 license must be documented in `infra/THIRD_PARTY_LICENSES.md`. Container size ~900 MB–1 GB; cold-start time must be measured at M5.
  - **job-0042** (`model_flood_scenario` workflow): `build_sfincs_model` wraps `SfincsModel` via Full HydroMT; must implement the NLCD validation gate; must handle the GCS-experimental fallback (OQ-4d).

---

## Verification

- **Tests run:** None — research + decision job; no production code touched.
- **Live E2E evidence:** Not applicable — produces two documentation files only.
- **Results:** qualified — doc-only job per kickoff scope.

**Structural checks:**
1. `docs/decisions/oq-4-hydromt-depth.md` — exists, 5-section format, single concrete recommendation (Full HydroMT / Option A), under 300 lines. PASS
2. `docs/decisions/README.md` — exists, one-paragraph convention explanation. PASS
3. Recommendation implementable by job-0039 and job-0042 without re-litigating: §4 of the decision doc specifies exactly what each downstream job must do. PASS
4. No code touched (only `docs/decisions/` and `reports/inflight/job-0038-engine-20260606/` written). PASS
5. No SRS prose touched. PASS
6. FROZEN paths not touched. PASS
7. Three minimum OQs surfaced with TENTATIVE tags (OQ-4a, OQ-4b, OQ-4c). PASS (OQ-4d bonus.)
