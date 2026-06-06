# Report: Repo realignment — delete v0.2 artifacts, v0.3 layout, git init + MIT license

**Job ID:** job-0012-infra-20260605
**Sprint:** sprint-03
**Specialist:** infra
**Task:** Delete v0.2 artifacts; lay out the v0.3 repo (`web/`, `services/agent/`, `services/workers/`, `infra/`, `styles/`, root `Makefile`/`.gitignore`/`README.md`); `git init` + MIT `LICENSE` at root + initial commit. (Verbatim scope in `audit.md` § Task Assignment.)
**Status:** ready-for-audit

## Summary

Deleted all v0.2 (Strands/Bedrock/QGIS-plugin) artifacts, established the SRS-v0.3 web-workbench directory layout with owner-naming README stubs, wrote v0.3 root scaffold (`Makefile`, `.gitignore`, `README.md`), added a GitHub-detectable MIT `LICENSE`, and `git init`'d the repository with a single scoped initial commit (`6fd37e6`). All five acceptance criteria pass on live re-run. The initial commit deliberately stages only job-0012-owned paths plus the surviving `docs/`/`agents/`/`reports/`, excluding `packages/` because job-0013 (schema) is mid-write there in parallel.

## Changes Made

### Deletions (v0.2 artifacts — *remove don't shim*, *no legacy support pre-MVP*)
- Deleted `src/` (`grace2_contracts/`, `grace2_agent/`, `grace2.egg-info/`) — v0.2 Strands/Bedrock contracts + agent.
- Deleted `plugin/grace2_plugin/` — v0.2 QGIS Desktop plugin (Decision A retires it).
- Deleted `tests/contracts/` — v0.2 contract test suite (job-0017 lands the v0.3 suite).
- Deleted `docs/contracts/` — eight v0.2 JSON-schema files (`*.schema.json`).
- Deleted root `Makefile`, `pyproject.toml`, `environment.yml` — plugin/conda-shaped v0.2 config.
- Deleted stale `.pytest_cache/`.
- Replaced v0.2 `README.md` content and rewrote `.gitignore`.

### New v0.3 layout (each dir + an owner-naming README stub)
- `web/README.md` — React + MapLibre client (owner: `web`).
- `services/agent/README.md` — ADK + Gemini 3 agent service (owner: `agent`; container/deploy: `infra`).
- `services/workers/README.md` — PyQGIS workers + SFINCS solver (code: `engine`; image/Jobs: `infra`).
- `infra/README.md` — OpenTofu IaC for the GCP substrate (owner: `infra`).
- `styles/README.md` — QML presets (content: `engine`; baked into QGIS Server image: `infra`).
- `tests/README.md` — acceptance/conformance suites (owner: `testing`; CI plumbing: `infra`).
- **Not created:** `packages/contracts/` (job-0013/schema owns), `public_hazard_catalog.yaml` (engine authors later) — per kickoff.

### Root scaffold (v0.3)
- `Makefile` — stub targets `run-agent` (-> job-0015), `run-web` (-> job-0016), `test` (-> job-0017/0013); `make test` exits 0 with zero suites. `grace2` conda env documented as local-PyQGIS-worker-dev-only.
- `.gitignore` — v0.3 stack: Python, Node, **OpenTofu/Terraform** (`.terraform/`, `*.tfstate`, `*.tfvars`), conda, `.env`, and an explicit GCP-credential block (`*-key.json`, `service-account*.json`, `application_default_credentials.json`, `.gcloud/`). Dropped the v0.2 AWS-creds block.
- `README.md` — project one-liner, v0.3 architecture sketch (browser -> agent -> QGIS Server/workers -> GCS/Atlas+MCP), repo-layout table, toolchain facts (Node 24, Docker 29, OpenTofu 1.12.1, gcloud, grace2 env), make targets, MIT license pointer.
- `LICENSE` — canonical MIT text, first line `MIT License` (GitHub license-detection key), `Copyright (c) 2026 Nathaniel J. Almanza`.

### git
- `git init -b main`; root-commit `6fd37e6` staging 43 files (owned scaffold + `docs/`/`agents/`/`reports/`). `packages/` intentionally left untracked (job-0013 mid-write). Commit message namespaced `job-0012:`; co-author trailer present. **Not pushed** (per protocol); GitHub remote is the user's step (commands below).

## Decisions Made

- **Decision:** Stage paths explicitly by name at the initial commit (not `git add -A`/`.`).
  - **Rationale:** Kickoff states "job-0012 itself stages only its owned paths (a parallel job may be mid-write elsewhere)." Confirmed live: `reports/inflight/job-0013-schema-20260605/STATE` is `in-progress` and `packages/contracts/src/grace2_contracts/*.py` were being written during this job. A blanket add would have swept job-0013's partial work into job-0012's commit, creating a cross-job partial-commit race.
  - **Alternatives considered:** `git add -A` then `git reset packages/` (more fragile — order-dependent, easy to miss new paths); a `.gitignore` entry for `packages/` (wrong — it's a tracked dir, just not *this job's* to commit).

- **Decision:** `tests/` kept as a directory with a README stub (not deleted with `tests/contracts/`).
  - **Rationale:** The v0.3 layout has a top-level `tests/` (testing-owned, per PROJECT_STATE layout line). Only the v0.2 `tests/contracts/` content is dead.
  - **Alternatives considered:** Delete `tests/` entirely and let job-0017 recreate it — rejected; the layout names it now and an empty owned dir with a README is the established pattern here.

- **Decision:** `Makefile` `test` target is a clean-exit echo stub, not `pytest`.
  - **Rationale:** No test suites exist yet (job-0013 contracts tests, job-0017 acceptance). `pytest` with no tests and no installed package would either error (no pytest in PATH) or print a collected-0 line that depends on environment. A pinned echo gives reviewers a deterministic green `make test` (RC=0) regardless of local env, satisfying AC "make test runs (zero tests OK)." Wired to real commands as components land.
  - **Alternatives considered:** `conda run -n grace2 pytest` (couples a scaffold smoke-check to the conda env and to packages that don't exist yet); `pytest || true` (hides real failures once suites exist).

- **Decision:** README documents OpenTofu (`tofu`) as the IaC tool, gcloud auth as a user step, and the `grace2` env as local-worker-dev-only.
  - **Rationale:** Carries the PROJECT_STATE 2026-06-05 OpenTofu decision and the infra domain discipline (gcloud auth login is the user's interactive step) into the repo's front door so downstream infra jobs inherit it.

## Invariants Touched

This is a scaffold/hygiene job; it provisions no runtime resources, so no invariant is exercised at runtime. Two are *structurally seeded* by the layout and docs:
- **4. Rendering through QGIS Server / nothing else writes `.qgs`:** `services/workers/README.md` and `styles/README.md` codify that `.qgs` is mutated only by PyQGIS workers and that QML presets are engine-authored / infra-baked into QGIS Server — preserved (documented, not yet enforced by code).
- **5. Tier separation:** `web/README.md` codifies that the client reaches Tier B only via QGIS Server (WMS/WMTS/WFS) or agent GeoJSON, never GCS directly — preserved (documented).
- No invariant is risked: no secrets committed, no bucket-enumeration path, no cost surface, no `.qgs`-writing path created.

## Open Questions

- **OQ-A [TENTATIVE — layout naming]:** I adopted the orchestrator's TENTATIVE default layout verbatim (`web/`, `services/agent/`, `services/workers/`, `packages/contracts/`, `infra/`, `styles/`). One contestable point: **`services/agent/` + `services/workers/` under a shared `services/` parent vs. flat top-level `agent/` + `workers/`.** I went with the nested `services/` grouping (matches the kickoff's path spellings exactly and reads well as "deployable services"). Reasonable alternative: flat top-level dirs to match `web/`/`infra/`/`styles/` siblings. *Recommendation:* keep `services/` (TENTATIVE — confirm; cheap to change now, costly once `agent`/`engine` import paths exist). No SRS section dictates this; it derives from PROJECT_STATE's "Tentative repo layout" line.

- **OQ-B [TENTATIVE — license header form]:** `LICENSE` copyright reads `Copyright (c) 2026 Nathaniel J. Almanza` (with middle-initial period and "(c)"). Kickoff said "copyright Nathaniel J Almanza 2026." I used the canonical MIT phrasing (`Copyright (c) <year> <name>`) because GitHub's license detector and `licensee` match against exactly that template; deviating risks a non-detected license (NFR-L-1). *Recommendation:* keep canonical form (TENTATIVE — confirm the name spelling/punctuation is acceptable; trivial to amend before push).

- **OQ-C [non-blocking — grep residue, informational]:** AC2's grep (`strands|bedrock|grace2_plugin|QtWebSockets`, excluding `reports/`+`docs/`) returns exactly one hit: `agents/web.md:89`, the banned-vocabulary governance line that *names* the forbidden terms in order to forbid them (correct v0.3 usage). It is outside infra's file-ownership (an `agent`-roster doc) and is not a v0.2 artifact. The AC excludes `docs/` and `reports/` but not `agents/`; I read the AC's intent as "no live v0.2 vocabulary in app/build code," which is satisfied — the app tree (`web/services/infra/styles/tests/` + root scaffold) is fully clean. *Flagged so the reviewer isn't surprised by the single grep line.* No action proposed unless the orchestrator wants the AC's exclude-set to add `agents/`.

- **OQ-D [non-blocking — packages/ untracked at commit, informational]:** The initial commit excludes `packages/` (job-0013's in-flight code). That directory therefore is **not** in `6fd37e6`; job-0013 commits it when it lands (per the kickoff's "commit your job's changes" rule for post-0012 jobs). If the orchestrator expects a single all-encompassing first commit, that conflicts with the parallel-write reality — I prioritized the kickoff's explicit "stage only owned paths" instruction. *Recommendation:* leave as-is; this is the intended sequencing.

## Dependencies and Impacts

- **Depends on:** none (foundation job).
- **Affects / unblocks:**
  - `job-0013` (schema): owns `packages/contracts/` — left untracked for it; it commits that tree itself. Its work is unaffected by this commit.
  - `job-0014` (infra, GCP+Atlas): builds in `infra/`; inherits the OpenTofu decision and the gcloud-auth-as-user-step note documented here.
  - `job-0015` (agent): builds in `services/agent/`; `make run-agent` reserved.
  - `job-0016` (web): builds in `web/`; `make run-web` reserved.
  - `job-0017` (testing): builds in `tests/`; `make test` reserved.
- **PROJECT_STATE.md follow-ups for the orchestrator** (I do not edit it):
  - "Tentative repo layout" line can be promoted to fact: layout is now `web/ . services/agent/ . services/workers/ . packages/contracts/ (job-0013) . infra/ . styles/ . tests/`, MIT `LICENSE` at root, repo is a git repo on branch `main` (root-commit `6fd37e6`).
  - Environment facts: this directory **is now a git repository** (was "not a git repository"); branch `main`; no remote yet.
  - "No git repo / no license file" known-issue line can be cleared.

### User's GitHub-remote step (NOT performed — user-only, per kickoff)
Run from the repo root after creating/choosing a GitHub account:
```bash
# Option 1 — create the remote repo and push in one step (requires `gh` authenticated):
gh repo create grace-2 --private --source=. --remote=origin --push

# Option 2 — manual, if the GitHub repo already exists:
git remote add origin git@github.com:<your-user>/grace-2.git
git push -u origin main
```
(`infra` never pushes; pick private vs public per preference — MIT license supports either.)

## Verification

- **Tests run:** `make test` (RC=0, zero suites — expected); `make help`, `make run-agent`, `make run-web` (all RC=0).
- **Live E2E evidence — verbatim transcripts (re-runnable by the reviewer):**

  **AC1a — src/plugin gone:**
  ```
  $ ls src plugin
  ls: plugin: No such file or directory
  ls: src: No such file or directory
  ```

  **AC1b — initial commit present:**
  ```
  $ git -C . log --oneline
  6fd37e6 job-0012: repo realignment — delete v0.2 artifacts, v0.3 layout, MIT license, git init
  ```

  **AC1c — MIT LICENSE at root:**
  ```
  $ head -1 LICENSE
  MIT License
  $ grep -i copyright LICENSE | head -1
  Copyright (c) 2026 Nathaniel J. Almanza
  ```

  **AC2 — v0.2 vocabulary grep (excl reports/, docs/):**
  ```
  $ grep -rin "strands\|bedrock\|grace2_plugin\|QtWebSockets" \
      --include='*.py' --include='*.md' --exclude-dir=reports --exclude-dir=docs .
  agents/web.md:89:- Banned-vocabulary check: no QGIS Desktop plugin, dockable panel, `iface`, QtWebSockets, Strands/Bedrock/...
  ```
  (Single hit = the `agents/web.md` banned-vocabulary governance line — see OQ-C. App tree clean.)

  **AC3a — layout dirs + README stubs:**
  ```
  PASS  web/             (README.md, 15 lines)
  PASS  services/agent/  (README.md, 15 lines)
  PASS  services/workers/(README.md, 20 lines)
  PASS  infra/           (README.md, 26 lines)
  PASS  styles/          (README.md, 13 lines)
  PASS  tests/           (README.md, 11 lines)
  ```

  **AC3b — make test runs (zero tests OK):**
  ```
  $ make test
  test: scaffold stub — no test suites present yet.
    packages/contracts tests land in job-0013; acceptance suite in job-0017.
  $ echo $?
  0
  ```

  **Working tree after commit (only job-0013's packages/ untracked):**
  ```
  $ git status --short --untracked-files=normal
  ?? packages/
  ```

- **Results:** **pass.** All five acceptance criteria verified on live re-run. No secrets or junk in the commit (scanned staged set for `.env`/`.pem`/`*key.json`/`*.tfstate`/`*.tfvars`/`__pycache__`/`.DS_Store`/`egg-info` — none). `packages/` correctly excluded from this job's commit.
