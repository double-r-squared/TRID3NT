# Audit: grace2 conda env recreation on Linux Debian via conda-forge (QGIS 3.40.3, dead-dep strip)

**Job ID:** job-0022-infra-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** approved

## Task Assignment

**Specialist:** infra
**Prerequisites:** job-0012-infra-20260605 (complete) — repo layout (`infra/` ownership). No upstream dependency on 0018 or 0020 — this env is for LOCAL worker dev only; the worker ships as its own container in production (per `agents/infra.md` "Repurpose the grace2 conda env; strip dead dependencies"). PROJECT_STATE.md § "Environment facts": **no `grace2` conda env on this machine** (env was Mac-local on the prior box for QGIS 3.40.3-Bratislava PyQGIS); this job recreates on Debian 13.
**SRS references:** FR-AS-1 (Python 3.12 compatibility); FR-QS-6 (worker code substrate — local iteration loop); NFR-PO-3 (IaC — `environment.yml` committed); Decision C (PyQGIS workers); the AGENTS.md cross-cutting **"Remove don't shim"** principle and the `agents/infra.md` "Repurpose the `grace2` conda env; strip dead dependencies" clause; Invariant 4 (Rendering through QGIS Server — this env is the substrate for the only PyQGIS-worker local-dev iteration path, the one writer of `.qgs`); Invariant 2 (Deterministic workflows — worker code dev that this env supports stays a deterministic Python function, no LLM in the loop)..

### Environment

Dev + prod substrate is Linux (Debian 13 trixie, `Linux maturin 6.12.74+deb13+1-amd64`, x86_64). All container builds are `linux/amd64`-only — out of scope here (the conda env is for LOCAL PyQGIS worker dev only; production worker ships as the container built in job-0021). Consume the live cloud substrate from `PROJECT_STATE.md` § "Live cloud substrate" only as context (this job touches no cloud resources). `python3-venv` is unavailable on Debian 13 by default — use `virtualenv` if a Python venv is needed for the strip-and-verify script (sprint-03 retrospective pattern). No interactive auth required; surface `STATE = blocked` only if conda-forge is unreachable.

### Scope

1. **Author `infra/conda/environment.yml`** — the env spec under infra ownership:
   - `name: grace2`
   - `channels:` — `conda-forge` only (no `defaults`, no `anaconda`).
   - `dependencies:`
     - `python=3.12` (FR-AS-1 / Python compatibility)
     - `qgis=3.40.3` (Bratislava LTR — matches `PROJECT_STATE.md` § "Environment facts" record of the Mac-side env; matches the QGIS Server image pin from job-0018)
     - `google-cloud-storage` (worker GCS access for local iteration outside `/vsigs/`)
     - `google-cloud-pubsub` (worker Pub/Sub publish for local iteration)
     - `pip` (for any pure-python helpers not on conda-forge)
     - `pip:`
       - any pure-python deps job-0020's worker requires that are not on conda-forge (verify against `services/workers/pyqgis/worker.py` imports; surface gap as OQ if a dep is conda-forge-incompatible)
   - **DEAD-DEP STRIP — explicitly enumerated absences** (per AGENTS.md "Remove don't shim" + `agents/infra.md` clause):
     - NO `boto3`, NO `aws-cli`, NO `s3fs`
     - NO `strands` (former agent-provider abstraction)
     - NO `ollama`, NO `llama-cpp-python`, NO `litellm`
     - NO `anthropic-bedrock` or provider-abstraction packages from the v0.2 stack
     - Document each absence as a comment in `environment.yml` (one block at top: `# Intentionally absent: <list> — see agents/infra.md and AGENTS.md "Remove don't shim"`).
2. **Create the env locally** to prove the recipe works on this Debian 13 box:
   - `conda env create -f infra/conda/environment.yml` (verbatim transcript).
   - `conda activate grace2`.
   - Verify: `python --version` returns `Python 3.12.*`; `python -c "from qgis.core import QgsProject, QgsApplication; print(QgsApplication.QGIS_VERSION)"` returns a 3.40.3 string; `python -c "from google.cloud import storage, pubsub_v1; print('ok')"` returns `ok`.
3. **Document bootstrap in `infra/README.md`** (additive section "Local PyQGIS dev: `grace2` conda env"):
   - Miniforge install one-liner.
   - `conda env create -f infra/conda/environment.yml` invocation.
   - The dead-dep strip rationale (link to AGENTS.md + `agents/infra.md`).
   - The Docker-is-authoritative-runtime decision: this env is for local iteration ONLY; production worker ships as `infra/worker/Dockerfile` (job-0021). Surface the decision explicitly.
   - Per-session activation snippet (`conda activate grace2`).
4. **Open Questions to surface (TENTATIVE-tagged):**
   - Miniforge vs Mambaforge vs system conda. TENTATIVE: Miniforge (conda-forge-only, MIT/BSD posture).
   - Whether to also pin minor versions on `google-cloud-storage` + `google-cloud-pubsub` (vendor SDKs drift fast). TENTATIVE: leave unpinned for now; revisit at M3 when first agent-side consumer lands (single-version-source-of-truth across worker + agent).
   - Whether `pyproject.toml` for the worker (alongside conda) is needed to make `python -m services.workers.pyqgis.worker` resolve cleanly. TENTATIVE: defer — `PYTHONPATH=.` invocation from repo root is sufficient for M2; engine can decide at first sign of import friction.
   - Whether to add `pytest` to the env (job-0020 unit test uses pytest). TENTATIVE: add `pytest` to the conda deps so the local engine unit test runs without `pip install` outside the env.

### File ownership (exclusive)

**Parallel-ownership notes**: this job runs concurrently with job-0018. `infra/README.md` additions are additive only — append a new "Local PyQGIS dev environment (grace2 conda env)" subsection; do NOT edit job-0018's QGIS Server prose. No OpenTofu state touched (env recreation is documented + scripted only; conda env is not in IaC).

- `infra/conda/environment.yml`
- `infra/conda/` (directory creation)
- `infra/README.md` (additive section — do NOT modify existing M1 substrate documentation; append only)

**FROZEN (do NOT edit):** all other `infra/**` files (job-0018 and job-0021 own QGIS Server + worker container infra this sprint), `services/workers/pyqgis/**`, `styles/**`, `packages/contracts/**`, `services/agent/**`, `web/**`, `tests/**`, `docs/SRS_v0.3.md`, `public_hazard_catalog.yaml`, root `Makefile`.

### Cross-cutting principles in force
*Bundle small fixes; scan for all instances* — when this job touches a known class of issue (e.g., a missing label on a labeled resource), sweep the whole sprint scope for similar instances and surface in the report.

Cite by name from AGENTS.md § "Cross-cutting principles":
- **Pre-MVP scope — no legacy support.** No backward-compat with the Mac-side env shape; the recipe is the Debian 13 recipe and the only recipe.
- **Remove don't shim.** Dead deps from v0.2 (`boto3`, `strands`, `ollama`, provider-abstraction packages) DELETED from the recipe, not commented-out. The `# Intentionally absent: <list>` block is documentation of intent, not a placeholder for re-addition.
- **Live E2E validation required.** Verbatim `conda env create` + `conda activate grace2` + `python --version` + PyQGIS import check + `google.cloud` import check transcripts. Not "recipe written, untested".
- **Diagnose before fix.** Env-create failures: name the failing layer (Miniforge install vs conda-forge channel resolution vs QGIS 3.40.3 build availability vs Debian 13 glibc compat).
- **Surface uncertainty.** Every contestable choice → Open Question with TENTATIVE tag.
- **Don't edit in-flight kickoffs.** Frozen.
- **Infra: Repurpose the grace2 conda env; strip dead dependencies.** This is THE job that executes the clause from `agents/infra.md`.
- **Infra: gcloud auth login is the user's step.** Not applicable here (no GCP API calls in the env-create itself; SDKs are installed but not invoked). If any verification command needs ADC, use the existing ADC from M1.

### Acceptance criteria (reviewer re-runs)

- `infra/conda/environment.yml` exists; YAML parses (`python -c "import yaml; yaml.safe_load(open('infra/conda/environment.yml'))"`).
- The recipe contains `qgis=3.40.3`, `python=3.12`, `google-cloud-storage`, `google-cloud-pubsub`. Channel list is `conda-forge` only.
- The recipe contains the `# Intentionally absent: ...` documentation block enumerating the stripped deps.
- `grep -E 'boto3|strands|ollama|litellm|anthropic-bedrock' infra/conda/environment.yml` returns ZERO matches.
- `conda env create -f infra/conda/environment.yml` succeeds from a fresh shell (verbatim transcript). If re-run idempotent, document; otherwise `conda env remove -n grace2 && conda env create -f infra/conda/environment.yml` is the clean-slate test.
- `conda activate grace2 && python -c "from qgis.core import QgsApplication; print(QgsApplication.QGIS_VERSION)"` prints `3.40.3-*`.
- `conda activate grace2 && python -c "from google.cloud import storage, pubsub_v1; print('ok')"` prints `ok`.
- `infra/README.md` has the new "Local PyQGIS dev: `grace2` conda env" section with Miniforge install + create + activate snippets + Docker-is-authoritative-runtime decision.
- All Open Questions surfaced with TENTATIVE tags + SRS references.

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

`grace2` conda env recreated live on Debian 13 via conda-forge from `infra/conda/environment.yml`: `qgis.core.Qgis.QGIS_VERSION = '3.40.3-Bratislava'`, `python 3.12.13`, `google.cloud.storage 3.11.0`, `google.cloud.pubsub 2.38.0`, `osgeo.gdal 3.10.2`, `pytest 9.0.3`. Dead-dep strip verified — zero hits for `boto3|strands|ollama|litellm|anthropic-bedrock|aws-cli|s3fs|llama-cpp` outside the documented "intentionally absent" comment block. `infra/README.md` additive append (new "Local PyQGIS dev environment" section, no edits to job-0018's QGIS Server prose). Commits `79d4917` + `cb85ba4` (revision round 1 addressed reviewer findings: empty report.md, missing Open Questions, missing `.history/` archive). Adversarial reviewer verdict (second pass): approve with zero findings.

## Invariant Check

- **Determinism boundary:** n/a — local dev env, no LLM / narrative path.
- **Deterministic workflows:** pass (preserved) — env contains zero LLM packages (no `anthropic`/`openai`/`ollama`/`strands`/`litellm` per dead-dep grep); worker code dev that this env supports remains a deterministic Python function.
- **Engine registration, not modification:** n/a.
- **Rendering through QGIS Server:** pass (preserved) — env supports the *only* legitimate `.qgs` writer path (PyQGIS worker local-dev iteration). No other rendering path created. Production rendering remains QGIS Server in the container (job-0018); this env exists so engine can iterate worker code locally before pushing the container (job-0021). The env never serves rendered tiles.
- **Tier separation:** n/a — no map data path here.
- **Metadata-payload pattern:** n/a — no Mongo/GCS access wired in env spec.
- **Claims carry provenance:** n/a.
- **Cancellation is first-class:** n/a.
- **Confirmation before consequence — and no cost theater:** pass — zero `cost`/`usd`/`cents` strings in env spec or README append.
- **Minimal parameter surface:** pass — env spec is 7 deps + 1 channel; no excess knobs.

## Dependency Check

- **Prerequisites satisfied:** yes — no upstream job (Stage A parallel; only requires Linux Debian dev box + conda-forge network access).
- **Downstream impacts:**
  - **job-0020 (engine: PyQGIS worker code):** consumes the env for local iteration. `mamba env create -f infra/conda/environment.yml && conda activate grace2` is documented in `infra/README.md`.
  - **First post-M2 PyQGIS dev iteration on Debian:** env is reproducible from `environment.yml`.

## Decisions Validated

- **Miniforge3 over Mambaforge / system conda / Anaconda:** agree — Mambaforge is officially deprecated; system conda doesn't exist on Debian 13; Anaconda would drag the `anaconda` channel + license terms that conflict with the conda-forge-only posture (NFR-L).
- **`qgis=3.40.3` pinned to match the M2 QGIS Server image:** agree — local worker dev and production runtime are version-identical, eliminating drift class.
- **`python=3.12` pinned:** agree — current stable for PyQGIS via conda-forge; matches FR-AS-1 Python compatibility.
- **`pytest` included in env:** agree — `conda activate grace2 && pytest` works without separate `pip install`; env stays reproducible-from-yaml for both runtime and test paths.
- **`gdal` pinned explicit alongside `qgis`:** agree — solver still picks the QGIS-compatible build (gdal 3.10.2 co-installed); explicit handle makes local `/vsigs/` scripts ergonomic.
- **`google-cloud-storage` + `google-cloud-pubsub` left unpinned:** agree — production worker (job-0021 container) owns its own pinning regime; pinning here would create two sources of truth. Revisit at M3 when agent service adopts the same SDKs.
- **Docker-is-authoritative-runtime decision documented in `infra/README.md`:** agree — mitigates the v0.2 mistake where conda env drifted into a quasi-canonical runtime. Production worker ships as the job-0021 container; this env is local-iteration convenience.
- **Live E2E evidence recorded in report.md (not just commit body) after revision-round-1:** agree — AGENTS.md "Before halting any task: Ensure `report.md` reflects current truth"; reviewer + orchestrator audit looks in `report.md`.

## Open Questions Resolved

- **OQ-22A (Miniforge3 vs Mambaforge vs system conda):** resolved → Miniforge3.
- **OQ-22B (minor-version pin on google-cloud-storage + google-cloud-pubsub):** deferred to M3 when agent service adopts the same SDKs and a single-version-source-of-truth becomes relevant.
- **OQ-22C (pyproject.toml for the worker alongside conda):** deferred — `PYTHONPATH=.` invocation from repo root suffices for M2; engine revisits at first import friction.
- **OQ-22D (pytest in the env):** resolved → yes.
- **OQ-22E (Docker vs conda for worker dev — long-term):** resolved → conda for local iteration only; Docker authoritative everywhere past dev. Documented in `infra/README.md` § "Docker-is-authoritative-runtime decision".
- **Kickoff-text correction note** (`QgsApplication.QGIS_VERSION` → `qgis.core.Qgis.QGIS_VERSION`): noted for next kickoff template.

## Follow-up Actions

- **Apply kickoff-text correction in future template:** next infra kickoff that references QGIS version-check should use `qgis.core.Qgis.QGIS_VERSION` (the attribute exists on the `Qgis` class, not `QgsApplication`).
  - Routing: orchestrator (next kickoff). Priority: low.
- **OQ-22B revisit at M3** when agent service adopts `google-cloud-storage` + `google-cloud-pubsub`: decide on single-version-source-of-truth across worker + agent.
  - Routing: orchestrator (M3 planning). Priority: medium when M3 opens.
- **PROJECT_STATE update** (this audit closure): grace2 conda env exists at `~/miniforge3/envs/grace2` with QGIS 3.40.3-Bratislava + Python 3.12.13.
  - Routing: orchestrator. Priority: high.
- **Close job-0022.** Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All five kickoff acceptance criteria pass on live re-run: AC1 env spec correct + dead-dep strip verified; AC2 env actually built (`mamba env create` succeeded); AC3 QGIS importable in env (3.40.3-Bratislava); AC4 google-cloud SDKs importable; AC5 `infra/README.md` additive section added with Docker-is-authoritative-runtime decision.
- Invariants #2 + #4 pass (preserved); #9 pass (no cost theater); remaining n/a (no runtime surface in a dev env).
- One revision round (commits `79d4917` → `cb85ba4`) addressed initial reviewer findings (empty report.md, missing OQs, missing `.history/` archive); second review approved with zero findings.
- Five Open Questions surfaced with TENTATIVE tags + SRS references; orchestrator carries OQ-22B to M3 planning.
- Live conda env exists on disk at `~/miniforge3/envs/grace2`; reproducible from `infra/conda/environment.yml`.
- Revisions: 1.
