# Sprint 03: Foundation (SRS v0.3 M1)

**Status:** complete
**Opened:** 2026-06-05
**Closed:** 2026-06-05
**SRS milestones covered:** M1 (Foundation), plus repo realignment from the v0.2→v0.3 pivot

## Goal

At the end of this sprint: the repo is a git repository (MIT license, v0.3 layout, v0.2 artifacts deleted); the SRS Appendix A–D contracts exist as an installable pydantic-v2 package with round-trip tests; a fresh GCP project (Terraform-captured) and a MongoDB Atlas M0 cluster exist with the MongoDB MCP server connection verified; an ADK agent on Gemini 3 streams real replies over the Appendix-A WebSocket core locally; and a browser shows a CONUS MapLibre map whose chat box round-trips with that agent — all verified by `make test` and an acceptance record.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|
| job-0012-infra-20260605 | infra | Repo realignment, git init + MIT license, v0.3 layout | — | approved |
| job-0013-schema-20260605 | schema | Contracts v0 from Appendices A–D (pydantic v2) | — | approved |
| job-0014-infra-20260605 | infra | GCP project + Atlas Flex import + OpenTofu IaC | 0012 | approved |
| job-0015-agent-20260605 | agent | ADK hello-world Gemini + Appendix-A WS core + MCP | 0013, 0014 | approved |
| job-0016-web-20260605 | web | Web stub: CONUS MapLibre map + chat round-trip | 0013, 0015 | approved |
| job-0017-testing-20260605 | testing | M1 acceptance: protocol/contract tests + record | 0015, 0016 | approved |

## Execution order

```
stage A (parallel):  job-0012 (repo)        job-0013 (contracts)
stage B:             job-0014 (GCP+Atlas — BLOCKS at user auth checkpoints: `! gcloud auth login`, `! atlas auth login`)
stage C:             job-0015 (agent)
stage D:             job-0016 (web stub)
stage E:             job-0017 (acceptance)
```

Each gate is an in-workflow adversarial review per AGENTS.md. One revision round per job; second failure blocks the job and its dependents. job-0014's user-auth blocks are expected, not failures — the orchestrator resumes after the user authenticates.

## Exit criteria

1. v0.2 artifacts gone; v0.3 layout in place; `git log` shows the initial commit; MIT `LICENSE` at root (job-0012)
2. `packages/contracts` installs in a fresh venv; round-trip tests pass for every Appendix A message type + envelope + claims; the `research_mode` Appendix-A amendment diff and OQ-7 are in the report (job-0013)
3. GCP project exists with the five APIs enabled and `terraform plan` clean; Atlas M0 reachable; MongoDB MCP server round-trip transcript (job-0014)
4. `make run-agent` streams a real Gemini 3 reply over Appendix-A frames locally; `cancel` mid-stream yields cancelled `pipeline-state`; MCP call from the agent verified (job-0015)
5. Browser: CONUS OSM map + chat box streams a live reply; agent-death → disconnected indicator, reconnect works — screenshot + transcript (job-0016)
6. `make test` green: protocol conformance, negative controls, contract suite; acceptance table completed (job-0017)

## Exit criteria — verification

| # | Criterion | Status | Evidence (cited by job ID) |
|---|---|---|---|
| 1 | v0.2 artifacts gone; v0.3 layout; git log shows initial commit; MIT LICENSE at root | **pass** | job-0012 audit; root commit `6fd37e6`; re-verified live in job-0017 |
| 2 | `packages/contracts` installs in fresh venv; round-trip tests pass for every Appendix-A message + envelope + claims; `research_mode` amendment diff + OQ-7 in report | **pass** | job-0013 audit (91/91 tests, 35 schemas, A1–A5 proposals, OQ-7 = 768); re-verified live in job-0017 |
| 3 | GCP project with 5 APIs enabled and `tofu plan` clean; Atlas Flex reachable; MongoDB MCP round-trip transcript | **pass** | job-0014 audit (`grace-2-hazard-prod` 425352658356, 12 APIs, `tofu plan: No changes`); re-verified in job-0017 (MCP smoke 17.66s) |
| 4 | `make run-agent` streams a real Gemini-3 reply; cancel mid-stream → cancelled `pipeline-state`; MCP call verified | **qualified** | job-0015 audit (502 ms cancel; live MCP); QUALIFIED on Gemini-3-vs-2.5-pro substitution — gemini-3-pro\* returns 404 on Vertex 2026-06-05. Documented in job-0015 OQ-A-1; SRS FR-AS-1 amendment proposal pending user landing |
| 5 | Browser: CONUS OSM map + chat box streams a live reply; agent-death → disconnected indicator, reconnect works | **pass** | job-0016 audit (7 headless screenshots Chromium + Firefox; ~4 s reconnect; CDP transcripts) |
| 6 | `make test` green: protocol conformance + negative controls + contract suite + acceptance table | **pass** | job-0017 audit (114 tests passed: 91 contracts + 23 acceptance in ~36 s) |

**Verdict: M1 achieved (5 pass + 1 qualified).** The single qualification is the documented Gemini-3-on-Vertex 404 substitution; SRS amendment proposal pending user landing — does not change the substantive deliverable (the agent runs end-to-end against the latest available Vertex Gemini and is single-constant-flip-away from Gemini-3 when Vertex GAs it).

## Retrospective

**What shipped (the bones of M1):**
- Repo is git-initialized on GitHub (`double-r-squared/GRACE-2`), MIT-licensed, v0.3 layout with seven owned directories, root commit `6fd37e6` chained through HEAD.
- `grace2-contracts` v0.1.0 installable pydantic-v2 package — 10 modules covering Appendices A–D + FR-PHC-2 + FR-TA-2; 91 round-trip + negative-control tests; 35 idempotent JSON schemas. Five Appendix-A amendments proposed (A1–A5) ready for user landing.
- Live GCP substrate billed and verified: `grace-2-hazard-prod` (425352658356) + 12 APIs + GCS-backed OpenTofu state + agent-runtime SA + GCS artifact bucket + Secret Manager SRV.
- Live MongoDB Atlas Flex cluster `grace-2-dev` imported into OpenTofu state; `tofu plan` clean; programmatic API-key issuance + revoke ritual documented.
- Working agent: ADK + Vertex AI `gemini-2.5-pro` + Appendix-A WebSocket core via `grace2_contracts.ws` (zero hand-rolled JSON) + MCP stdio sidecar (SRV from Secret Manager via ADC) + cancellation chain verified live at 502 ms vs 30 s NFR-R-3 budget.
- Working web stub: React 18 + Vite 5 + TS strict + MapLibre 4.7 CONUS map (Decision I camera lock) + chat box streaming `agent-message-chunk` deltas; disconnect→reconnect in ~4 s; cross-browser Chromium + Firefox-ESR Linux screenshots.
- Pytest harness running 91 contracts + 23 acceptance tests in ~36 s, exit 0; protocol conformance + negative controls + cancellation + research_mode + MCP smoke + latency p50/p95; `live_gemini` + `live_atlas` markers gating live-cloud tests.

**Decisions that landed (orchestrator carried, user approved at the substrate level):**
- Atlas tier: Flex (not M0) — pre-MVP through M-9; M10-replica-set at M10 milestone. Supersedes prior M0 choice.
- Linux (Debian 13) is both dev AND prod substrate; container builds `linux/amd64`-only. Project-wide invariant.
- OpenTofu (not Terraform) for IaC; GCS-backed state from day one (bootstrap chicken-and-egg documented).
- OQ-2 MCP hosting: Cloud Run sidecar (lowest latency, stdio proven, single auth surface).
- OQ-7 embedding dimension: 768 (matches `text-embedding-005` native; preserves recall headroom). Validation gate qualified at smoke scale; real-corpus revalidation tied to M7 news pipeline.
- OQ-1 agent deployment target: Cloud Run + WebSocket (`--use-http2 --session-affinity --min-instances=1`) — recommendation surfaced for user confirmation before M2.
- Repo layout promoted from tentative → fact: `web/` · `services/{agent,workers}/` · `packages/contracts/` · `infra/` · `styles/` · `tests/` + root scaffold.
- Pelicun + OpenTelemac landed in SRS as forward-looking architectural slots (v0.3.13, v0.3.14) at user direction — no implementation, but the architecture accommodates them so we are not bolting them on later.

**What worked:**
- The orchestrator loop (sprint plan → specialist runner workflow → adversarial in-workflow reviewer → revision round if needed → orchestrator audit at closure) executed five jobs end-to-end with the user only intervening at strategic decision points (Atlas tier choice, Pelicun + OpenTelemac SRS amendments, gitignore audit decision). Per-job check-ins were not needed.
- Adversarial in-workflow review caught real issues every job: `research_mode` field gaps, missing 7-day allowlist expiry, latency-label misleading, missing report population. None of these would have been caught by self-review.
- The structured-output schema discipline kept specialist returns machine-parsable; the two harness failures (job-0013 + job-0017 first runs missing StructuredOutput call → blank report.md) were diagnosable and recoverable via focused closeout workflows.
- The 10-architectural-invariant walk per audit caught Tier-separation seeding (job-0012), cancellation chain (job-0015), Decision-M intensity-as-ClaimSet (job-0013) — invariants would have eroded silently without it.

**What surprised:**
- Gemini-3 still returns 404 on Vertex AI 2026-06-05. SRS-named Gemini-3 substituted with single-constant flip path; SRS amendment proposal pending.
- Atlas Flex Vector Search is "testing only" per MongoDB docs — workable for pre-MVP but real production load requires M10+ before M10 milestone.
- Debian 13 has no `python3-venv` apt-installed by default — both job-0013 and job-0015 hit it; `virtualenv` is the working substitute.
- The job-0014 specialist created the Atlas cluster out-of-band via UI before this session (user did it directly). `tofu import` recovered cleanly but a future session would have been smoother with IaC-from-scratch.
- The first version of every "tough" specialist run (0013 contracts, 0017 testing) had its agent skip the StructuredOutput call at the end despite explicit prompt instructions. A focused "closeout workflow" pattern recovered both cleanly without redoing the work.

**Decisions surfaced to user for landing at this sprint close** (all itemized in the M1 sign-off package below):
- A1–A5 SRS Appendix amendment proposals from job-0013 (research_mode field, event_type→intensity mapping, v0.2+ subtype payload typing, RunDocument.assessment storage-layer note, A.6 cancel-error codes)
- NFR-C-1 cost-line correction (~$170/mo M10 actual, not <$100)
- NFR-P-1 first-token budget reality (3–8s warm vs 2s spec) — amendment candidate
- FR-AS-1 Gemini-3 substitution clause ("Gemini 3 when available, latest stable otherwise")
- OQ-1 ratification: Cloud Run + WebSocket
- Gitignore identifier-exposure decision (Lever A sanitize / B rotate / C history purge) — surfaced separately

**Carry-forward into the next sprint (sprint-04, M2 Foundation: QGIS Server in cloud + PyQGIS worker prototype):**
- OQ-T-1 real-Gemini latency p50/p95 follow-up under `live_gemini` marker
- OQ-T-3 agent `server.py` `stream_reply` rebind cleanup
- OQ-T-4 Playwright fixture for in-job web E2E
- OQ-W-3 Chromium provisioning (apt vs Playwright vs dev-container)
- Real-corpus OQ-7 revalidation when news pipeline (M7) ships
- CI plumbing when GitHub Actions wiring lands
- Cloud Run egress IP reservation (replaces dev IP allowlist) when first Cloud Run service deploys
- Conda env recreation when first PyQGIS worker code job opens

**Sprint discipline notes (carry to AGENTS.md / orchestrator memory if recurring):**
- Specialist agents need their StructuredOutput call hammered home in the prompt — adding "CRITICAL: …" emphasis worked for jobs 0014, 0015, 0016 but missed for 0013 and 0017. Consider a workflow-runtime check that flags a missing call before terminating.
- Kickoffs revised after their initial creation (job-0014 Linux/Flex rewrite, downstream Linux/browser-matrix tweaks) are in-bounds *only before handoff*. The revision discipline held.
- The user's "you define sprints, I sign off at close" loop fits the AGENTS.md state machine cleanly — five jobs through the loop with zero unsolicited per-job questions to user.
