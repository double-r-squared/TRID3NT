# Kickoff (frozen)

You are the engine specialist. Job job-0226-engine-20260609 — fetch_mrms_qpe MRMS accumulated-precip fetcher (sprint-13 Stage 1).

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

## Scope (repo-convention path)
services/agent/src/grace2_agent/tools/fetch_mrms_qpe.py (NEW): fetch_mrms_qpe(bbox, accumulation="24h") with accumulation in 1h|6h|24h|72h. Source: NOAA MRMS public mirror (s3://noaa-mrms-pds — MultiSensor_QPE Pass2 products; anonymous access via the same fsspec/boto pattern other NOAA fetchers in this repo use — read fetch_era5_reanalysis.py / data_fetch.py for conventions). GRIB2 -> clip to bbox -> COG (EPSG:4326) -> cache shim -> returns LayerURI. Declare estimate_payload_mb. supports_global_query=False. Pick the most recent available timestamp at call time; record it in the LayerURI metadata/provenance.
Registration: tools/__init__.py + catalog.py + categories.py PRIMARY_CATEGORY (weather/precip category — match where other precip fetchers live).
Tests services/agent/tests/test_fetch_mrms_qpe.py: product-key construction per accumulation option, GRIB2-to-COG conversion on a tiny synthetic GRIB2 (or mocked dataset), bbox clip, registration presence. Network-touching paths mocked; if a quick live anonymous S3 HEAD against noaa-mrms-pds succeeds from this machine, capture one real product listing as evidence (optional, do not block on it).

## File ownership
tools/fetch_mrms_qpe.py, tests/test_fetch_mrms_qpe.py, registration lines (NOTE: job-0224 just edited the same registration files in this track before you — re-read them first).
