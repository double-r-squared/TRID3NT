# Audit: Python sandbox Cloud Run Job container

**Job ID:** job-0232-infra-20260609
**Sprint:** sprint-13 (Stage 2, pulled forward)
**Auditor:** Development Orchestrator
**Status:** assigned

# Kickoff (frozen)

You are the infra specialist. Job job-0232-infra-20260609 — Python sandbox Cloud Run Job container (sprint-13 Stage 2, pulled forward; adversarial-verify gated, network-egress focus).

## Common rules (GRACE-2 sprint-13)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, agents/infra.md, reports/sprints/sprint-13-manifest.md (job-0232 scope, line ~129), reports/PROJECT_STATE.md.
FIRST ACTION: mkdir -p reports/inflight/job-0232-infra-20260609/ ; write audit.md containing this kickoff verbatim under "# Kickoff (frozen)"; write STATE file "RUNNING".
- NO Gemini/Vertex generate_content calls. Hard rule.
- NEVER git push. Commit locally at job end: git add <only your files> && git commit -m "job-0232-infra-20260609: Python sandbox Cloud Run Job container". On index.lock wait 5s, retry up to 5x (sprint-13 Stage 1 agents are committing in parallel).
- Environment: docker daemon NOT reachable (socket permission denied); gcloud NOT installed; tofu IS installed (validate with init -backend=false only). Design around this, document what needs the user.
- Report honestly; PARTIAL with documented blockers beats fake success.
- AT JOB END: write reports/inflight/job-0232-infra-20260609/report.md and set STATE to "READY_FOR_AUDIT".

## Scope (manifest job-0232)
1. infra/python-sandbox/ (NEW dir):
   - Dockerfile: python:3.11-slim base + venv discipline matching services/workers/modflow + sfincs patterns; pre-installed: rasterio, geopandas, numpy, pandas, matplotlib, scikit-learn, networkx, gcsfs. Pin versions consistent with services/agent constraints where shared.
   - Entrypoint harness (executor.py inside the container image): receives a job payload (python_code string + layer_refs dict LayerURI->gcs path) via Cloud Run Job env/args or GCS staging file; injects layer references as pre-opened gcsfs-backed rasterio/geopandas handles; executes user code with 60s wallclock cap (SIGALRM or subprocess timeout) and bounded output capture; returns JSON {stdout, stderr, result} where result is the final `result` variable — auto-converted: matplotlib Figure -> vega-lite-compatible PNG-fallback descriptor or chart payload, DataFrame -> records JSON (cap rows). If grace2_contracts chart_contracts is importable, emit a ChartEmissionPayload-shaped dict for figures (soft import; job-0223 panel is concurrently finalizing that schema — any drift gets reconciled by job-0233).
   - Egress denial design: Cloud Run Job with VPC egress control — all traffic through a VPC connector + firewall allowing ONLY GCS (restricted.googleapis.com Private Google Access range) + MongoDB Atlas endpoint. Express this in tofu. ALSO add defense-in-depth inside the harness: no proxy env vars, and a pre-exec socket guard (override socket.create_connection allowlist in the executor namespace) so even image-local code can't trivially reach the internet — document that the real boundary is the VPC layer, the in-process guard is best-effort.
2. infra/python-sandbox.tf (NEW): Cloud Run Job resource (2GB mem cap, 60s task timeout, max 1 retry), read-only GCS access to cache/ + runs/ buckets via dedicated SA with objectViewer ONLY (no write), VPC connector + egress firewall rules per above. Mirror resource-naming conventions in infra/sfincs.tf.
3. services/agent/src/grace2_agent/sandbox_runner.py (NEW): host-side dispatch shim submit_sandbox_job(python_code, layer_refs) -> ExecutionHandle-shaped pending result + local-subprocess fallback executor (GRACE2_SANDBOX_LOCAL=1) that reuses the same executor.py harness logic for dev/test on this machine. The local fallback enforces the same 60s cap + output bounds.

## Acceptance (environment-adjusted)
- [REQUIRED] Local harness live test: run executor.py via the local-subprocess fallback on (a) a benign numpy script producing result=float, (b) a matplotlib figure script -> chart payload conversion, (c) a malicious script attempting urllib/socket to example.com -> blocked by the in-process guard, (d) an infinite loop -> killed at the cap. Save all 4 logs to reports/inflight/job-0232-infra-20260609/evidence/.
- [REQUIRED] tofu validate (init -backend=false) green on infra/.
- [REQUIRED] pytest services/agent/tests/test_sandbox_runner.py covering the 4 scenarios above through sandbox_runner local mode.
- [BLOCKED-ENV, document] docker build + Cloud Run deploy + REAL VPC egress-deny verification — write exact user unblock commands + a verification runbook section in report.md (the adversarial panel and later job-0238 acceptance will need it).

## File ownership
infra/python-sandbox/**, infra/python-sandbox.tf, services/agent/src/grace2_agent/sandbox_runner.py, services/agent/tests/test_sandbox_runner.py. NOTHING else — Stage 1 agents own tools/, workflows/, contracts/, services/workers/modflow/ right now.
Return StructuredOutput.
