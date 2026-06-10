# Kickoff (frozen)

You are the infra specialist. Job job-0220-infra-20260609 — MODFLOW 6 container + Cloud Run Job + Workflows skeleton (sprint-13 Stage 1, adversarial-verify gated).

## Common rules (GRACE-2 sprint-13 Stage 1)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, your specialist file in agents/, reports/sprints/sprint-13-manifest.md (your job scope), reports/PROJECT_STATE.md.
FIRST ACTION: mkdir -p reports/inflight/<job-id>/ ; write audit.md containing this kickoff prompt verbatim under a "# Kickoff (frozen)" header; write STATE file containing "RUNNING".
- NO Gemini/Vertex generate_content calls of any kind. This job needs none. Hard rule.
- NEVER git push. Commit locally at job end: git add <ONLY your owned files> && git commit -m "<job-id>: <short title>". On index.lock conflict wait 5s, retry up to 5x.
- Stay inside your file ownership. Registration touchpoints (tools/__init__.py, catalog.py, categories.py, contracts __init__.py) only where your kickoff explicitly grants them.
- Python venv: services/agent/.venv (pip install missing deps there as needed). Contracts tests: packages/contracts. Web: npx vitest in web/.
- Environment facts: docker daemon NOT reachable on this machine (socket permission denied); gcloud NOT installed; tofu IS installed (validate with -backend=false only, no plan/apply). Do not burn time fighting these — design around them and document.
- Report honestly. If acceptance can only partially be met on this machine, verdict=PARTIAL with exact blocker documented — never fake success.
- AT JOB END: write reports/inflight/<job-id>/report.md (outcome, evidence, open questions) and set STATE to "READY_FOR_AUDIT".
Return StructuredOutput.

## Authoritative design
reports/inflight/sprint-13-mod-1-modflow-container-design-20260609/design.md — follow it. Deviations must be documented in report.md with reasoning.

## Scope
1. services/workers/modflow/ (NEW dir): Dockerfile (python:3.11-slim base, venv at /opt/grace2/.venv, flopy pinned, MODFLOW 6.5.0 binary from USGS GitHub release with SHA-256 checksum verification per design doc), entrypoint.py (reads deck from GCS, runs mf6, uploads outputs — mirror services/workers/sfincs/ pattern), README.md, minimal smoke-test model fixture.
2. infra/modflow.tf (NEW): Cloud Run Job resource + Cloud Workflows definition mirroring infra/sfincs.tf (submit, poll, fetch output, trigger postprocess). Match the ExecutionHandle contract shape used by the SFINCS workflow.
3. Makefile target modflow-build (pattern-match the sfincs build target) — documented, not executed (no docker daemon here).

## Acceptance (environment-adjusted)
- [REQUIRED] mf6 HOST smoke test: download the pinned mf6.5.0 linux zip (it is a static binary), verify checksum, build a minimal GWF model via flopy in a temp dir, execute mf6, confirm convergence + non-empty head output. Save the log to reports/inflight/<job-id>/evidence/mf6_smoke.log. This is your primary live evidence.
- [REQUIRED] tofu validate passes on infra/ (init -backend=false).
- [BLOCKED-ENV, document only] docker build + Cloud Run Job deploy: write exact commands into report.md under "User unblock steps" (docker group membership: sudo usermod -aG docker nate; gcloud install + auth). Do NOT attempt sudo.

## File ownership
services/workers/modflow/** (except gwt_adapter.py and test_gwt_adapter.py — owned by job-0221 running in parallel; do not create them), infra/modflow.tf, Makefile (additive target only).
