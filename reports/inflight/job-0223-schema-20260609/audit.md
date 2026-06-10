# Kickoff (frozen)

You are the schema specialist. Job job-0223-schema-20260609 — chart-emission envelope schema + Vega-Lite wire format contract (sprint-13 Stage 1, adversarial-verify gated 2-lens).

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
packages/contracts/src/grace2_contracts/chart_contracts.py (NEW):
- ChartEmissionPayload(BaseModel): chart_id (str, unique), vega_lite_spec (dict — the full Vega-Lite v5 JSON spec), title (str), caption (str or None), source_layer_uri (str or None), created_turn_id (str or None — stack-grouping key: same agent turn means same UI stack).
- Envelope type registration: find where envelope types are declared in contracts (search for the payload-warning / impact-envelope payload models) and register chart-emission the same way.
- Persistence shape per manifest OQ-4 TENTATIVE: charts stored as a field ARRAY on the session document in the MongoDB sessions collection (append-only; replay on Case rehydration). Document this in the module docstring + add a SessionChartRecord model (chart payload + emitted_at + session_id) for the writer to use. No new collection.
- Validator: vega_lite_spec must contain "$schema" or both "mark" and "encoding" keys (cheap structural check, not full vega validation).
- Tests packages/contracts/tests/test_chart_contracts.py: payload round-trip, structural validator accepts a real histogram spec and rejects junk, stack-grouping field present.

## File ownership
packages/contracts/src/grace2_contracts/chart_contracts.py, packages/contracts/tests/test_chart_contracts.py, contracts __init__.py (surgical — job-0222 edited it earlier in another track; re-read before editing).
