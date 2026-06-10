# Kickoff (frozen)

You are the engine specialist. Job job-0225-engine-20260609 — model_flood_scenario v2: real-precip forcing branch (sprint-13 Stage 1).

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

## Scope
services/agent/src/grace2_agent/workflows/model_flood_scenario.py (ADDITIVE amendment): add forcing_raster_uri (str or None, default None) parameter alongside the existing design-storm forcing. When set: download the precip raster, compute AREA-MEAN accumulated precip over the model domain, convert to SFINCS uniform-rain forcing (netamt path) in the deck builder (sfincs_builder.py is in your ownership for this amendment if the forcing plumbing lives there). This locks manifest OQ-6 TENTATIVE: v0.1 = area-mean netamt fallback (document the spw upgrade path in a code-adjacent comment + report.md). When forcing_raster_uri is None: behavior IDENTICAL to today (regression-critical).
Check (read-only, ~10 min cap) whether the SFINCS container pinned in services/workers/sfincs/ supports spw spatially-varying precip — record the finding in report.md either way; do NOT implement spw.
Tests services/agent/tests/test_model_flood_scenario_v2.py: None-path unchanged (assert deck identical to a baseline run), raster-path computes correct area-mean from a synthetic raster, netamt value lands in the deck.

## File ownership
workflows/model_flood_scenario.py, workflows/sfincs_builder.py (forcing plumbing only), tests/test_model_flood_scenario_v2.py.
