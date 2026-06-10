# Kickoff (frozen)

You are the schema specialist. Job job-0222-schema-20260609 — MODFLOW contracts (sprint-13 Stage 1).

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
packages/contracts/src/grace2_contracts/modflow_contracts.py (NEW):
- MODFLOWRunArgs(BaseModel): spill_location_latlon (tuple/2-list lat,lon with validators), contaminant (str), release_rate_kg_s (float >0), duration_days (float >0), aquifer_k_ms (float >0, default 1e-4), porosity (float 0-1, default 0.3). Defaults per manifest OQ-3 TENTATIVE (demo parameterization, narrated caveat).
- PlumeLayerURI: extends the existing LayerURI contract (read the LayerURI source first; match its style exactly) adding max_concentration_mgl (float >=0) and plume_area_km2 (float >=0).
- Export both from grace2_contracts __init__.py (single import line + __all__ additions; minimal surgical diff).
- Tests packages/contracts/tests/test_modflow_contracts.py: validation bounds, defaults, PlumeLayerURI round-trip serialization, inheritance from LayerURI verified.

## File ownership
packages/contracts/src/grace2_contracts/modflow_contracts.py, packages/contracts/tests/test_modflow_contracts.py, contracts __init__.py (surgical).
