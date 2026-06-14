# Kickoff (frozen)

You are the engine specialist. Job job-0221-engine-20260609 — mf6-gwt solute transport adapter (sprint-13 Stage 1, adversarial-verify gated).

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

## Authoritative context
reports/inflight/sprint-13-mod-1-modflow-container-design-20260609/design.md section 2 — mf6 single binary contains GWF + GWT; the adapter assembles BOTH package sets via flopy.

## Scope
services/workers/modflow/gwt_adapter.py (NEW): build_modflow_deck(spill_location_latlon, contaminant, release_rate_kg_s, duration_days, aquifer_k_ms, porosity, workdir) — writes a complete MF6 simulation (steady-state GWF + transient GWT advection-dispersion) via flopy and returns the deck manifest. Physically meaningful minimal model: structured grid centered on the spill point (~2km x 2km, 50m cells, single layer acceptable for v0.1), constant-head boundary gradient driving flow, mass-loading source at the spill cell, dispersion package, output control for concentration arrays.
NOTE: take plain keyword args matching the MODFLOWRunArgs field names above — the Pydantic contract is being authored in parallel by job-0222 and gets bound in Stage 2 (job-0227). Do NOT import from grace2_contracts.

## Acceptance
- pip install flopy into services/agent/.venv (or a local venv under services/workers/modflow/).
- Unit tests services/workers/modflow/test_gwt_adapter.py: deck files exist + GWT source package carries the requested mass rate + grid georegistration matches spill latlon.
- [REQUIRED live evidence] download pinned mf6.5.0 static linux binary (same URL/checksum as the design doc), run the generated deck end-to-end, assert concentration field is non-zero, finite, peaked near the source, and total mass plausible vs release_rate x duration. Save run log + a concentration-summary printout to reports/inflight/<job-id>/evidence/.

## File ownership
services/workers/modflow/gwt_adapter.py, services/workers/modflow/test_gwt_adapter.py ONLY. job-0220 owns the rest of that dir in parallel — mkdir -p is fine, touch nothing else.
