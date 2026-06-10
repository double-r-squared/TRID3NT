# Sprint-13.5 Manifest Refresh Report

**Job:** sprint-13-5-manifest-refresh-20260610
**Date:** 2026-06-10
**Type:** coordination/planning (docs only — no code changes)
**State:** DONE

## Purpose

Refresh the sprint-13.5 manifest to reflect everything sprint-13 produced, fold in 8 new items accumulated during sprint-13 execution, and correct the PROJECT_STATE.md environment facts which had become stale (still reflecting sprint-03 "no GCP project yet" state).

## Sources read

| Source | Key finding |
|---|---|
| `reports/inflight/job-0203-agent-20260609/report.md` | M4 APPROVED (4/4 CONFIRM). MCPSurfaceTranslator stable. Three OQs carried: MCP-VERSION-PIN, OQ-0115-CASE-USER-LINK, SESSIONS_TTL retention (60d effective, user sign-off needed). |
| `reports/inflight/job-0232-infra-20260609/report.md` | Python sandbox infra READY_FOR_AUDIT. OQ-SANDBOX-1 (Atlas egress), OQ-SANDBOX-3 (Cloud Logging read path decision) surfaced for 13.5. |
| `reports/inflight/job-0233-agent-20260609/report.md` | code_exec_request envelope READY_FOR_AUDIT. OQ-SANDBOX-3 resolved v0.1 (Cloud Logging read recommended). Makefile targets added. |
| `reports/inflight/wave-4-11-close-20260609/report.md` | P5 Pelicun live acceptance deferred. Confirmed carry-over into sprint-13.5. |
| `reports/inflight/job-0240-infra-20260610/audit.md` | Cloud Build job dispatched for MODFLOW + python-sandbox image pinning; RUNNING concurrently with sprint-13 Stage 3. |
| Sprint-13 STATE scan (job-0220 through job-0236) | job-0220 through job-0235 READY_FOR_AUDIT; job-0236 RUNNING; job-0237/0238/0239 not yet dispatched. |

## Changes to sprint-13-5-manifest.md

### Structural additions

1. New section "What sprint-13 produced" — table of sprint-13 artifacts that are now locked substrate.

2. New section "New items folded in since original manifest" — documents the 8 accumulated items with their provenance.

3. New prereq job `job-0241-agent-TBD` — MCPClient PDEATHSIG + sandbox Cloud Logging result transport. Two small targeted fixes. No adversarial panel. Must land before Stage 1 and before job-0257. ~80K Sonnet.

4. New Stage 0 in the wave structure — captures concurrent sprint-13 Stage 3 acceptance + job-0240 digest pinning.

### Job scope amendments

5. job-0252 scope expanded: added pre-Auth case migration (OQ-0115-CASE-USER-LINK) as part 2. Estimate: +20K to 220K Opus.

6. job-0257 scope expanded: added MDB_MCP_READ_ONLY=false requirement (MCP-3), mongodb-mcp-server version pin + startup smoke test (MCP-2), Cloud Logging read IAM for sandbox transport (SANDBOX-1), prereq on job-0241. Estimate: +50K to 250K Opus.

7. job-0259 scope expanded: added step 8 (pre-Auth case isolation verification) and P5 Pelicun conditional step (step 9). P5 is explicitly NOT a close-blocker. Estimate: +50K to 300K Opus.

8. job-0257 adversarial panel annotated to explicitly check MDB_MCP_READ_ONLY and mongodb-mcp-server pin.

### Gating additions

9. Three new pre-Stage-1 acceptance criteria: job-0241 approved, job-0240 digest pins confirmed, user confirms SESSIONS_TTL policy (60d effective retention).

10. DECISION-GATE added to execution order for SESSIONS_TTL confirmation — hard block before Stage 1.

### Open questions

- OQ-1 (SESSIONS_TTL) added as highest-priority, marked DECISION-GATE
- OQ-7 (Atlas egress rule / OQ-SANDBOX-1) added
- OQ-8 (P5 Pelicun conditional) added
- OQ-3 (signed-URL TTL for WMS tiles) updated to reflect job-0255 proxy architecture confirms the tentative answer

### Budget

Revised total: ~4.15M (up from ~3.9M). Delta: +250K across new job-0241 (~80K), job-0252 expansion (+20K), job-0257 expansion (+50K), job-0259 expansion (+50K), adversarial panel increase (+50K on final panel).

### Deferred-to-sprint-14 additions

Added: OQ-227-PLUME-PRESET-QML, OQ-SANDBOX-2, OQ-CODE-EXEC-CATEGORY, OQ-0203-FIND-PAGINATION, tool card expand output, synthetic close-out design.

## Changes to PROJECT_STATE.md

The environment facts section had not been updated since sprint-03 (2026-06-05/06). Corrections:

| Item | Old (stale) | Corrected |
|---|---|---|
| gcloud location + auth state | "no GCP project yet; gcloud at ~/tools/google-cloud-sdk/; will be authed" | gcloud IS at /home/nate/tools/google-cloud-sdk/bin/; authed as natealmanza3@gmail.com; ADC live; active project grace-2-hazard-prod; sandboxed Bash caveat documented |
| Docker | "Docker 29.3.1 container builds ready" | Docker daemon socket NOT accessible to this user; all container builds via Cloud Build |
| npx | not mentioned | npx present |
| mf6 binary | not mentioned | mf6 6.5.0 verified runnable on host |
| mongod | not mentioned | mongod 7.0.14 at /tmp/mongod (ephemeral; used for M4 local round-trip evidence) |

## Files changed

- `reports/sprints/sprint-13-5-manifest.md` — refreshed
- `reports/PROJECT_STATE.md` — environment facts section corrected
- `reports/inflight/sprint-13-5-manifest-refresh-20260610/report.md` — this file

## No code changes

This job owns only planning/coordination documents. No files under services/, web/, packages/, or infra/ were touched.
