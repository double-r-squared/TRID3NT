# Report: grace2 conda env recreation on Linux Debian via conda-forge (QGIS 3.40.3, dead-dep strip)

**Job ID:** job-0022-infra-20260605
**Sprint:** sprint-04
**Specialist:** infra
**Task:** Author `infra/conda/environment.yml` (python=3.12, qgis=3.40.3, google-cloud-storage, google-cloud-pubsub, pip; conda-forge only; dead deps stripped); create the env locally on Debian 13; document Miniforge bootstrap + create/activate + Docker-is-authoritative-runtime decision in `infra/README.md`.
**Status:** ready-for-audit

## Summary

Authored `infra/conda/environment.yml` (conda-forge only; `python=3.12`, `qgis=3.40.3`, `gdal`, `google-cloud-storage`, `google-cloud-pubsub`, `pytest`, `pip`) with a `# Intentionally absent` documentation block enumerating the v0.2-era dead deps that are removed (per AGENTS.md "Remove don't shim" + `agents/infra.md`). Created the env live on this Debian 13 box via Miniforge3, activated it, and verified `Qgis.QGIS_VERSION = 3.40.3-Bratislava`, `google.cloud.storage + google.cloud.pubsub_v1` import cleanly, and `osgeo.gdal == 3.10.2`. Added the "Local PyQGIS dev environment (`grace2` conda env)" section to `infra/README.md` covering Miniforge3 install, env create, activation, and the Docker-is-authoritative-runtime decision (production worker ships via job-0021 container, not this env).

## Changes Made

- File: `infra/conda/environment.yml` (new)
  - Created the env spec under infra ownership. `conda-forge` only — no `defaults`, no `anaconda`. Deps: `python=3.12`, `qgis=3.40.3`, `gdal`, `google-cloud-storage`, `google-cloud-pubsub`, `pytest`, `pip`. Header comment block enumerates the intentionally absent v0.2 deps (boto3 / aws-cli / s3fs; strands; ollama / llama-cpp-python; litellm / anthropic-bedrock) with per-line rationale tied to SRS v0.3 decisions.
- File: `infra/README.md` (additive append)
  - Appended "Local PyQGIS dev environment (`grace2` conda env)" subsection covering: Miniforge3 install one-liner; `mamba env create -f infra/conda/environment.yml` (with `conda env create` fallback); clean-slate recreate snippet; per-session `conda activate grace2` + verification one-liners with expected outputs; what's in the env; what's NOT in the env (dead-dep strip with citations); Docker-is-authoritative-runtime decision (production worker ships as `infra/worker/Dockerfile`, job-0021 — this env is local-iteration convenience, not contract).
- File: `reports/inflight/job-0022-infra-20260605/.history/report.v1.md` (new, archival)
  - Archived the empty v1 template before populating the v2 report (per AGENTS.md § "File Overwrite Rules": archive to `.history/` before overwriting).

No edits to existing `infra/**` files (job-0018 and job-0021 own QGIS Server + worker container infra this sprint). No `services/workers/**` touched. No OpenTofu state touched.

## Decisions Made

- Decision: **Miniforge3, not Mambaforge, not system conda, not Anaconda.**
  - Rationale: Mambaforge is officially deprecated (the conda-forge installer matrix replaced it with Miniforge3 ~mid-2024; Miniforge3 ships `mamba` v2+ by default since 24.x). Miniforge3 is conda-forge-only out of the box (MIT/BSD posture, consistent with NFR-L). Debian 13 has no system `conda` package; installing Anaconda would drag the `anaconda` channel and license terms that the dead-dep-strip principle is meant to avoid.
  - Alternatives considered: Mambaforge (deprecated upstream); system-package conda (does not exist on Debian 13); Anaconda Distribution (license + channel pollution).
- Decision: **Include `pytest` in conda deps**, not deferred to a pip install layer.
  - Rationale: job-0020's worker unit tests use pytest; adding it inside the env spec means `conda activate grace2 && pytest` works without a separate `pip install`. Cost is one extra dep (~2 MB); benefit is no out-of-spec "you also need pytest" footnote in the docs.
  - Alternatives considered: pip-install-on-demand (rejected — would leave the env reproducible-from-yaml only for the runtime path, not the test path).
- Decision: **`gdal` pinned explicit** alongside `qgis=3.40.3`, not left fully transitive.
  - Rationale: QGIS pulls GDAL as a transitive dep, but local scripts that touch `/vsigs/` outside of a `QgsProject` (e.g., raw `gdal_translate` calls during iteration) want a stable explicit handle. The conda-forge solver still picks the QGIS-compatible build (verified: `gdal 3.10.2` co-installed with `qgis 3.40.3`); pinning to a major version was not done to let the solver match QGIS's needs.
  - Alternatives considered: drop the explicit `gdal` line (transitive only — works, but obscures intent); pin `gdal=3.10.*` (rejected — over-constrains the solver against QGIS's pin).
- Decision: **`google-cloud-storage` and `google-cloud-pubsub` left unpinned** (no minor-version pin).
  - Rationale: Vendor SDKs drift fast and the env is for local iteration only — production worker (the container in job-0021) has its own pinning regime. Pinning here without also pinning the worker `requirements.txt` would create two sources of truth. Surfaced as OQ-2 for revisit at M3 when an agent-side consumer of these SDKs lands.
  - Alternatives considered: `google-cloud-storage>=3,<4` + `google-cloud-pubsub>=2,<3` (rejected for now — premature single-version-source-of-truth without the agent-side counterpart existing).
- Decision: **Live E2E evidence recorded in this report, not just in the commit body.**
  - Rationale: Revision-round-1 finding (blocking-severity) noted that v1 left the report empty and put E2E evidence only in the commit message body. Reviewer + orchestrator audit looks in `report.md`. Per AGENTS.md § "What Every Agent Always Does — Before halting any task: Ensure `report.md` reflects current truth", the verbatim transcripts belong in the report's `## Verification` section.
  - Alternatives considered: keep evidence in commit body only (rejected by reviewer); duplicate into both (this is what the revision does — commit body summary stays, full transcripts now live in the report).

## Invariants Touched

- **Invariant 4 — Rendering through QGIS Server:** *preserves* — this env is the substrate for the one writer of `.qgs` (the local PyQGIS worker iteration path). It does not introduce a second rendering path. Production rendering remains QGIS Server in the container (job-0018); this env exists so an engine can edit `services/workers/pyqgis/worker.py` and run it locally before pushing the worker container (job-0021). The env never serves rendered tiles.
- **Invariant 2 — Deterministic workflows:** *preserves* — the worker code this env supports is a pure deterministic Python function (no LLM in the loop). Nothing in the env spec changes that: `google-cloud-storage` + `google-cloud-pubsub` are messaging/IO, not model providers; `pytest` is test infra; `qgis` + `gdal` are deterministic geo libraries. There is no `anthropic`, no `openai`, no `ollama` in the env (see dead-dep strip).

## Open Questions

- **OQ-1: Miniforge3 vs Mambaforge vs system conda for the bootstrap instruction.**
  - Options: (a) Miniforge3 (conda-forge-only, MIT/BSD posture, ships `mamba` v2+); (b) Mambaforge (deprecated upstream — installer redirects to Miniforge3); (c) system conda (does not exist on Debian 13's apt); (d) Anaconda Distribution (drags `anaconda` channel + license terms inconsistent with the dead-dep-strip posture).
  - TENTATIVE: **(a) Miniforge3.** Mambaforge is deprecated; system conda is unavailable; Anaconda violates the channel-purity intent. README documents Miniforge3 as the only path.
  - SRS reference: NFR-PO-3 (IaC posture — reproducible env); NFR-L (license posture).
- **OQ-2: Pin minor versions on `google-cloud-storage` + `google-cloud-pubsub` in the env spec?**
  - Options: (a) leave unpinned (current state — solver picks latest conda-forge build; today resolved to `google-cloud-storage 3.11.0`, `google-cloud-pubsub 2.38.0`); (b) pin `>=3,<4` / `>=2,<3` major-only; (c) full minor pin `==3.11.*` / `==2.38.*`.
  - TENTATIVE: **(a) unpinned for now**, revisit at M3 when the first agent-side consumer of these SDKs lands. Pinning here without an agent-side counterpart creates two sources of truth (local env vs. eventual agent container).
  - SRS reference: NFR-PO-3 (IaC posture); the eventual single-version-source-of-truth across worker + agent containers.
- **OQ-3: Add a `pyproject.toml` for the worker so `python -m services.workers.pyqgis.worker` resolves cleanly without `PYTHONPATH=.`?**
  - Options: (a) defer — `PYTHONPATH=.` from repo root is sufficient for M2 iteration; (b) add a minimal `pyproject.toml` under `services/workers/pyqgis/` with `[tool.setuptools.packages.find]` so the worker is editable-installable into the env; (c) repo-root `pyproject.toml` covering all `services/**`.
  - TENTATIVE: **(a) defer** — `PYTHONPATH=.` invocation from repo root is sufficient for M2. Engine can decide at first sign of import friction when the worker code lands.
  - SRS reference: FR-QS-6 (worker code substrate); engine specialist owns the call.
- **OQ-4: Add `pytest` to the conda deps, or leave it to a pip layer?**
  - Options: (a) include `pytest` in the env spec (current state — `pytest 9.0.3` lands with `conda env create`); (b) leave it out, require `pip install pytest` in each fresh env; (c) `pytest` + `pytest-asyncio` + `pytest-cov` pre-loaded.
  - TENTATIVE: **(a) include `pytest`** — local unit tests run inside the env without `pip install`. Extra plugins added when first test needs them.
  - SRS reference: FR-QS-6 (worker dev loop); aligns with the "env spec is reproducible from yaml alone" principle.
- **OQ-5: Kickoff AC at audit.md line 83 references a non-existent PyQGIS attribute (`QgsApplication.QGIS_VERSION`).**
  - Options: (a) note the documentation gap in this report so the next env-touching job picks up the corrected AC wording; (b) edit the kickoff in place (forbidden — frozen).
  - TENTATIVE: **(a) report-only.** The kickoff AC is satisfied by the supported equivalent (`from qgis.core import Qgis; print(Qgis.QGIS_VERSION)` → `3.40.3-Bratislava`). Verified live below in `## Verification`. `QgsApplication` has no `QGIS_VERSION` attribute in QGIS 3.40 (the attribute lives on the `Qgis` class — confirmed via `getattr(QgsApplication, 'QGIS_VERSION', 'ATTR_MISSING')` → `ATTR_MISSING` in this env). Per AGENTS.md § "Don't edit in-flight kickoffs" the kickoff stays frozen; the next env-job kickoff should use `from qgis.core import Qgis; print(Qgis.QGIS_VERSION)`.
  - SRS reference: Decision C (PyQGIS workers — API surface).

## Dependencies and Impacts

- **Depends on:** job-0012 (infra repo layout established `infra/` ownership); `PROJECT_STATE.md` § "Environment facts" (record that the prior `grace2` env on the Mac box held QGIS 3.40.3-Bratislava — this job recreates that on Debian 13).
- **No upstream dependency on job-0018 or job-0020** — env is for LOCAL worker dev only; production worker ships as its own container in job-0021.
- **Affects:**
  - **Engine specialist (job-0020 + downstream):** the env this job produces is the substrate for the local PyQGIS worker iteration loop. Engine reads `infra/README.md` § "Local PyQGIS dev environment" to bootstrap. If engine hits import friction with `PYTHONPATH=.`, OQ-3 (`pyproject.toml`) is the resolution path.
  - **Infra specialist (job-0021, worker Dockerfile):** the dep set here (`python=3.12`, `qgis=3.40.3`, `gdal`, `google-cloud-storage`, `google-cloud-pubsub`) is the reference for what the worker container must also have. The container is the contract; this env is the iteration substrate.
  - **Testing specialist:** `pytest` is in the env, so worker unit tests run inside `conda activate grace2` with no extra pip step.
  - **Orchestrator:** OQ-5 surfaces a documentation gap in the kickoff AC wording (non-existent `QgsApplication.QGIS_VERSION` attribute). Next env-touching job should be kicked off with `Qgis.QGIS_VERSION` instead.

## Verification

### Tests run

- YAML parse check on `infra/conda/environment.yml`.
- Dead-dep grep on `infra/conda/environment.yml` (must return zero non-comment matches).
- `conda env create -f infra/conda/environment.yml` (idempotent — already created during v1; the env still exists in this revision round and is the artifact under test).
- `conda activate grace2` → `python --version`.
- `python -c "from qgis.core import Qgis; print(Qgis.QGIS_VERSION)"` → expect `3.40.3-*`.
- `python -c "from google.cloud import storage, pubsub_v1; print('ok')"` → expect `ok`.
- `python -c "from osgeo import gdal; print(gdal.__version__)"` → expect `3.10.*`.
- `python -c "import pytest; print(pytest.__version__)"` → expect a 8.x/9.x string.

### Live E2E evidence — verbatim transcripts (Debian 13, Miniforge3, this dev box)

**Conda env list (env exists):**

```
$ conda env list
# conda environments:
#
# * -> active
# + -> frozen
base                     /home/nate/miniforge3
grace2               *   /home/nate/miniforge3/envs/grace2
```

**Conda version:**

```
$ conda --version
conda 26.3.2
$ which conda
/home/nate/miniforge3/condabin/conda
```

**Python version inside the env:**

```
$ conda activate grace2
$ python --version
Python 3.12.13
$ which python
/home/nate/miniforge3/envs/grace2/bin/python
```

**PyQGIS import + version (the kickoff AC, via the supported `Qgis.QGIS_VERSION` attribute — see OQ-5):**

```
$ python -c "from qgis.core import Qgis; print(Qgis.QGIS_VERSION)"
3.40.3-Bratislava
```

**Kickoff-literal AC wording (`QgsApplication.QGIS_VERSION`) confirms the documentation gap — see OQ-5:**

```
$ python -c "from qgis.core import QgsApplication; print(getattr(QgsApplication, 'QGIS_VERSION', 'ATTR_MISSING'))"
ATTR_MISSING
```

**`google.cloud` SDK imports:**

```
$ python -c "from google.cloud import storage, pubsub_v1; print('ok')"
ok
```

**Vendor SDK exact versions (for OQ-2 evidence):**

```
$ python -c "from importlib.metadata import version; print('google-cloud-storage', version('google-cloud-storage')); print('google-cloud-pubsub', version('google-cloud-pubsub'))"
google-cloud-storage 3.11.0
google-cloud-pubsub 2.38.0
```

**GDAL import + version:**

```
$ python -c "from osgeo import gdal; print(gdal.__version__)"
3.10.2
```

**pytest available inside the env:**

```
$ python -c "import pytest; print('pytest', pytest.__version__)"
pytest 9.0.3
```

**YAML parses cleanly:**

```
$ python3 -c "import yaml; spec = yaml.safe_load(open('infra/conda/environment.yml')); print('YAML parses OK'); print('name:', spec['name']); print('channels:', spec['channels']); print('deps:', spec['dependencies'])"
YAML parses OK
name: grace2
channels: ['conda-forge']
deps: ['python=3.12', 'qgis=3.40.3', 'gdal', 'google-cloud-storage', 'google-cloud-pubsub', 'pytest', 'pip']
```

**Dead-dep strip — non-comment matches (the only matches are inside the `# Intentionally absent` documentation block, which is by design):**

```
$ grep -E 'boto3|strands|ollama|litellm|anthropic-bedrock' infra/conda/environment.yml | grep -v '^#'
# (no output)
$ echo "exit=$?"
exit=1
```

### Results

- **Pass.** All kickoff acceptance criteria satisfy live. OQ-5 records the one documentation gap (kickoff AC referenced `QgsApplication.QGIS_VERSION` which does not exist in QGIS 3.40; the supported `Qgis.QGIS_VERSION` path returns the expected `3.40.3-Bratislava` and is what the README and verification commands use).

## Revision Round 1

**Trigger:** reviewer findings on v1 (4 items: 1 blocking, 1 high, 2 low).

**v1 deficiencies (per reviewer):**
1. **BLOCKING — report.md was the empty 22-line template.** STATE=`ready-for-audit` was flipped without populating `## Summary`, `## Changes Made`, `## Decisions Made`, `## Invariants Touched`, `## Open Questions`, `## Dependencies and Impacts`, `## Verification`. Live E2E evidence lived only in commit `79d4917`'s body, not where the reviewer/orchestrator audit reads. Violated AGENTS.md § "report.md" required structure and § "Before halting any task: Ensure `report.md` reflects current truth."
2. **HIGH — zero Open Questions surfaced** despite the kickoff explicitly enumerating four contestable choices at audit.md lines 47-51. Violated AGENTS.md § "Surface uncertainty in reports" and the kickoff's own ask.
3. **LOW — AC wording vs PyQGIS API reality.** Kickoff AC at audit.md line 83 referenced `QgsApplication.QGIS_VERSION`, which does not exist in QGIS 3.40 (the attribute is on the `Qgis` class). Verified live in this revision: `getattr(QgsApplication, 'QGIS_VERSION', 'ATTR_MISSING')` → `ATTR_MISSING`. v1 used the correct attribute (`Qgis.QGIS_VERSION`) without flagging the mismatch.
4. **LOW — `.history/` directory did not exist.** AGENTS.md § "File Overwrite Rules" requires archival to `.history/report.v<N>.md` before any structural overwrite.

**Revision actions (this round):**
1. **Archived** v1 report (the empty template) to `.history/report.v1.md` before overwriting. `.history/` directory created.
2. **STATE** flipped `ready-for-audit` → `in-progress` for the duration of the revision.
3. **Populated** all required report sections (`## Summary`, `## Changes Made`, `## Decisions Made`, `## Invariants Touched`, `## Open Questions`, `## Dependencies and Impacts`, `## Verification`) with content matching the work actually done.
4. **Surfaced 5 Open Questions** — the four enumerated in the kickoff at lines 47-51 (Miniforge3 vs Mambaforge vs system conda; pin minor versions on the google-cloud SDKs; `pyproject.toml` for the worker; pytest inclusion) each with TENTATIVE recommendation and SRS reference, plus OQ-5 (the kickoff AC documentation gap on `QgsApplication.QGIS_VERSION` vs `Qgis.QGIS_VERSION`).
5. **Re-ran all kickoff ACs live** in this revision round (not relying on v1's commit body) and captured verbatim transcripts in `## Verification` above. PyQGIS, google-cloud SDKs, GDAL, and pytest all import cleanly inside `conda activate grace2`.
6. **No edits to `infra/conda/environment.yml` or `infra/README.md`** — reviewer findings were all about the report contract + OQ surfacing, not about the env spec or the README content (both v1 deliverables verified correct against ACs).
7. **STATE** flipped back to `ready-for-audit` at the end of this round; new commit `job-0022: revision round 1` records the report population + history archival.

**v1 artifacts retained:** commit `79d4917` ("job-0022: grace2 conda env on Debian 13 via conda-forge") is unchanged; this round is a new commit per the harness's "create new commits rather than amending" rule.

