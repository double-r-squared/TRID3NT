# Audit: grace2 conda env recreation on Linux Debian via conda-forge (QGIS 3.40.3, dead-dep strip)

**Job ID:** job-0022-infra-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** assigned

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

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
