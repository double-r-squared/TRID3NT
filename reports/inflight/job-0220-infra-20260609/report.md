# job-0220-infra — MODFLOW 6 container + Cloud Run Job + Workflows skeleton

**Specialist:** infra
**Sprint:** sprint-13 Stage 1 (MOD-1)
**Date:** 2026-06-09
**Verdict:** PARTIAL (all on-machine acceptance met; container build + cloud deploy BLOCKED-ENV, documented)

---

## Outcome

The MODFLOW 6 solver substrate is built end-to-end and validated on this
machine to the limit the environment allows:

- **services/workers/modflow/** — Dockerfile (python:3.11-slim + venv + mf6
  6.5.0 binary, SHA-256-verified), entrypoint.py (GCS-IN -> mf6-RUN -> GCS-OUT
  shim with the MODFLOW-specific subdir-preserving download + convergence
  guard), __init__.py, README.md, and a minimal smoke-test deck fixture + manifest.
- **infra/modflow.tf** — Cloud Run Job (grace-2-modflow-solver, 8Gi/4vCPU/7200s)
  + dedicated runtime SA + workflow-invoker SA + IAM + Cloud Workflow
  (grace-2-modflow-orchestrator), mirroring infra/sfincs.tf section by section.
- **infra/modflow/cloudbuild.yaml** — Cloud Build config (mirror of the SFINCS one).
- **Makefile** — modflow-build / modflow-push / modflow-deploy targets.

Primary live evidence (mf6 HOST smoke test) PASSES. A SECOND live test
exercises the actual entrypoint module end-to-end against a fake in-memory GCS.

---

## Acceptance evidence

### [REQUIRED PASS] mf6 HOST smoke test
evidence/mf6_smoke.log + evidence/mf6_provenance.txt
  mf6: 6.5.0 05/23/2024
  zip SHA-256 = 0fac00211c42b7a74c7266abbe50776a6215ea8409c8ce887e5decd4a9335940
  Normal termination of simulation.
  head file: smoke.hds shape=(1,10,10) size_bytes=852 min=2.0 max=8.0 all_finite=True
  SMOKE TEST PASS

### [REQUIRED PASS] tofu validate
evidence/tofu_validate.log
  tofu init -backend=false  -> OpenTofu has been successfully initialized!
  tofu validate             -> Success! The configuration is valid.
  tofu fmt -check modflow.tf -> exit 0

### [BONUS PASS] entrypoint module end-to-end (fake GCS, real mf6)
evidence/entrypoint_e2e.log
  completion.json: status=ok exit_code=0 converged=true model_crs=EPSG:26915
  output_uris include smoke.hds + mfsim.lst; uploaded smoke.hds = 852 bytes
  ALL E2E ASSERTIONS PASSED
  Convergence guard independently exercised: converged deck -> True;
  synthetic divergent list file -> solver_diverged (exit 2); absent list -> not converged.

### [BLOCKED-ENV — documented only] docker build + Cloud Run Job deploy
No reachable docker daemon (socket permission denied); gcloud not installed.
Image NOT built/pushed; Cloud Run Job / Workflow NOT applied. Config complete
+ validated; only AR push + digest pin + tofu apply remain.

---

## User unblock steps

    # 1. (local docker only) sudo usermod -aG docker nate   (then newgrp docker)
    # 2. install + auth gcloud (interactive — USER's step):
    gcloud auth login
    gcloud auth application-default login
    gcloud config set project grace-2-hazard-prod
    # 3. build + push via Cloud Build (no local docker needed):
    make modflow-build
    # 4. read the resolved digest:
    gcloud artifacts docker images list \
      us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers \
      --include-tags | grep modflow-solver
    # 5. pin it: edit infra/modflow.tf `modflow_image_digest` (currently sha256:0000...)
    # 6. deploy:
    make modflow-deploy
    # 7. live-verify: stage fixtures/{mfsim.nam,smoke.*} + a resolved manifest.json
    #    into gs://grace-2-hazard-prod-cache/modflow/<run_id>/, then:
    gcloud workflows run grace-2-modflow-orchestrator \
      --data='{"run_id":"<run_id>","manifest_uri":"gs://grace-2-hazard-prod-cache/modflow/<run_id>/manifest.json"}'
    #    assert completion.result.status == "ok" + gs://...-runs/<run_id>/smoke.hds appears.
After step 6, tofu plan must return "No changes".

---

## Deviations from the design doc

1. mf6 asset name: design doc cited mf6.0_linux.zip; actual 6.5.0 asset is
   mf6.5.0_linux.zip (verified live; SHA pinned). Used the correct name.
2. Repo redirect: MODFLOW-USGS/modflow6 301-redirects to MODFLOW-ORG/modflow6;
   Dockerfile keeps the canonical URL with curl -fL (follow redirects).
3. Runs bucket reuse: infra/sfincs.tf already provisions google_storage_bucket.runs;
   modflow.tf reuses it (one runs bucket, per-run_id prefixes) instead of a 2nd bucket.
4. No -qgs viewer grant on modflow-runtime (design doc § 4) — MODFLOW reads
   FloPy text decks from the cache bucket, not the .qgs store.
5. Image digest is a PLACEHOLDER (sha256:0000...) until the first Cloud Build;
   validate passes, apply will fail to pull until step 5 above records the real digest.

---

## Files created / changed

- services/workers/modflow/__init__.py (new)
- services/workers/modflow/Dockerfile (new)
- services/workers/modflow/entrypoint.py (new)
- services/workers/modflow/README.md (new)
- services/workers/modflow/fixtures/manifest.json (new)
- services/workers/modflow/fixtures/{mfsim.nam,smoke.tdis,smoke.ims,smoke.nam,smoke.dis,smoke.ic,smoke.npf,smoke.chd,smoke.oc} (new — input deck only)
- infra/modflow.tf (new)
- infra/modflow/cloudbuild.yaml (new)
- Makefile (additive: .PHONY + help + 3 modflow targets)

NOT created (owned by job-0221, parallel): gwt_adapter.py, test_gwt_adapter.py.

---

## Open Questions / notes for downstream jobs

- OQ-MOD-1 (convergence string) RESOLVED for 6.5.0: marker hardcoded in
  entrypoint.py; converged=True requires the Normal-termination marker too.
- OQ-MOD-3 (model CRS) wired: manifest carries model_crs (new vs SFINCS);
  entrypoint echoes it into completion.json for the postprocess reprojection.
  gwt_adapter.py (job-0221) must populate it.
- Interface for job-0221: deck builder uploads mfsim.nam + gwf/ + gwt/ subdir
  files per design-doc § 6 manifest shape; entrypoint reconstructs the subdir
  tree via _download mkdir -p (verified). The smoke fixture is a FLAT pure-GWF
  deck (no GWT) to keep the convergence proof self-contained.
- OQ-MOD-4 (memory): 8 GiB demo-grid ceiling; profile + bump if job-0221 goes finer.
- Budget (NFR-C-1): Cloud Run Job scale-to-zero (no min instances) — zero idle
  cost; labeled component=modflow-solver, sprint=13 for itemization.
- Live-verify for the adversarial panel is BLOCKED-ENV step 7 (needs gcloud/docker
  unblock). Host mf6 smoke + fake-GCS entrypoint e2e cover the full solver-container
  logic minus the cloud plumbing (tofu validate-clean, structurally identical to
  the live-verified SFINCS path).

---

## Fix round 1 (adversarial panel — REFUTED)

The panel REFUTED the job on a single build-breaking bug (CORRECTNESS [major] +
LIVE-VERIFY [major], same root cause). Everything else was independently PASS.

### The bug

`services/workers/modflow/Dockerfile` lines 88-90 (pre-fix) extracted/installed
the mf6 binary from `mf6.${MF6_VERSION}_linux/...`. With `ARG MF6_VERSION=6.5.0`
that expands to **`mf6.6.5.0_linux`** — but the USGS 6.5.0 release zip's actual
top-level directory is **`mf6.5.0_linux`** (the zip filename `mf6.5.0_linux.zip`
already drops the leading "6." that the Dockerfile re-prepended). The `unzip`
glob matched nothing → exit 11 → the `&&`-chained RUN step aborts → `docker build`
fails at that layer → no image is ever produced. The host smoke test missed it
because it ran flopy against a separately pre-extracted binary, never the
Dockerfile's unzip/install lines.

### The fix (recommended ARG form)

Added `ARG MF6_ZIP_DIR=mf6.5.0_linux` after the MF6_VERSION/URL/SHA pins
(Dockerfile line 57, with a comment explaining the USGS naming inconsistency:
zip dir is `mf6.5.0` while `mf6 --version` reports `6.5.0`), and replaced the
three `mf6.${MF6_VERSION}_linux` occurrences (unzip glob + two install source
paths, now lines 94-96) with `${MF6_ZIP_DIR}`. The SHA pin, MF6_ZIP_URL,
entrypoint.py, infra/modflow.tf, Makefile, and cloudbuild.yaml were all already
correct and were left untouched.

### Re-verification (all green)

Re-ran the panel's exact reproduce commands against the SHA-256-verified zip
(`reports/inflight/job-0220-infra-20260609/verify/mf6_dl/mf6.zip`):

- Panel-buggy glob `unzip -o mf6.zip "mf6.6.5.0_linux/bin/mf6" -d /tmp/x` →
  `caution: filename not matched`, **exit 11**, nothing extracted (confirms the bug).
- Fixed glob `unzip -o mf6.zip "mf6.5.0_linux/bin/mf6" -d /tmp/x2` → inflates the
  binary, **exit 0**.
- Full RUN-block simulation with the expanded ARG values
  (`sha256sum -c` → `unzip "${MF6_ZIP_DIR}/bin/{mf6,libmf6.so}"` →
  `install -m 755 .../${MF6_ZIP_DIR}/bin/mf6 → /usr/local/bin/mf6` + libmf6.so):
  sha **OK**, both files inflated, both installed `-rwxr-xr-x`, `test -x` passes —
  **FULL CHAIN OK, exit 0**.
- `grep` confirms the only remaining `mf6.${MF6_VERSION}_linux` / `mf6.6.5.0_linux`
  tokens are in the new explanatory comment (lines 55-56); all three functional
  paths use `${MF6_ZIP_DIR}`.
- No regression in `infra/modflow.tf` (it never referenced the zip dir):
  `tofu fmt -check` exit 0 (no diff), `tofu validate` → "The configuration is
  valid." exit 0.

`docker build` itself could not be run (docker daemon is down in this sandbox,
same BLOCKED-ENV as step 7), but the refutation was specifically the unzip/install
path resolution, which is now verified end-to-end at the shell level with the
real zip. Build-time smoke (`mf6 --version`, lines 98 + 130) is now reachable.
