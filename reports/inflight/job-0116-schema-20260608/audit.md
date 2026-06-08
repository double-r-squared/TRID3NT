# Audit: Auth/Users appendix schema (Firebase Auth + Identity Platform)

**Job ID:** job-0116-schema-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** schema

**Required reads:**
- Memory: `feedback_mongodb_mcp_canonical_persistence.md`
- `packages/contracts/src/grace2_contracts/case.py` (job-0099 — Case schema)
- Firebase Auth + GCP Identity Platform docs (WebSearch as needed)

### Scope

Author SRS Appendix amendment for Auth/Users as a DECOUPLED appendix per user direction 2026-06-08 ("decoupled enough to be an appendix").

NEW SRS file: `docs/srs/H-auth-and-users.md` (next-letter appendix; verify nothing already at H):

Sections:
- **H.1 Identity provider choice** — Firebase Authentication (managed SaaS, GCP-native, supports email/OAuth/anonymous, scales to Identity Platform enterprise SKU for SLA + customer-managed keys without contract change). Rationale: GCP-native, no separate identity vendor, decoupled from compute, custom-claims model for case-ownership scoping.
- **H.2 User → Case ownership** — every Case carries `owner_user_id` (ULID of User). MongoDB MCP enforces ownership filters in `list_cases_for_user`. Cases deletable by owner OR shared via explicit `case_collaborators[]` (defer — v0.1 is single-owner).
- **H.3 Anonymous → authenticated upgrade** — Firebase supports anonymous → linkWithCredential flow. v0.1 UX: allow anonymous Cases, prompt to upgrade on first-attempt save/share. Memory rule: user is informed before persistent action.
- **H.4 Custom claims for tier** — Identity Platform supports custom JWT claims; use for tier gating (free / pro / enterprise). v0.1: all users free-tier.
- **H.5 Session validation** — agent's WebSocket connect verifies Firebase ID token via `firebase_admin.auth.verify_id_token`. Resolution: firebase_uid → MongoDB `users._id` via Persistence.get_user_by_firebase_uid (job-0115).
- **H.6 Secrets scoping** — `SecretRecord.user_id` + `SecretRecord.case_id`; null case_id = user-scope cross-Case. Tier-2 API keys (eBird, IUCN, Movebank) live here.
- **H.7 Decision E** — record the decision: Firebase Authentication over alternatives (Auth0, Cognito-via-AWS-bridge, custom OIDC). Single-paragraph rationale.

ALSO update `docs/srs/INDEX.md` to list Appendix H.

**Tests**: this is an SRS amendment — no code tests required. Acceptance is documentation completeness.

### File ownership (exclusive)

- `docs/srs/H-auth-and-users.md` (NEW)
- `docs/srs/INDEX.md` — append H to TOC
- `reports/inflight/job-0116-schema-20260608/`

### FROZEN

- All other docs/srs/*.md
- `docs/SRS_v0.3.md` monolith (regenerated only; surface OQ-116-SRS-MAKE-RERUN if can't `make srs`)
- All code paths (Auth implementation lands in Wave 2 jobs)
- `reports/complete/**`

### Acceptance

- [ ] Appendix H landed; 7 sections per scope
- [ ] INDEX.md updated
- [ ] No conflict with existing appendices A-G
- [ ] Decision E documented
- [ ] Single commit prefix `job-0116:`; co-author line


### FROZEN

All other `tools/*` (each Wave 1.5 sibling owns one); all `workflows/`, `services/workers/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`. For schema/agent jobs, FROZEN is the inverse of their declared file ownership.

### Concurrency note (Wave 1.5 fan-out — 16 parallel)

~16 Wave 1.5 jobs in parallel. Idempotent-append works for `tools/__init__.py` + `main.py` + `packages/contracts/__init__.py` but Wave 1 produced 3 commit-label-swap patterns under load. **Required mitigation**: before `git commit`, run `git pull --rebase=true origin main 2>/dev/null || git stash && git pull --rebase && git stash pop` to handle sibling concurrent landings cleanly. If conflict on registration site, re-apply your import line.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: if your tool emits geometry, verify against actual geography (river mouth where it should be, not just bbox/URL consistency). Every fetcher's live test must check that emitted features fall inside requested bbox AND match the named place's actual outline if applicable.

2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.

### Acceptance criteria

- [ ] New tool/contract registered + visible at appropriate test surface
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness check where applicable
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

