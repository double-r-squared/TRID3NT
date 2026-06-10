# job-0240-infra-20260610 — Kickoff (frozen, verbatim)

Job: build + push the MODFLOW and python-sandbox container images via Cloud Build (no local docker needed), pin digests.

## Common rules
Working dir: /home/nate/Documents/GRACE-2
- HARD CONSTRAINT: Stage 3 live acceptance is running against the live agent (:8765) and web dev server (:5173) RIGHT NOW. You must NOT: edit anything under services/agent/src or web/src, restart/kill any service, run pytest against services/agent (CPU contention is fine but imports could collide with the running venv — avoid), or call Gemini/Vertex generate APIs.
- gcloud IS at /home/nate/tools/google-cloud-sdk/bin (export PATH first). ADC live, project grace-2-hazard-prod. Sandboxed Bash cannot resolve googleapis.com — use dangerouslyDisableSandbox:true on Bash calls that hit GCP.
- NEVER git push. Commit only your owned files at end; index.lock: wait 5s retry 5x (Stage 3 jobs commit concurrently).
Return StructuredOutput.
FIRST: mkdir -p reports/inflight/job-0240-infra-20260610/evidence; audit.md kickoff verbatim; STATE RUNNING.

## Context
- job-0220 authored services/workers/modflow/Dockerfile + infra/modflow/cloudbuild.yaml + Makefile modflow-build (BLOCKED-ENV then on "no docker, no gcloud" — gcloud is NOW confirmed live; Cloud Build needs no local docker).
- job-0232 authored infra/python-sandbox/Dockerfile + cloudbuild.yaml.
- Artifact Registry digest discipline per infra/sfincs.tf:84 — after build, record the AR image digest in the .tf placeholder (modflow.tf has sha256:0000... placeholder; python-sandbox.tf check).

## Steps
1. Check the Cloud Build API + Artifact Registry repo exist (gcloud services list / artifacts repositories list — unsandboxed). If the AR repo for these images is missing, check how sfincs images are stored (gcloud artifacts docker images list on the existing repo) and target the same repo.
2. Submit both builds (gcloud builds submit per each cloudbuild.yaml — read them first; fix any path assumptions ONLY inside infra/modflow/cloudbuild.yaml + infra/python-sandbox/cloudbuild.yaml if broken, those are infra-owned). Stream logs to evidence/.
3. On success: record image digests; replace the digest placeholders in infra/modflow.tf + infra/python-sandbox.tf; tofu validate (init -backend=false). Do NOT tofu apply (resource creation is a separate gated step).
4. If a build fails on a Dockerfile bug, FIX the Dockerfile (services/workers/modflow/Dockerfile + infra/python-sandbox/Dockerfile are infra-owned, NOT under services/agent/src — allowed), rebuild, document. The mf6 zip-dir bug class (job-0220 fix round) is already fixed — but the build has never actually run, expect surprises; iterate up to 3 attempts per image.
5. Report build durations + digests + total Cloud Build cost estimate. Commit owned files (Dockerfiles if changed, cloudbuild.yamls, .tf digest pins, report dir).

## Ownership
services/workers/modflow/Dockerfile, infra/modflow/**, infra/modflow.tf, infra/python-sandbox/**, infra/python-sandbox.tf, your report dir. NOTHING else.
