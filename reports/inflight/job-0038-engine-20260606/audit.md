# Audit: OQ-4 HydroMT integration depth decision (research → decision doc)

**Job ID:** job-0038-engine-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** assigned

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
