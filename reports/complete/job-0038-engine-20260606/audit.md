# Audit: OQ-4 HydroMT integration depth decision (research → decision doc)

**Job ID:** job-0038-engine-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** engine (Sonnet — research + summarize + recommend; no production code)

**Prerequisites:**
- SRS §6 Open Question 4 (HydroMT integration depth): "full reliance for SFINCS setup, or custom config builders? HydroMT is powerful but adds a heavy dependency."
- v0.3.15 §2.3 lists SFINCS as "Python shim via HydroMT" — establishes HydroMT as part of the v0.1 integration mode.

**SRS references** (narrow files only):
- `docs/srs/06-open-questions.md` — OQ-4 verbatim
- `docs/srs/02-system-overview.md` — §2.3 Engine catalog; Decision J tractability principle; SFINCS row in v0.1 catalog
- `docs/srs/03-functional-requirements.md` — FR-TA-1 (the `model_flood_scenario` workflow you're scoping for) + FR-CE-1/2/3 (Cloud Workflows orchestration)
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Environment
HydroMT is a Python library (Deltares) that scaffolds SFINCS model setup from data ingredients. Three rough depths are commonly seen:

- **Full HydroMT** — use `hydromt-sfincs` plugin to ingest DEM + landcover + forcing + river network, generate the `sfincs.inp` + mesh + boundary conditions automatically. Lowest code surface; heaviest dep.
- **Partial HydroMT** — use `hydromt-sfincs` for the messy preprocessing (DEM hydro-conditioning, landcover→Manning's mapping) but author the `sfincs.inp` by hand from our atomic-tool outputs. Mid-level coupling.
- **Custom config builders** — skip HydroMT entirely; write Python that consumes our atomic-tool LayerURIs and emits `sfincs.inp` directly. Maximum control; heaviest custom code.

### Scope

1. **Research** the three depths against the M5 SFINCS demo target (Hurricane Ian / Fort Myers, ≤200 km², ≤30m resolution, NFR-P-4 ≤15 min). For each depth, note:
   - Code surface in our repo (estimated lines per atomic-tool integration)
   - Dependency footprint (HydroMT pins what?)
   - Flexibility for non-SFINCS solvers later (HydroMT also has `hydromt-wflow`, `hydromt-delft3dfm` plugins — does adopting full HydroMT generalize, or are we just adopting it for SFINCS?)
   - Failure mode when an upstream data source surprises (e.g., NLCD changes class encoding) — does HydroMT abstract this or does it leak?
   - Cancellation/progress reporting compatibility with job-0035 `PipelineEmitter`
2. **Recommend one depth** with clear rationale. Pick a depth, defend it, note the tradeoffs.
3. **Land the decision** in `docs/decisions/oq-4-hydromt-depth.md` (NEW file; see Decisions docs convention below). Closes OQ-4.

### Decisions doc convention (NEW — sprint-07 establishes the pattern)

`docs/decisions/` is a new directory for one-page resolution docs for open questions. Each file:
- One markdown file per OQ resolution.
- Title: `# OQ-<n>: <decision title>` matching the SRS OQ.
- Sections: Context (why this OQ existed), Options Considered (named alternatives with tradeoffs), Decision (the picked option + when picked + by whom), Consequences (what changes downstream), References (commits, SRS amendment, etc.).
- Length: under 300 lines. Tight.

This is the first decision doc; the convention lands with it. Future sprints close their OQs the same way.

### File ownership (exclusive)

- `docs/decisions/oq-4-hydromt-depth.md` (NEW)
- `docs/decisions/README.md` (NEW — one-paragraph convention doc explaining what `docs/decisions/` is)
- `reports/inflight/job-0038-engine-20260606/` — kickoff frozen

### FROZEN — no edits in this job

- `docs/srs/**`, `docs/SRS_v0.3.md` (OQ-4 lives in §6; this job RESOLVES it but the SRS prose closure (marking OQ-4 closed) is a separate orchestrator-direct edit at sprint-07 close, mirroring the v0.3.15 OQ-5 closure pattern).
- All of `services/`, `packages/`, `web/`, `infra/`, `styles/`, `tests/`, `reports/complete/`.
- Stage A concurrent jobs (data_fetch.py from 0037; infra/sfincs.tf from 0040).

### Cross-cutting principles in force

- **Invariant 2 (Deterministic dispatch):** the chosen depth must support deterministic model setup; HydroMT depths vary in determinism (some randomize sampling).
- **Decision J tractability:** the chosen depth must be tractable for v0.1; if it isn't, SFINCS itself gets re-evaluated.
- **NFR-P-4:** the chosen depth must enable ≤15-min runs for the M5 demo bbox.
- **Bundle small fixes:** if the research surfaces a separate SRS amendment opportunity (e.g., §2.3 prose tightening for SFINCS), note it but don't bundle here.

### Acceptance criteria (reviewer re-runs)

- [ ] `docs/decisions/oq-4-hydromt-depth.md` exists with the 5-section format + a clear single-depth recommendation.
- [ ] `docs/decisions/README.md` exists with the convention statement.
- [ ] Recommendation is implementable by job-0039 (3 fetcher tools) and job-0042 (`model_flood_scenario` workflow) without re-litigating.
- [ ] No code touched. No SRS prose touched (orchestrator handles the OQ-4 closure marker at sprint close).
- [ ] No edits to FROZEN paths.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: scope of HydroMT generalization (SFINCS-only vs all-Deltares-solvers); pin-or-vendor strategy for HydroMT (depend on PyPI vs vendor a tested release); behavior when HydroMT can't process the user-supplied bbox (graceful degrade vs hard fail).

## Assessment

**Verdict:** approved.

OQ-4 lands a concrete, defensible recommendation: **Full HydroMT (Option A)** via `hydromt-sfincs` end-to-end. The decision doc is tight (151 lines, well under the 300-line cap) and walks through the three options against three explicit filters — Decision J tractability, NFR-P-4 timing budget, Invariant 2 determinism — picking Full HydroMT with a calibrated tradeoff acknowledgement (+~900 MB container size vs ~1500 lines of custom DEM-conditioning + SFINCS-format code).

**Critical Invariant 7 risk surfaced and mitigated.** The specialist caught that HydroMT's roughness component silently fills unmatched NLCD class integers with default `manning_land`/`manning_sea` values — a real silent-wrong-answer risk. The decision mandates a **validation gate in `build_sfincs_model`** that checks the fetched NLCD vintage's class set against a version-pinned mapping CSV and raises `SFINCSSetupError("LULC_MAPPING_MISMATCH")` rather than allowing silent fallback. This is exactly the discipline the "diagnose before fix" cross-cutting principle demands. Job-0042 inherits this as a hard requirement.

The decision is concrete enough to consume:
- **Job-0039** gets specific contracts: `fetch_dem` returns GCS-readable LayerURI; `fetch_landcover` returns NLCD vintage year alongside URI; forcing tools emit HydroMT-compatible NetCDF or CSV.
- **Job-0040** gets a Dockerfile dependency line: `hydromt-sfincs >= 1.1.2, < 2.0` (v2.0 RC has breaking API changes; v1.x is the stable pin until 2.0 exits RC).
- **Job-0042** gets the validation gate requirement + the `DataCatalog` bridging pattern that converts our `LayerURI`s into HydroMT catalog entries.

The decision doc also acknowledges three forward-looking concerns honestly:
1. Cold-start time on Cloud Run Jobs with the larger image (~900 MB) — measure at M5, optimize if it exceeds 2–3 min.
2. HydroMT v2.0 migration when it exits RC — breaking changes documented; pin v1.x for now.
3. GPLv3 license of `hydromt-sfincs` — must be documented in `infra/THIRD_PARTY_LICENSES.md` (out-of-process invocation per NFR-L means GRACE-2 stays MIT; standard Pelicun + TELEMAC license isolation pattern).

**`docs/decisions/` convention established cleanly.** The README is a single-paragraph statement of the convention + the 5-section format + the constraints (one file per OQ, under 300 lines, concrete single-option recommendation, minimum sub-questions). Future OQ resolutions follow this pattern; saves re-litigating format for every future closure doc.

**Token economics: Sonnet at 79,970 tokens vs Opus average ~180K for this sprint pattern.** ~44% of Opus cost for a task that fits the research+summarize+recommend shape exactly. First validation of the cost-discipline rule.

## Invariant Check

- **Invariant 1 (Determinism boundary):** preserved. No LLM-in-the-loop in the recommended path; HydroMT YAML build configs are programmatic, deterministic.
- **Invariant 2 (Deterministic workflows):** preserved. The decision explicitly addresses this — given the same YAML build config + same GCS-cached inputs, `sfincs.inp` is byte-for-byte reproducible.
- **Invariant 7 (no silent wrong answers / claims have provenance):** the silent-fallback risk in HydroMT's roughness component is exactly the kind of failure mode Invariant 7 exists to prevent. The mitigation (validation gate in `build_sfincs_model`) is correctly identified as a job-0042 hard requirement.
- **Decision J (tractability):** the explicit filter the specialist used to choose Option A. Validated.

## Dependency Check

- **SRS §6 OQ-4** — resolved by this decision. Orchestrator-direct OQ closure marker lands in `docs/srs/06-open-questions.md` at sprint-07 close, mirroring the v0.3.15 OQ-5 pattern.
- **SRS §2.3 SFINCS row** ("Python shim via HydroMT") — consistent with the chosen depth; no §2.3 amendment needed.
- **Job-0039** (3 fetcher tools): concrete contracts inherited. Specialist must read `docs/decisions/oq-4-hydromt-depth.md` §4 "Immediate (job-0039)" before starting.
- **Job-0040** (SFINCS container): Dockerfile must include `hydromt-sfincs >= 1.1.2, < 2.0`; license documentation in `infra/THIRD_PARTY_LICENSES.md` required.
- **Job-0042** (`model_flood_scenario` workflow): inherits the `DataCatalog` bridging pattern + the NLCD validation gate as a hard requirement.

## Decisions Validated

- **Option A (Full HydroMT) over Option B (Partial) and Option C (Custom)** — correct call grounded in Decision J + NFR-P-4 + Invariant 2. Accepted.
- **NLCD validation gate as a hard requirement** (Invariant 7 mitigation) — correctly identified as critical, not a "nice to have". Accepted.
- **HydroMT v1.x pin** — correct given v2.0 RC status. Accepted with the upgrade path noted (post-v2.0-stable migration follow-up).
- **`docs/decisions/` convention** — single-source-of-truth doc per OQ; 5-section format; ≤300 lines; downstream jobs cite filename + section rather than re-litigating. Accepted as project convention.

## Open Questions Resolved

Closes:
- **SRS §6 OQ-4** (HydroMT integration depth) — Full HydroMT picked. Orchestrator marks closed in §6 at sprint-07 close.

Filed for triage:
- **OQ-4a** — Defer generic `HydroMTCatalogBridge` class until the second Deltares solver lands (wflow / Delft3D-FM, post-v0.1). YAGNI hold.
- **OQ-4b** — PyPI pin `hydromt-sfincs >= 1.1.2, < 2.0`. Upgrade follow-up after v2.0 exits RC.
- **OQ-4c** — Raise typed `SFINCSSetupError` (not graceful degrade) when bbox is unsupported. Job-0042 surface.
- **OQ-4d** — HydroMT's GCS raster driver is experimental; job-0042 must verify it works against our actual cache bucket and fall back to local temp download if needed. Verification follow-up at job-0042 time.

## Follow-up Actions

1. **SRS §6 OQ-4 closure marker** — orchestrator-direct edit at sprint-07 close, mirroring the v0.3.15 OQ-5 pattern (mark inline as "Closed by docs/decisions/oq-4-hydromt-depth.md").
2. **`infra/THIRD_PARTY_LICENSES.md`** — job-0040 must document the GPLv3 of `hydromt-sfincs` (out-of-process invocation, MIT posture preserved).
3. **Job-0039 + job-0042 kickoffs** — when scaffolded at Stage B / Stage D, must cite `docs/decisions/oq-4-hydromt-depth.md` as the inheritance source for the Full HydroMT contract.
4. **Cold-start measurement at M5** — capture container cold-start time during job-0043 acceptance; if it exceeds 2–3 min, surface as a follow-up optimization OQ.

## Sign-off

**Approved 2026-06-06 by Development Orchestrator.**

All 5 acceptance criteria met. Decision is concrete + actionable + downstream-consumable. `docs/decisions/` convention established cleanly. Invariant 7 risk caught + mitigated. Sonnet at ~44% of average Opus cost validates the model-routing rule for research-style tasks.

Sprint-07 Stage A one of three complete. Jobs 0037 (WorldPop flip) and 0040 (SFINCS infra) remain in flight.
