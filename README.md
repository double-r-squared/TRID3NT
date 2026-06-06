# GRACE-2 — Hazard Modeling Agent

A web-based AI workbench for multi-hazard modeling and discovery. You describe a
hazard scenario in natural language; the agent fetches authoritative data, sets
up and runs physics-based solvers, sources real-world hazard events with
provenance-tracked claims, and renders the results on an interactive map — no
GIS expertise or desktop software required.

See [`docs/srs/INDEX.md`](docs/srs/INDEX.md) for the section-addressed canonical SRS (or [`docs/SRS_v0.3.md`](docs/SRS_v0.3.md) for the regenerated monolith) — the full specification (currently
SRS v0.3.12).

## Architecture (SRS v0.3)

A browser client talks to a cloud agent; the agent orchestrates rendering,
solver, and data services; MongoDB Atlas is the durable knowledge layer.

```
 Browser (React + MapLibre GL JS)
   │  WebSocket (chat, tool calls, pipeline)   │  HTTPS (WMS/WMTS/WFS tiles)
   ▼                                           ▼
 Agent service ────────────────────────►  QGIS Server
 (Cloud Run, Google ADK + Gemini 3)        (Cloud Run, renders .qgs as OGC services)
   │  invokes via Cloud Workflows                  ▲ reads
   ▼                                               │
 Worker pool (Cloud Run Jobs)              GCS buckets
  - PyQGIS workers (mutate .qgs)  ────────►  (.qgs, COG, FlatGeobuf, QML)
  - SFINCS solver containers
  - news fetchers / event extractors
   │
   ▼
 MongoDB Atlas (news corpus, events, run catalog, embeddings, Vector Search)
   └── MongoDB MCP server ◄── agent connects here for database tools
```

Key properties (the architectural invariants): the LLM plans and narrates but
never produces numbers (the determinism boundary); all map rendering flows
through QGIS Server and `.qgs` is mutated only by PyQGIS workers; the browser
never reads GCS directly; MongoDB is the only discovery path; every numerical
claim about a hazard event carries per-source provenance; long-running runs are
cancellable end-to-end within 30 seconds.

## Repository layout

```
web/                  React + MapLibre web client                  (web)
services/agent/       ADK + Gemini 3 agent service (Cloud Run)      (agent)
services/workers/     PyQGIS workers + SFINCS solver (Cloud Run Jobs) (engine code, infra image)
packages/contracts/   pydantic v2 shared contracts                  (schema — lands in job-0013)
infra/                OpenTofu IaC for the GCP substrate            (infra)
styles/               QML style presets                            (engine content, infra-baked)
tests/                acceptance + conformance suites               (testing)
docs/                 SRS and design docs
agents/               development-workflow convention + agent roles
reports/              workflow reports (orchestrator + specialists)
```

Each top-level component directory has a `README.md` naming its owning
specialist and what lands there.

## Development setup

This repo is in early scaffold. Component-level dev instructions arrive with each
component (web in job-0016, agent in job-0015, infra toolchain + GCP project in
job-0014).

**Toolchain (this machine, verified 2026-06-05):**

- **Node v24** + npm — web client dev (`web/`).
- **Docker 29** — container image builds (agent, QGIS Server, workers).
- **OpenTofu `tofu` v1.12.1** — IaC (`infra/`). The MPL-2.0 fork of Terraform;
  chosen over BUSL Terraform (NFR-PO-3 permits "or equivalent", and all-OSI
  tooling matches the project's NFR-L licensing posture).
- **gcloud CLI** — authenticated; the GCP project is provisioned in job-0014.
  `gcloud auth login` is an **interactive user step** — never scripted.
- **`grace2` conda env** (QGIS 3.40.3) — the **local PyQGIS-worker dev
  environment only** (FR-QS-6). The agent service and QGIS Server ship as their
  own containers; this env is not how they run. Dead pre-pivot dependencies
  (the former AWS/agent-provider SDKs) are stripped when the env's definition is
  reworked alongside the first worker job.

**Make targets** (scaffold stubs today; wired as components land):

```bash
make help        # list targets
make run-agent   # launch the local agent service        (wired in job-0015)
make run-web     # launch the local web client dev server (wired in job-0016)
make test        # run acceptance + conformance suites    (wired in job-0017)
```

## License

[MIT](LICENSE) © 2026 Nathaniel J. Almanza.
