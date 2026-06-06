# Audit: Repo realignment — delete v0.2 artifacts, v0.3 layout, git init + MIT license

**Job ID:** job-0012-infra-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** assigned

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

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
