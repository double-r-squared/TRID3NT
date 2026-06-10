# Report: Python sandbox Cloud Run Job container

**Job ID:** job-0232-infra-20260609
**Sprint:** sprint-13 (Stage 2, pulled forward; adversarial-verify gated)
**Specialist:** infra
**Task:** Cloud Run Job container for the conversational-analysis Python sandbox — rasterio/geopandas/numpy/pandas/matplotlib/scikit-learn/networkx/gcsfs pre-installed; network egress denied (except whitelisted GCS + Atlas); 60s wallclock cap; 2 GiB mem cap; read-only GCS (cache/ + runs/); layer refs injected as gcsfs-backed handles; result auto-converted to chart-emission/DataFrame descriptors; host-side dispatch shim + local-subprocess fallback.
**Status:** ready-for-audit

## Summary
Built the Python sandbox substrate: a `python:3.11-slim` + venv container (`infra/python-sandbox/`) whose `executor.py` harness runs user-confirmed Python under a 60s SIGALRM cap with bounded output + an in-process network guard, auto-converting a `result` variable to a ChartEmissionPayload-shaped / DataFrame / scalar descriptor; the `infra/python-sandbox.tf` Cloud Run Job wired to an isolated VPC with a default-deny egress firewall (allow ONLY restricted.googleapis.com GCS PGA + the Atlas CIDR) behind a Serverless VPC Access connector with ALL_TRAFFIC egress, run by a read-only-objectViewer SA; and the host-side `sandbox_runner.py` dispatch shim with a local-subprocess fallback that reuses the identical harness. All 4 required acceptance scenarios pass live through the local fallback, `tofu validate` is green, and 19 pytest cases pass. Container build + real VPC egress-deny verification are BLOCKED-ENV (no docker daemon, no gcloud) with the exact unblock runbook below.

## Changes Made
- **infra/python-sandbox/executor.py** (NEW) — container entrypoint harness. Payload via --payload-file / gs:// staging / inline env; installs in-process net guard + strips proxy env; builds pre-opened rasterio/geopandas handles (raster->rasterio.open via /vsigs/, vector->geopandas.read_file/read_parquet, unknown->raw URI + <name>_uri alias); runs user code under SIGALRM 60s cap inside bounded redirect buffers; converts result (Figure->PNG + ChartEmissionPayload-shaped dict via soft-import; DataFrame->records JSON row-capped; numpy scalar/array; JSON-native; else repr); prints one JSON envelope on real stdout.
- **infra/python-sandbox/Dockerfile** (NEW) — python:3.11-slim + venv at /opt/grace2/.venv (mirrors modflow Dockerfile PEP-668 discipline); pinned analytical stack consistent with services/agent/pyproject.toml where shared; MPLBACKEND=Agg; build-time import smoke + run_user_code assert; ENTRYPOINT python3 -m executor.
- **infra/python-sandbox/cloudbuild.yaml** (NEW) — Cloud Build mirroring infra/modflow/cloudbuild.yaml.
- **infra/python-sandbox.tf** (NEW) — egress boundary + Job. In-file vars (3 CIDRs) + compute/vpcaccess API enablement (variables.tf/gcp.tf out of ownership); isolated VPC grace-2-sandbox-net + subnet (private_ip_google_access) + Serverless VPC connector; default-deny egress firewall (pri 65534) + ALLOW restricted.googleapis.com /30 (443) + ALLOW Atlas CIDR; python-sandbox-runtime SA objectViewer-ONLY on -cache + -runs (NO write); grace-2-python-sandbox Cloud Run v2 Job (2 GiB/1 vCPU, 60s timeout, max_retries=1, vpc_access.egress=ALL_TRAFFIC); agent-runtime invoke/develop/actAs (resource-scoped).
- **services/agent/src/grace2_agent/sandbox_runner.py** (NEW) — submit_sandbox_job: cloud mode stages payload to GCS + submits run_v2 Job execution + returns SandboxExecutionHandle (execution_name poll/cancel seam); local mode (GRACE2_SANDBOX_LOCAL=1) runs executor.py in a child subprocess with the SAME 60s cap + an outer communicate(timeout=cap+10) hard-kill backstop, returns parsed envelope.
- **services/agent/tests/test_sandbox_runner.py** (NEW) — 19 cases through the local fallback: 4 required scenarios + outer-kill backstop + chart-payload pydantic construction + loopback-allowed + stdout-truncation + DataFrame conversion/row-cap + user-error capture + layer-ref injection + submit-routing + handle shape.

## Decisions Made
- **In-process net guard overrides socket connect paths (defense-in-depth, NOT the boundary).** Covers urllib/http.client/requests/raw-socket in one patch; clean SandboxNetworkBlocked error. The VPC egress firewall is the real boundary. Restricted-builtins rejected (not a real CPython boundary); seccomp rejected (heavy + redundant with VPC at v0.1).
- **Subprocess (not in-process import) for the local fallback.** Harness monkeypatches process-global socket state + installs SIGALRM; child process is disposed each run + gives the outer wallclock hard-kill that survives a defeated in-process alarm.
- **matplotlib Figure -> PNG wrapped in a Vega-Lite image-mark spec (the kickoff's PNG-fallback descriptor).** A raw figure has no native Vega-Lite spec; ChartEmissionPayload constructs cleanly from the image-mark wrapper (proven in test). Job-0230's chart tools emit true data-encoded specs; this is the escape hatch for arbitrary sandbox figures.
- **60s Job timeout (not 1800s like SFINCS).** Sandbox is a fast query; the Job timeout IS the hard cap, with the in-container SIGALRM at 60s and the host outer-kill at cap+10s.
- **python-sandbox-runtime SA is objectViewer-ONLY (no objectAdmin anywhere).** Pure reader; charts persisted by the agent via Mongo. Hostile code cannot overwrite a layer. Load-bearing for Invariant 5.
- **ALL_TRAFFIC egress through the connector (not PRIVATE_RANGES_ONLY).** Forces every outbound packet through the connector where the firewall drops all but GCS PGA + Atlas. PRIVATE_RANGES_ONLY would let public-internet egress bypass the connector.

## Invariants Touched
- **5. Tier separation:** preserves — sandbox reads -cache/-runs READ-ONLY, never writes; client never reaches it (agent mediates code-in/result-out).
- **6. Metadata-payload pattern:** preserves — agent hands explicit layer_refs; sandbox never enumerates buckets.
- **9. Confirmation / no cost theater:** preserves — no cost field; scale-to-zero Job (connector floor excepted, see budget note); user-confirm gate is job-0234.
- **New egress-deny boundary:** adds VPC connector + default-deny firewall (no prior VPC existed) — isolated network, widens no existing surface.

## Open Questions
- **OQ-SANDBOX-1 (Atlas egress rule — keep or drop?):** Kickoff says GCS + Atlas, but at v0.1 the agent persists charts via the Mongo MCP path, not the sandbox — sandbox may need GCS-only. Provisioned the Atlas ALLOW rule with a non-routable placeholder CIDR (203.0.113.0/32, RFC-5737) so no real egress opens until the user sets sandbox_atlas_cidr. TENTATIVE: keep but default non-routable; orchestrator confirm whether the sandbox needs direct Mongo.
- **OQ-SANDBOX-2 (vars + API placement):** Declared the 3 CIDR vars + compute/vpcaccess google_project_service INSIDE python-sandbox.tf (variables.tf/gcp.tf out of ownership). Functionally correct; tidier home is variables.tf + gcp.tf enabled_apis. TENTATIVE: leave for the job; orchestrator may relocate on landing.
- **OQ-SANDBOX-3 (result readback for cloud mode):** Cloud handle carries result_uri, but the executor writes its envelope to stdout (Cloud Run logs), not result_uri — the runtime SA is objectViewer-only and CANNOT write. Job-0233 must finalize: agent reads logs, OR a different identity writes the result. TENTATIVE: surfaced for job-0233.
- **OQ-SANDBOX-4 (chart-contract drift):** chart-emission dict is a soft-import best-effort against job-0223 ChartEmissionPayload; constructs the real model TODAY (proven), but a job-0223 field rename needs job-0233 reconciliation per the kickoff.

## Dependencies and Impacts
- **Depends on:** Wave 4.11 close; grace2_contracts.chart_contracts (job-0223, soft-import); execution.ExecutionHandle shape (reference); -cache/-runs buckets.
- **Affects:** job-0233 (consumes submit_sandbox_job + handle; owns code_exec_request envelope + result readback OQ-SANDBOX-3 + confirm-gate dispatch); job-0238 (Playwright acceptance — needs real Cloud Run deploy + job-0234 modal); adversarial panel (correctness lens needs real VPC verification — runbook below).

## Verification
### Tests run
- `pytest services/agent/tests/test_sandbox_runner.py` — 19 passed (~45s, agent venv Python 3.12.13). Log: evidence/pytest_sandbox_runner.log.
- `tofu validate` (after init -backend=false) — Success! configuration is valid. `tofu fmt -check python-sandbox.tf` — no diff. Log: evidence/tofu_validate.log.
- Executor build-time smoke (mirrors Dockerfile assert) — OK. Log: evidence/executor_build_smoke.log.

### Live E2E evidence (local-subprocess fallback — the harness baked into the container)
4 REQUIRED scenarios via sandbox_runner.run_sandbox_local (same executor.py the container runs), logs in evidence/:
- **(a) scenario_a_numpy.log** — benign numpy -> status=ok, result={kind:json, value:25.0}. ASSERT PASS.
- **(b) scenario_b_matplotlib.log** — figure -> result.kind=chart, 15 KB PNG inlined, chart_emission dict constructs a real ChartEmissionPayload. ASSERT PASS.
- **(c) scenario_c_malicious_network.log** — raw socket AND urllib to example.com -> both SandboxNetworkBlocked; result=BLOCKED:SandboxNetworkBlocked; no internet reached. ASSERT PASS.
- **(d) scenario_d_infinite_loop.log** — while True -> status=timeout at 3.11s (3s cap). ASSERT PASS.
- **(d2) scenario_d2_outer_kill_backstop.log** — code that SIG_IGNs SIGALRM -> killed by outer subprocess timeout at 12.01s. ASSERT PASS (belt-and-suspenders).

### Results
- PASS for every required environment-adjusted acceptance: local harness 4/4 + (d2) backstop, tofu validate green, pytest 19/19.
- BLOCKED-ENV (documented, runbook below): docker build + Cloud Run deploy + REAL VPC egress-deny verification. No docker daemon + gcloud not installed.

---
## BLOCKED-ENV: user unblock + verification runbook

### 1. Enable APIs + apply IaC (user)
```bash
gcloud auth login                              # interactive — user's step
gcloud auth application-default login
gcloud services enable compute.googleapis.com vpcaccess.googleapis.com --project=grace-2-hazard-prod
# OPTIONAL in infra/terraform.tfvars: sandbox_atlas_cidr (or drop the rule — OQ-1), sandbox_subnet_cidr, sandbox_connector_cidr
cd infra && tofu init && tofu apply            # VPC + subnet + connector + firewall + SA + Job
```

### 2. Build + push image, pin digest (user)
```bash
make python-sandbox-build                      # wraps gcloud builds submit --config=infra/python-sandbox/cloudbuild.yaml .
gcloud artifacts docker images list us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers --include-tags | grep python-sandbox
# paste sha256 -> infra/python-sandbox.tf local.python_sandbox_image_digest
cd infra && tofu apply && tofu plan            # plan MUST return "No changes"
```
NOTE: the `make python-sandbox-build` target must be added to the root Makefile (mirror of modflow-build/sfincs-build) — that line touches Makefile, outside this job's ownership, left for orchestrator/agent. cloudbuild.yaml + invocation are ready.

### 3. REAL VPC egress-deny verification (adversarial-panel correctness lens)
```bash
cat > /tmp/payload.json <<'EOF'
{"python_code": "import urllib.request, socket\ntry:\n    socket.create_connection(('example.com', 80), timeout=8)\n    result='REACHED'\nexcept Exception as e:\n    result=f'NET_BLOCKED:{type(e).__name__}'\n", "layer_refs": {}}
EOF
gsutil cp /tmp/payload.json gs://grace-2-hazard-prod-cache/sandbox/verify/payload.json
# in-process guard DISABLED (wildcard allow) to isolate the VPC layer:
gcloud run jobs execute grace-2-python-sandbox --project=grace-2-hazard-prod --region=us-central1 --wait \
  --update-env-vars=GRACE2_SANDBOX_PAYLOAD_URI=gs://grace-2-hazard-prod-cache/sandbox/verify/payload.json,GRACE2_SANDBOX_NET_ALLOW=example.com
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=grace-2-python-sandbox' --project=grace-2-hazard-prod --limit=20 --freshness=10m
```
PASS criterion: result is NET_BLOCKED:* (timed out / no route) with the in-process guard wide open — proving the VPC firewall is the boundary. REACHED = misconfigured firewall.

### 4. GCS-read-still-works verification
```bash
cat > /tmp/gcs_payload.json <<'EOF'
{"python_code": "import gcsfs\nfs=gcsfs.GCSFileSystem()\nfiles=fs.ls('grace-2-hazard-prod-runs')\nresult={'gcs_reachable': True, 'n': len(files)}\n", "layer_refs": {}}
EOF
gsutil cp /tmp/gcs_payload.json gs://grace-2-hazard-prod-cache/sandbox/verify/gcs.json
gcloud run jobs execute grace-2-python-sandbox --project=grace-2-hazard-prod --region=us-central1 --wait \
  --update-env-vars=GRACE2_SANDBOX_PAYLOAD_URI=gs://grace-2-hazard-prod-cache/sandbox/verify/gcs.json
```
PASS criterion: result.gcs_reachable=True — GCS reads survive the egress-deny (PGA route open).

### Budget note (NFR-C-1)
Idle additions: the Serverless VPC Access connector has a non-zero floor (min_instances=2 e2-micro, ~$8-10/mo) — the ONE non-scale-to-zero item, justified because the connector IS the egress boundary the Job requires (a Cloud Run Job cannot attach a fully-scale-to-zero connector). Everything else ($0 idle). Labeled component=python-sandbox, sprint=13. If unwanted, the alternative is a shared connector reused across future VPC-bound Jobs — surfaced for budget review.
