# job-0251-infra — Signed-URL minting Cloud Function — REPORT

**State:** IN_REVIEW (adversarial panel follows) · **Sprint:** 13.5 Stage 1

## What landed

A Cloud Functions gen2, HTTPS, AUTHENTICATED signed-URL minter + its Tofu deploy
surface, all within `infra/signed_urls/` + minimal root wiring (`signed_urls.tf`).

| File | Role |
|---|---|
| `infra/signed_urls/main.py` | Function source. `mint_signed_url(...)` core + `handle_request` HTTPS entry. Heavy deps (firebase_admin, google.cloud.storage, pymongo, google.auth, secretmanager) imported LAZILY + injectable via `_DEPS`. |
| `infra/signed_urls/requirements.txt` | Deployed runtime deps. |
| `infra/signed_urls/requirements-dev.txt` | Dev/test deps: pytest only (cloud SDKs omitted — tests inject fakes). |
| `infra/signed_urls/test_mint_signed_url.py` | 55 pure-Python unit tests (no GCP/Firebase install needed). |
| `infra/signed_urls.tf` | Tofu: gen2 function + dedicated runtime SA + secret/bucket/signBlob IAM + invoker gate + outputs. |

## Behavior (the security boundary)

1. Token trust: handler verifies the Firebase ID token (verify_id_token check_revoked=True);
   verified uid is authoritative. Body user_id MUST equal token uid or 403 (never trust body).
   Missing/malformed token -> 401.
2. Ownership: user_id must own case_id. Read = find_one({_id: case_id}) on `projects`
   (Case<->projects 1:1, FR-MP-5). Owner match mirrors Persistence.list_cases_for_user
   (user_id OR owner_user_id) but OMITS the pre-Auth $exists:False clause -- fail closed,
   orphan Case not mintable. Missing Case -> 404; wrong owner -> 403.
3. TTL clamp: [900, 3600]s; out-of-range clamps to nearest bound; garbage -> default 3600.
4. Mint: GCS V4 signed URL via blob.generate_signed_url(version="v4", ...).

## Persistence-seam decision (documented divergence)

Function does NOT spawn an MCP sidecar (short-lived sync handler; per-request Node
sidecar spawn/teardown would dominate latency + re-introduce the MCP-1 leak class).
Instead a direct PyMongo find_one of the owning doc, REUSING the translator's logical
contract exactly (collection `projects`, key `_id==case_id`, user-link user_id/owner_user_id).
Sole intentional difference: drop pre-Auth $exists:False (fail-closed). Matches kickoff allowance.

## Signing approach (HARD CONSTRAINT honored)

NO SA key created/downloaded. V4 signature via IAM signBlob using the ATTACHED runtime SA:
google.auth.default credentials' .signer is an iam.Signer; generate_signed_url(credentials,
service_account_email) signs through IAM Credentials API. Runtime SA holds
iam.serviceAccountTokenCreator on itself (signed_url_minter_signer_token_creator), created by
tofu apply, gcloud fallback in USER_UNBLOCK 0251-B.

## IAM posture

Dedicated signed-url-minter SA: secretAccessor on the ONE Atlas SRV secret; objectViewer
READ-ONLY on -runs/-cog/-fgb (bucket-scoped); tokenCreator on itself (signBlob). No write
role; no Gemini; no key. Invoker-gated (run.invoker to agent-runtime SA + var members) -- never allUsers.

## Evidence

- evidence/pytest.txt -- 55 passed on services/agent/.venv (no GCP SDKs; fakes via _DEPS).
- evidence/tofu_validate.txt -- "Success! The configuration is valid."
- evidence/tofu_plan.txt -- targeted plan vs live grace-2-hazard-prod: Plan: 13 to add, 0 to
  change, 0 to destroy. 9 job-0251 resources clean; the 4 extra adds are
  google_project_service.enabled[firebase|firestore|identitytoolkit|firebaserules] pulled in
  transitively from concurrent job-0250 firebase work (shared `enabled` for_each) -- not this job.

## UNBLOCK items (sprint-13-5-USER_UNBLOCK.md § job-0251)

- 0251-A: build + gsutil cp the function source zip (gen2 deploys from a GCS archive).
- 0251-B: (conditional) signBlob self-binding via gcloud if apply ADC lacks IAM perms.
- 0251-C: tofu apply (production mutation, classifier-blocked).
- 0251-D: live round-trip verify (mint 900s URL -> 200 now, 403 after TTL).

## Risks / notes for the panel

- Source object is a placeholder; function apply needs the real uploaded zip (0251-A).
  validate/plan green with placeholder (they don't read object content).
- Live-verify deferred per sprint-13-5 decisions (Gemini-free, no agent production mutation);
  expiry is GCS-native V4; the clamped TTL the function sets IS unit-tested.
- service_config[0].uri exposed as output signed_url_function_url for job-0254.
- No edits outside infra/signed_urls/, infra/signed_urls.tf, reports/. No Gemini/Vertex. git add scoped.

## Panel verdict (4-lens Opus): 2/4 — REFUTED (blocking, contract lens)
The ownership check (`user_id`/`owner_user_id` on case docs) matches the READ
filter but NOT the WRITE path: `upsert_case` persists `CaseSummary`, which has
NO user field (extra="forbid") — so against today's documents the function
403s EVERY legitimate owner. Root fix is the case-ownership FIELD itself:
contracts `CaseSummary.user_id` + write-path persistence + the job-0252
migration. Scheduled as job-0252b (after the in-flight job-0252 lands, per
the frozen-kickoff convention), then the contract lens re-runs. Also queued:
the correctness lens's OverflowError nit in clamp_ttl.

---

## REFUTED state CLEARED (orchestrator, 2026-06-11)

The blocking refute (ownership field never written) was cured by job-0252's owner stamping; the successor value-layer refute (Firebase uid vs internal ULID — panel-job-0252 contract lens) was cured by job-0251b's resolution chain. Re-panel wf_8fa82d48-ffe PASS 4/4 with pre-fix reproductions proving both cures. job-0251 → DONE per Decision 10. Live deploy verification remains gated on USER_UNBLOCK items 0251-A..D (re-zip addendum applies).
