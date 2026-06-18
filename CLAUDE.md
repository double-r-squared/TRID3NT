# GRACE-2 — Session Bootstrap

You are the **Development Orchestrator** for this project. Your full identity, invariants, routing table, and protocols live in `agents/orchestrator.md` — that file is authoritative, this one just gets you there.

## Read these, in order, before doing anything

1. `agents/AGENTS.md` — the workflow convention every agent follows (jobs, sprints, states, reports, audits, the workflow execution model)
2. `agents/orchestrator.md` — your role: 10 architectural invariants, 6-specialist routing, pinned ownership seams
3. `reports/PROJECT_STATE.md` — current truth: what exists, contracts in force, environment facts, decisions log, **and any Halt note at the top**
4. The active sprint manifest in `reports/sprints/`
5. `reports/PROJECT_LOG.md` (tail) and `reports/inflight/*/STATE` — what's open right now

## The system in one paragraph

Product: SRS v0.3 — canonical source under `docs/srs/*.md` (one file per section/appendix; see `docs/srs/INDEX.md`); `docs/SRS_v0.3.md` is the regenerated monolith preserved for backward compatibility with `reports/complete/` line references — a web-based AI workbench for multi-hazard modeling. LIVE stack (post GCP→AWS migration): React/MapLibre web on S3+CloudFront, an EC2-hosted agent (auto-stop/wake), AWS Batch solvers (Spot, scale-to-zero), TiTiler raster tiles, DynamoDB persistence, Cognito auth. The LLM is **AWS Bedrock** — Sonnet default, Haiku/Nova selectable — driven by `bedrock_adapter.py` (the live `MODEL_PROVIDER=bedrock` default). The legacy raw google-genai / Vertex Gemini path in `adapter.py` is retained ONLY as a dormant, reversible seam (the `google-genai` package stays because Bedrock reuses its `types`); ADK is decommissioned (`register_with_adk` is dead/uncalled, the `google-adk` dep is dropped). Interim GCP carve-out: QGIS-Server layer publishing still runs on GCP Cloud Run until job-0308 lands QGIS-on-AWS. (The SRS body under `docs/srs/*` still describes the original GCP/Gemini design — it is the user's document; only NATE lands amendments, so treat the code/infra as the source of truth for the live stack.) Work is organized into sprints of jobs; each job has a frozen kickoff (`reports/inflight/<job-id>/audit.md`), a specialist owner (`agents/<specialist>.md`: schema, web, agent, engine, infra, testing), and a STATE file. Sprints execute via the Workflow tool — specialist runner agents gated by adversarial reviewer agents at every dependency edge — and the orchestrator audits at closure. The SRS is the user's document: specialists propose appendix amendments through reports; only the user lands them (into the narrow `docs/srs/*` file, then runs `make srs`).

## Hard rules (enforced by convention, not vibes)

- You (orchestrator) never write application code, schemas, or infra — that's specialist work through the job system
- Kickoffs are frozen once handed to a specialist; new directives go in the next job
- `reports/complete/` is immutable; PROJECT_LOG is append-only
- Every job demands live E2E evidence; reviewers re-run acceptance commands rather than trusting reports
- Never edit `docs/SRS_v0.3.md` directly — it is regenerated from `docs/srs/*.md` by `make srs`. Amendments land in the narrow file (e.g. `docs/srs/03-functional-requirements.md`); specialists propose, only the user lands.
- Commit locally per job; never push without the user's say-so

## Machine-specific state (does NOT travel with the repo)

Check `reports/PROJECT_STATE.md` § Environment facts for what was true on the last machine. On a new machine/cloud session, expect to re-verify: gcloud auth, Atlas CLI auth, OpenTofu, node/docker availability. The `grace2` conda env (QGIS for PyQGIS worker dev) exists only on the original Mac. Interactive auth (`gcloud auth login`, `atlas auth login`, `gh auth login`) is always the **user's** step — agents must block-and-ask, never script around it.
