# Project State

**Last updated:** 2026-06-05 (v0.3 realignment complete; sprint-03 scaffolded, execution pending go)
**Current sprint:** sprint-03 (active — jobs 0012–0017 scaffolded, not yet executing)

## Tentative repo layout (job-0012 confirms; becomes fact when it lands)

`web/` (React+MapLibre) · `services/agent/` (ADK) · `services/workers/` (PyQGIS + solver) · `packages/contracts/` (pydantic v2, schema-owned) · `infra/` (Terraform) · `styles/` (QML, engine-authored, infra-baked) · `public_hazard_catalog.yaml` at root (engine-curated) · `tests/` (testing-owned)

## What exists

- `docs/SRS_v0.3.md` — **SRS v0.3.12** (2026-06-04), supersedes v0.2. The authority for scope. Product: a **web-based AI workbench** for multi-hazard modeling and discovery — React/MapLibre client, Google ADK + Gemini 3 agent on Cloud Run, QGIS Server rendering `.qgs` via WMS/WMTS/WFS, PyQGIS workers (Cloud Run Jobs), MongoDB Atlas + MCP, Cloud Workflows + GCS, SFINCS flood engine, Hazard Event Pipeline with ClaimSet provenance, Public Hazard Catalog discovery mode. **Appendices A–D are preemptive contract specs** (WS protocol, AssessmentEnvelope, EventMetadata/ClaimSet, MongoDB collections) — schema implements from them; amendments flow back to the user.
- `agents/` — workflow convention (`AGENTS.md`), orchestrator definition (10 invariants from Decisions A–M), six specialist definitions: `schema`, `web`, `agent`, `engine`, `infra`, `testing` (v0.3 redraft in progress as of this writing).
- `reports/` — sprints 01 and 02 aborted (SRS pivots; retrospectives record salvage); `inflight/` empty; job counter at 11 (next: 0012).
- **Dead artifacts from v0.2 awaiting cleanup** (sprint-03 repo-realignment job deletes them): `src/grace2_contracts/`, `src/grace2_agent/`, `plugin/grace2_plugin/`, plugin-shaped `Makefile`/`pyproject.toml`/`README.md`/`environment.yml`, `tests/contracts/`, `docs/contracts/`. Do not build on these.
- **No v0.3 application code exists yet.**

## Contracts in force

None in code yet. **SRS Appendices A–D are the contract stubs of record** — any agent needing a message shape, envelope, claim type, or collection schema reads the appendix, not the dead `src/grace2_contracts/`. First schema job turns them into code.

## Environment facts

- Machine: macOS (Darwin 25.4.0), Apple Silicon, Homebrew at `/opt/homebrew`
- **Node v24.14.0 + npm present** (web client dev ready); **Docker 29.4.0 present** (container builds ready)
- **NO `gcloud` CLI, NO `terraform` installed** — infra bootstrap job must install both; `gcloud auth login` is an interactive user step
- **No GCP project, no MongoDB Atlas cluster yet** — user must create/designate both (sprint-03 blocker; see Next up)
- `grace2` conda env at `~/miniforge3/envs/grace2`: QGIS 3.40.3-Bratislava verified — **kept, repurposed for local PyQGIS worker dev** (FR-QS-6); strands/boto3 in it are dead weight, strip during env rework
- AWS account `226996537797` (user `nate`) exists but is **no longer relevant** — v0.3 is GCP-only (Decision E)
- Ollama + `llama3.2:3b` present but **no longer relevant** — v0.3 is Gemini 3 only (FR-AS-1)
- This directory is **not** a git repository — NFR-L-1 (license at repo root, GitHub-detectable) implies GitHub; `git init` is now on the critical path. User decision pending.

## Decisions log

| Date | Decision | Decided by | Rationale |
|------|----------|-----------|-----------|
| 2026-06-05 | SRS v0.3 pivot: web client, GCP/ADK/Gemini 3, QGIS Server, MongoDB Atlas+MCP, 2-layer tools, HEP with claims, discovery mode | user (SRS §2.1 Decisions A–M) | More tractable; no desktop install; AI as the GIS abstraction |
| 2026-06-05 | New dedicated GCP project; Atlas free-tier M0; git init + GitHub remote + MIT license | user | Sprint-03 foundation choices |
| 2026-06-05 | `research_mode` field on `user-message` is the FR-WC-15 toggle carrier (Appendix A amendment, schema proposes in job-0013) | orchestrator | Pins the web→agent→engine strategy path before anyone invents a second one |
| 2026-06-05 | OpenTofu (MPL-2.0) as the IaC tool, not BUSL Terraform | orchestrator + user | Terraform left homebrew-core after BUSL relicense; OpenTofu is drop-in; NFR-PO-3 says "or equivalent"; all-OSI tooling matches NFR-L posture |
| 2026-06-05 | Roster stays six; `plugin` → `web`; `engine` keeps the whole tool body incl. PyQGIS worker code | orchestrator | v0.3 surfaces map 1:1 onto existing roster; avoids fragmentation per user's standing guidance |
| 2026-06-04 | pydantic v2 for contracts | SRS-anchored (Appendix D) | Was tentative; now codified in the SRS itself |
| 2026-06-04 | Specialist roster consolidated to six | user + orchestrator | Avoid fragmenting work |
| 2026-06-04 | Sprint scaffolding stays lightweight until an SRS revision survives a sprint | orchestrator (retrospectives 01, 02) | Two pivots in two days; cheap-abort discipline validated |

## Known issues / debt

- SRS v0.3 §6 Open Questions 1–7 unresolved. Surfacing owners: OQ-1 agent (Cloud Run WS vs Agent Engine — needed before M2), OQ-2 infra (MCP hosting), OQ-3 engine (news API mix), OQ-4 engine (HydroMT depth), OQ-5 engine (forcing cache design, due M4/M5), OQ-6 engine (pre-baked demos), OQ-7 schema/infra (embedding dimension, verify before locking Atlas index).
- v0.2 dead artifacts on disk (list above) until the sprint-03 cleanup job lands.
- No git repo / no license file yet (NFR-L-1 fails until repo hygiene job).

## Next up

**Sprint-03 scaffolded (jobs 0012–0017), execution awaiting go.** Stage A (repo realignment ∥ contracts) is fully local. Stage B (job-0014, GCP + Atlas) has two **user auth checkpoints**: `! gcloud auth login` and `! atlas auth login` after the toolchain installs — the job blocks with instructions if unauthenticated, and resumes after. Then agent (0015) → web stub (0016) → acceptance (0017).
