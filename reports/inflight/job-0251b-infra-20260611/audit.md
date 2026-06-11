# job-0251b — mint_signed_url owner-identity resolution (Firebase uid → internal users._id ULID) + TTL clamp hardening (FROZEN KICKOFF)

**Specialist:** infra
**Sprint:** 13.5 Stage 1 (fix-pair closing the job-0251 REFUTED panel)
**Model:** Fable (critical auth-boundary fix per the standing critical-bug routing rule)
**Opened:** 2026-06-11
**Depends on:** job-0252 DONE (panel 3/4); Decision 10 in `reports/sprints/sprint-13-5-decisions.md`.

## Why this job exists (two panel verdicts, read them first)

1. **panel-job-0251 (BLOCKING refute):** `case_owned_by` read `user_id`/`owner_user_id` but at the time nothing ever wrote them. job-0252 has since landed owner stamping at both create sites + the `MIGRATION_ANON_UID` migration — the FIELD now exists.
2. **panel-job-0252 contract lens (major refute, the live one):** the VALUE is still wrong. Case docs store `user_id` = the **internal `users._id` ULID** (minted by `auth_handshake._resolve_or_provision_user` → `new_ulid()`; the Firebase uid lives separately in `users.firebase_uid`). `infra/signed_urls/main.py` compares `case_doc.user_id == verified_uid` where `verified_uid = claims.get("uid") or claims.get("sub")` — the **Firebase token uid**. These never match → in production every legitimate owner's mint returns 403. Reproduced end-to-end by the panel.

**Binding decision (Decision 10, sprint-13-5-decisions.md):** the canonical owner identity is the INTERNAL `users._id` ULID everywhere (SRS H.2:42 / H.5:124). The mint function must RESOLVE the verified Firebase uid to the internal ULID before the ownership check. Do NOT change the agent-side write path — it is the spec-correct side of the seam (panel-verified self-consistent and leak-free).

## Scope (file ownership: `infra/signed_urls/` ONLY)

### 1. Owner-identity resolution in `mint_signed_url`
- After token verification (`main.py:448` area), resolve `verified_uid` → internal user ULID via a **users-collection lookup**: filter `{"firebase_uid": verified_uid}` on collection `users`, internal id = the doc's `_id` (mirror the normalization in `services/agent/src/grace2_agent/persistence.py::get_user_by_firebase_uid` — `_id` is authoritative, `user_id` key fallback). Reuse the function's existing Mongo access path (the same client/seam it already uses for the case-doc fetch).
- **Fail-closed:** no users doc for the verified uid → 403 Forbidden (a Firebase user who has never connected to the agent owns nothing). Lookup error → 503/fail-closed, never fall through to a raw-uid comparison.
- `case_owned_by(case_doc, resolved_internal_id)` — comparison semantics (`user_id` OR `owner_user_id`) unchanged.
- **Body contract stays "never trust the body":** `body.user_id` continues to carry the Firebase uid and MUST equal `verified_uid` (existing check at `main.py:390`). Resolution is internal. Update the module docstring (lines 14-15, 34-38) and the SRS-reference comments so the documented chain reads: token uid == body.user_id → resolve to internal ULID → ownership check.
- `MIGRATION_ANON_UID`-owned cases are NOT mintable by any Firebase user — by design (pre-auth orphans). Assert it in a test.

### 2. `clamp_ttl` OverflowError nit (`main.py:254-269`)
`int(float("inf"))` / overlarge floats raise `OverflowError`, which the `(TypeError, ValueError)` catch misses → 500 instead of the documented fall-back-to-default. Add `OverflowError` to the catch. Test with `float("inf")`, `float("nan")` (ValueError — confirm covered), `1e400`.

### 3. Tests (extend the existing 55-test suite)
- Owner mints: case doc `user_id=<internal ULID>`, users doc `{_id: <ULID>, firebase_uid: <uid>}`, token uid == body.user_id == `<uid>` → 200 signed URL.
- Firebase uid with NO users doc → 403.
- Resolved user does not own the case → 403.
- A second user's firebase_uid resolving to a different ULID cannot mint the first user's case → 403.
- `MIGRATION_ANON_UID`-owned case unmintable via any token → 403.
- Users-collection lookup failure → fail-closed (no mint).
- clamp_ttl overflow inputs → DEFAULT_TTL_SECONDS.
- `tofu validate` stays green (no resource changes expected; if main.py needs an env/connection var for the users lookup it already has the Mongo seam — do NOT add new infra resources without flagging in the report).

## Hard constraints
- NO Gemini/Vertex calls. NO agent restart. NO changes outside `infra/signed_urls/` (the agent side is correct; if you believe it isn't, STOP and write the finding in your report instead of editing).
- No service-account keys, ever (keyless signBlob stands).
- Deployment remains a USER step (`reports/inflight/sprint-13-5-USER_UNBLOCK.md` items 0251-A..D stand; update them if the function's env/inputs changed).
- `git add` only files you touched; commit `job-0251b: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
`reports/inflight/job-0251b-infra-20260611/{report.md,STATE=IN_REVIEW}`; full signed_urls pytest suite green; tofu validate green; report names every changed contract line. A 4-lens adversarial re-panel (run by the orchestrator) gates DONE and clears job-0251's REFUTED state — write the report so a hostile reviewer can re-run everything.
