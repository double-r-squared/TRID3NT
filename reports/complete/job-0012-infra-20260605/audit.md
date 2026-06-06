# Audit: Repo realignment — delete v0.2 artifacts, v0.3 layout, git init + MIT license

**Job ID:** job-0012-infra-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** approved

## Task Assignment

**Specialist:** infra
**Prerequisites:** none — foundation job. Read PROJECT_STATE.md "Dead artifacts" list first.
**SRS references:** NFR-L-1 (OSI license at root, GitHub-detectable), NFR-L-2, Decision E context; user decisions 2026-06-05: git init + GitHub remote, MIT license.

### Scope

1. **Delete v0.2 artifacts** (*remove don't shim* — gone, not archived): `src/grace2_contracts/`, `src/grace2_agent/`, `plugin/`, `tests/contracts/`, `docs/contracts/`, plugin-shaped `Makefile`, `pyproject.toml`, `environment.yml`, `README.md` content, `src/grace2.egg-info`. The `grace2` conda env itself is KEPT (PyQGIS worker dev; strip note goes in the new env docs, actual env rework happens when workers land).
2. **v0.3 layout** (TENTATIVE orchestrator default — push back if unsound): `web/` (React client, empty + README stub), `services/agent/` (ADK service), `services/workers/` (PyQGIS worker + solver container code), `packages/contracts/` (do NOT create — job-0013 owns it), `infra/` (Terraform), `styles/` (QML presets), `public_hazard_catalog.yaml` placeholder NOT created (engine authors it later). Root: new `Makefile` (targets stubbed: `run-agent`, `run-web`, `test`), `.gitignore` (python, node, terraform, conda, .env, GCS keys), `README.md` (project one-liner, v0.3 architecture sketch, dev setup pointers).
3. **git init + initial commit** (user-approved 2026-06-05): `git init`, MIT `LICENSE` at root (copyright Nathaniel J Almanza 2026), commit everything that survives (docs/, agents/, reports/, new scaffold). Connecting the GitHub remote is the **user's step** — list the exact `gh repo create`/`git remote add` commands in your report for them.

### File ownership (exclusive)
Everything above; NOT `packages/contracts/` (0013), NOT `reports/` content beyond your own job files.

### Cross-cutting principles in force
*Remove don't shim*, *no legacy support pre-MVP*, *live E2E validation required*, *surface uncertainty*.

### Acceptance criteria (reviewer re-runs)
- `ls src plugin 2>&1` shows they're gone; `git -C . log --oneline` shows the initial commit; `LICENSE` is MIT and at root
- `grep -ri "strands\|bedrock\|grace2_plugin\|QtWebSockets" --include="*.py" --include="*.md" --exclude-dir=reports --exclude-dir=docs .` finds nothing outside `reports/` history and the SRS
- New layout dirs exist with README stubs; `make test` runs (zero tests OK)
- Report lists the layout as environment facts for PROJECT_STATE.md and the user's GitHub-remote commands

Surface contestable choices (layout names, license header form) as Open Questions with TENTATIVE tags.

## Assessment

Job-0012 is a clean scaffold/hygiene root-commit (`6fd37e6`, 43 files, +4893): v0.2 artifacts deleted, v0.3 layout seeded, git initialized, MIT `LICENSE` landed. All six ACs pass on live re-run; no invariant violations; two structural invariants (#4, #5) seeded verbatim by README copy, the remaining seven correctly n/a pending downstream jobs.

## Invariant Check

- **Determinism boundary:** n/a — no AssessmentEnvelope or agent runtime in tree (`services/agent/` is README-only); deferred to job-0015.
- **Deterministic workflows:** n/a — no workflow Python or atomic tools committed (`services/workers/README.md:20` defers to worker/solver jobs).
- **Engine registration, not modification:** n/a — no engine registry; `public_hazard_catalog.yaml` correctly absent (find returned zero hits).
- **Rendering through QGIS Server:** pass — `services/workers/README.md:7-10` pins `.qgs` read/mutate/write-back via PyQGIS worker as the only mutator; `styles/README.md:6-11` pins QML presets baked into the QGIS Server image.
- **Tier separation:** pass — `web/README.md:8-13` pins client → QGIS Server (WMS/WMTS/WFS) or agent-served GeoJSON only, never direct GCS; root `README.md:17-33` mirrors at architecture-sketch altitude.
- **Metadata-payload pattern:** n/a — no Mongo/GCS driver code; root README mentions Atlas + MCP only at architecture-sketch altitude (`README.md:30-32`).
- **Claims carry provenance:** n/a — no `ClaimSet`/`NumericClaim` code or hazard-event pipeline present.
- **Cancellation is first-class:** n/a — no WS server or Cloud Workflows defs; `infra/README.md:15` documents future cancel-path obligation only.
- **Confirmation before consequence — and no cost theater:** pass — grep for `cost|usd|cents` across scaffold yielded one hit, `infra/README.md:23` invoking NFR-C-1 idle-cost ceiling (`docs/SRS_v0.3.md:804`), infrastructure-side and not user-facing cost theater.
- **Minimal parameter surface:** n/a — no workflow signatures or tool parameters; `Makefile` targets are zero-arg stubs (`Makefile:33-46`).

## Dependency Check

- **Prerequisites satisfied:** yes — Stage-A foundation job, no upstream dependencies; staged only owned paths per kickoff (43 files in `web/`, `services/{agent,workers}/`, `infra/`, `styles/`, `tests/`, root `Makefile`, `.gitignore`, `README.md`, `LICENSE`, plus `docs/` and `agents/`).
- **Downstream impacts:**
  - job-0013 (schema): finish-and-verify — owns `packages/contracts/`, which is correctly untracked by 0012. Routing: schema.
  - job-0014 (infra GCP + Atlas bootstrap): gated on the two user auth checkpoints. Routing: infra.
  - job-0015 (agent service skeleton in `services/agent/`). Routing: agent.
  - job-0016 (web stub in `web/`). Routing: web.
  - job-0017 (acceptance suite in `tests/`). Routing: testing.
  - PROJECT_STATE refresh (layout promotion, git/license facts, GitHub remote, Atlas tier revision). Routing: orchestrator (this audit's close action).

## Decisions Validated

- **Nested `services/{agent,workers}/`** (vs flat top-level): agree — matches kickoff path spellings, PROJECT_STATE tentative layout, and Tier-separation semantics (deployable Cloud Run services group; `web/`, `infra/`, `styles/`, `tests/` are not services). TENTATIVE promoted to fact.
- **Canonical MIT template** (`Copyright (c) 2026 Nathaniel J. Almanza` with middle-initial period and `(c)`): agree — NFR-L-1 requires GitHub-licensee-detectable text; canonical template is what the detector matches. Kickoff phrasing was informal, not a typographic spec.
- **Staged only owned paths; left `packages/` untracked for job-0013:** agree — matches AGENTS.md concurrency rule (disjoint file ownership for parallel jobs) and the explicit kickoff directive. Rejecting the `git add -A` + `git reset packages/` pattern was correct (order-dependent, fragile).
- **Root commit `6fd37e6` carries the realignment payload:** agree — message accurately summarizes the delete/layout/license/git-init scope. Subsequent commits (`a424c9d`, `da85c9a`, `175b674`) are orchestrator/state housekeeping that do not invalidate the AC.

## Open Questions Resolved

- **OQ-A** (nested vs flat `services/`): resolved — keep nested `services/{agent,workers}/`. TENTATIVE → confirmed fact in PROJECT_STATE.
- **OQ-B** (LICENSE copyright phrasing): resolved — keep canonical `Copyright (c) 2026 Nathaniel J. Almanza`. NFR-L-1 detection is the load-bearing requirement, not the chat-typed phrasing.
- **OQ-C** (banned-vocabulary grep hit at `agents/web.md:89`): resolved — accept the single hit; do not add `agents/` to exclude-set. The line is governance-by-naming (forbidding the term requires writing it); entire app tree is clean. Documented here so future jobs are not surprised.
- **OQ-D** (staging discipline: stage only owned paths, leave `packages/` for job-0013): resolved — confirmed as intended sequencing per AGENTS.md concurrency rule and explicit kickoff directive.
- **No-secrets check (qualified evidence):** resolved as pass — original grep's one line was a false positive from the commit-message body; refined `git show --name-only --pretty=format:` rerun confirmed zero secret-file matches in tree. `.gitignore:83-94` properly excludes `.env*`, `*.pem`, `*.key`, GCP service-account keys; `:44-48` excludes `*.tfstate` / `*.tfvars`.

## Follow-up Actions

- **Update `reports/PROJECT_STATE.md`** (this audit closure):
  - promote tentative layout line to fact: `web/` · `services/agent/` · `services/workers/` · `packages/contracts/` (job-0013) · `infra/` · `styles/` · `tests/` + root `Makefile`/`.gitignore`/`README.md`/`LICENSE`
  - flip "This directory is not a git repository" → "git repo on `main`, root-commit `6fd37e6`, MIT LICENSE at root, remote `github.com/double-r-squared/GRACE-2`"
  - clear "No git repo / no license file" known-issue line
  - refresh Environment facts for this machine (Debian 13, Node 20, gcloud + atlas + tofu installed, no `grace2` conda env yet)
  - record revised Atlas tier decision: **Atlas Flex for pre-MVP, M10 by milestone M10** (supersedes the 2026-06-05 "Atlas M0" entry; surface as decision-change to user)
  - record Atlas pre-flight inventory: org `6a234700a0e1295958d10c99`, project `6a234700a0e1295958d10cf9` (`grace-2`), cluster `grace-2-dev` (Flex, GCP us-central1, 8.0.24, IDLE)
  - update Next-up: Stage A finish (audit + job-0013 finish-and-verify) → Stage B (job-0014 with user auth checkpoints)
  - Routing: orchestrator. Priority: high.
- **Close job-0012**: `mv reports/inflight/job-0012-infra-20260605/ reports/complete/`, append `PROJECT_LOG.md` line `2026-06-05 | job-0012-infra-20260605 | repo realignment — v0.2 delete, v0.3 layout, git init + MIT license | approved [revisions: 0]`, update sprint-03 manifest job-status column.
  - Routing: orchestrator. Priority: high.
- **Finish-and-verify job-0013 (schema)**: 10 contract modules already written under `packages/contracts/src/grace2_contracts/`; finish tests, `export_schemas` output, report, and commit the `packages/` tree. NOT a redo. The `research_mode` Appendix-A amendment proposal and OQ-7 embedding-dimension question are required report deliverables.
  - Routing: schema. Priority: high. Held pending user go.
- **Launch Stage B after 0012 + 0013 close**: job-0014 (infra GCP + Atlas) → job-0015 (agent skeleton) ∥ job-0016 (web stub) → job-0017 (acceptance). Each kickoff inherits the OpenTofu decision, the staging-only-owned-paths rule, the revised Atlas Flex decision, and the canonical license/layout facts now in PROJECT_STATE.
  - Routing: orchestrator. Priority: medium.
- **SRS-amendment-proposal to surface** (carried by orchestrator at next user touchpoint): SRS NFR-C-1 (`<$100/month idle` for M10 cluster) is numerically inaccurate — real M10 3-node replica set on GCP is ~$170/month base + backup. Schema or infra to draft the appendix amendment; user lands it.
  - Routing: orchestrator (surface) → schema or infra (draft). Priority: medium.
- **Watch-item** (not yet a job): at first post-0012 edit to `agents/*.md`, sweep for v0.2 vocabulary beyond the documented governance line at `agents/web.md:89`.
  - Routing: orchestrator. Priority: low.

## Sign-off

- **Ready to move to complete:** yes
- All six ACs pass on live re-run (AC1a, AC1b, AC1c, AC2, AC3a, AC3b); no-secrets check qualified-pass (false positive from commit-message body; refined rerun confirms zero secret files in tree).
- Invariants #4 and #5 structurally seeded verbatim; #9 passes (no cost theater); #1, #2, #3, #6, #7, #8, #10 correctly n/a — relevant runtime surfaces deferred to downstream jobs.
- All four open questions resolved (OQ-A, OQ-B, OQ-C, OQ-D); zero escalations from this audit. One **decision-change surface** to user: revising the Atlas tier choice from M0 to Flex (driven by 0.5 GB cap, Vector Search "test-only" classification, programmatic-index-management gap) — orchestrator carries this in the PROJECT_STATE refresh and the user is the system of record.
- Revisions: 0.
